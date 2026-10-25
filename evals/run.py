"""python -m evals.run --output evals/results/offline.json [--live]."""

import argparse
import asyncio
import hashlib
import json
import logging
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from evals.metrics import operational, quality, score
from evals.schema import Case
from reviewer import config
from reviewer.reliability.harness import ReviewBudget
from reviewer.runner import run_review_diff

DATA = Path(__file__).parent / "data"
VARIANTS = {
    "A": {
        "only": ["correctness"],
        "context_tools": False,
        "options": {"judge_enabled": False, "single_agent_baseline": True},
    },
    "B": {
        "only": ["security", "correctness", "quality", "test_mapping", "docs"],
        "context_tools": False,
        "options": {"judge_enabled": False},
    },
    "C": {
        "only": ["security", "correctness", "quality", "test_mapping", "docs"],
        "context_tools": False,
        "options": {"judge_enabled": True},
    },
    "D": {
        "only": None,
        "context_tools": True,
        "options": {"judge_enabled": True, "planner_mode": "rules"},
    },
}


def load_corpus(data=DATA):
    raw = (data / "cases.jsonl").read_bytes()
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        raise ValueError("dataset SHA mismatch; freeze a new version before evaluation")
    cases = [Case.model_validate_json(line) for line in raw.splitlines() if line.strip()]
    if len(cases) != manifest["cases"] or len({c.id for c in cases}) != len(cases):
        raise ValueError("case count/identity mismatch")
    for split in ("dev", "test"):
        if sum(c.split == split for c in cases) != manifest[split]:
            raise ValueError("fixed split mismatch")
    return cases, manifest


async def run_case(case, variant, live, budget, prices=None):
    # This projection is the only data crossing into the reviewer checkout.
    payload = case.reviewer_input()
    with tempfile.TemporaryDirectory(prefix="review-eval-") as directory:
        root = Path(directory) / "repo"
        root.mkdir()
        for relative, content in payload["repo_files"].items():
            path = root / relative  # validated by Case.safe_relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        patch = Path(directory) / "input.diff"  # outside reviewer tool root
        patch.write_text(payload["diff"], encoding="utf-8", newline="\n")
        selected = VARIANTS[variant]
        options = {
            **selected["options"],
            "budget": budget,
            "price_per_million_tokens": prices or {},
        }
        return await run_review_diff(
            patch,
            root,
            only=selected["only"],
            live=live,
            context_tools=selected["context_tools"],
            options=options,
        )


def summarize(records, cases, *, live, manifest, matches=None):
    by_id = {case.id: case for case in cases}
    approved = bool(
        live
        and matches
        and matches.get("dataset_sha256") == manifest["sha256"]
        and matches.get("gold_confirmed") is True
        and matches.get("reviewed_by")
    )
    results = {}
    for variant in VARIANTS:
        group = [r for r in records if r["variant"] == variant]
        reviewed = approved and all(r["key"] in matches.get("runs", {}) for r in group)
        counts = []
        if reviewed:
            for record in group:
                counts.append(
                    score(
                        record["report"].get("findings", []),
                        [g.model_dump() for g in by_id[record["case_id"]].gold_labels],
                        matches["runs"][record["key"]],
                    )
                )
        # Failure runs stay in the denominator. Absent predictions are missed gold.
        results[variant] = {
            **operational(group),
            "quality": quality(counts, len(group)) if reviewed else None,
            "quality_status": "human_reviewed"
            if reviewed
            else "not_live_benchmark"
            if not live
            else "human_matching_pending",
        }
    repeats = {}
    for variant in VARIANTS:
        group = [r for r in records if r["variant"] == variant]
        per_case = []
        for case_id in {r["case_id"] for r in group}:
            samples = [r for r in group if r["case_id"] == case_id]
            if len(samples) < 3:
                continue
            signatures = {
                json.dumps(
                    sorted(
                        (
                            f["file_path"],
                            f.get("line_start") or 0,
                            f["title"],
                            f.get("verification_status", "candidate"),
                        )
                        for f in r["report"].get("findings", [])
                    )
                )
                for r in samples
            }
            per_case.append(
                {
                    "case_id": case_id,
                    "runs": len(samples),
                    "exact_finding_set_consistent": len(signatures) == 1,
                    "latency_variance": statistics.pvariance(r["elapsed_seconds"] for r in samples),
                }
            )
        repeats[variant] = sorted(per_case, key=lambda x: x["case_id"])
    return results, repeats


async def evaluate(
    *,
    output,
    live=False,
    split="test",
    limit=None,
    repeats=1,
    matches_path=None,
    budget=None,
    prices=None,
):
    output = Path(output)
    if output.exists():
        raise ValueError("output exists; use a new filename to retain previous runs")
    cases, manifest = load_corpus()
    cases = [c for c in cases if split == "all" or c.split == split][:limit]
    budget = ReviewBudget.model_validate(budget or {}).model_dump()
    if live and config.llm_provider() != "qwen":
        raise ValueError("live A/B/C/D requires the same configured Qwen model")
    metadata = {
        "status": "live_benchmark_pending_human_matching" if live else "not_live_benchmark",
        "started_at": datetime.now(UTC).isoformat(),
        "dataset": manifest,
        "model": config.llm_model() if live else "MockLLM",
        "provider": "qwen" if live else "mock",
        "temperature": 0,
        "prompt_version": "review-v2.0",
        "review_version": "2.0.0",
        "budget": budget,
        "prices_per_million_tokens": prices,
        "split": split,
        "repeats": repeats,
        "variants": VARIANTS,
        "baseline_limit": "A shares the adapter/runner with one general reviewer; "
        "no static tools. Not an exact upstream replay.",
    }
    records = []
    output.parent.mkdir(parents=True, exist_ok=True)
    # Persist each completed run, including failures, so interruption loses no finished cases.
    with output.with_suffix(".runs.jsonl").open("x", encoding="utf-8") as stream:
        for repeat in range(1, repeats + 1):
            for case in cases:
                for variant in VARIANTS:
                    started = time.perf_counter()
                    try:
                        report = await run_case(case, variant, live, budget, prices)
                    except Exception as exc:
                        report = {
                            "status": "failed",
                            "findings": [],
                            "agents_failed": [{"agent": "pipeline", "error": type(exc).__name__}],
                        }
                    record = {
                        "key": f"{case.id}/{variant}/{repeat}",
                        "case_id": case.id,
                        "variant": variant,
                        "repeat": repeat,
                        "report": report,
                        "elapsed_seconds": time.perf_counter() - started,
                    }
                    records.append(record)
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
    matches = json.loads(Path(matches_path).read_text(encoding="utf-8")) if matches_path else None
    metrics, consistency = summarize(records, cases, live=live, manifest=manifest, matches=matches)
    result = {
        "metadata": metadata,
        "completed_at": datetime.now(UTC).isoformat(),
        "metrics": metrics,
        "three_run_consistency": consistency,
        "records": records,
        "conclusions": "No accuracy or real-model efficiency claim from mock runs. "
        "Live comparisons require human-approved gold and semantic matches, including failures.",
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--split", choices=["dev", "test", "all"], default="test")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeats", type=int, choices=range(1, 11), default=1)
    parser.add_argument("--matches", type=Path)
    parser.add_argument("--budget", type=Path, help="JSON ReviewBudget, identical for all variants")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("limit must be positive")
    logging.basicConfig(level=logging.WARNING)
    result = asyncio.run(
        evaluate(
            output=args.output,
            live=args.live,
            split=args.split,
            limit=args.limit,
            repeats=args.repeats,
            matches_path=args.matches,
            budget=json.loads(args.budget.read_text()) if args.budget else None,
        )
    )
    print(json.dumps({"status": result["metadata"]["status"], "runs": len(result["records"])}))


if __name__ == "__main__":
    main()
