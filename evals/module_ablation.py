"""Frozen multi-file corpus and paired module ablations; no gold enters review input."""

import argparse
import asyncio
import copy
import difflib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from evals.run import VARIANTS, load_corpus, run_case
from evals.schema import Case
from reviewer import config
from reviewer.reliability.harness import ReviewBudget

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "experiments/minishop-modules-v2"
SOURCE = PROJECT / "experiments/minishop-v1"
GROUPS = copy.deepcopy(VARIANTS)
GROUPS.update({
    "D_no_judge": {**copy.deepcopy(VARIANTS["D"]),
                   "options": {"judge_enabled": False, "planner_mode": "rules"}},
    "D_no_evidence": {**copy.deepcopy(VARIANTS["D"]), "context_tools": False},
    "D_no_routing": {**copy.deepcopy(VARIANTS["D"]), "only": VARIANTS["C"]["only"]},
    "D_no_static": {**copy.deepcopy(VARIANTS["D"]),
                    "options": {**VARIANTS["D"]["options"], "static_tools_enabled": False}},
})
COMBINATIONS = {
    "atomic_coupon": ("inventory_partial_reserve", "dev_coupon_minimum",
                      "clean_orders_named_quantities"),
    "cart_shipping": ("cart_overwrite", "pricing_shipping_equal", "clean_restock_named_total"),
    "order_catalog": ("orders_live_staging", "catalog_inactive", "clean_pricing_named_net"),
    "snapshot_quantity": ("inventory_snapshot_alias", "pricing_quantity", "clean_cart_setdefault"),
    "clean_checkout": ("clean_cart_setdefault", "clean_pricing_named_net",
                       "clean_orders_named_quantities"),
    "clean_catalog_stock": ("clean_catalog_guard", "clean_restock_named_total",
                            "clean_cart_total_feature"),
}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def baseline():
    return {p.relative_to(SOURCE / "base").as_posix(): p.read_text(encoding="utf-8")
            for p in (SOURCE / "base").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def construct():
    original, source_manifest = load_corpus(SOURCE / "data")
    by_id = {c.id: c for c in original}
    probes = json.loads((SOURCE / "data/probes.json").read_text(encoding="utf-8"))
    base = baseline()
    cases, checks = [], {}
    for name, components in COMBINATIONS.items():
        files, gold, changed = dict(base), [], set()
        for component in components:
            case = by_id[component]
            for path, content in case.repo_files.items():
                if base.get(path) != content:
                    if path in changed:
                        raise ValueError(f"Overlapping component: {name}/{path}")
                    changed.add(path)
                    files[path] = content
            gold.extend(g.model_dump() for g in case.gold_labels)
        patch = "".join(
            f"diff --git a/{p} b/{p}\n" + ("new file mode 100644\n" if p not in base else "")
            + "".join(difflib.unified_diff(
                base.get(p, "").splitlines(keepends=True), files[p].splitlines(keepends=True),
                fromfile=f"a/{p}" if p in base else "/dev/null", tofile=f"b/{p}"))
            for p in sorted(changed)
        )
        cases.append(Case(
            id=name, split="test", source=f"{source_manifest['repo_url']} paired composition",
            annotation_status="curated_pending_human_signoff",
            reviewed_by="Codex simulated annotation; not independent human review",
            review_note=" + ".join(by_id[c].review_note for c in components),
            repo_files=files, diff=patch, gold_labels=gold,
        ))
        checks[name] = {c: probes[c] for c in components}
    return cases, checks, source_manifest


def build():
    cases, checks, source_manifest = construct()
    data = ROOT / "data"
    raw = "".join(c.model_dump_json() + "\n" for c in cases).encode()
    path = data / "cases.jsonl"
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Frozen dataset differs; create a new version")
    data.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    write_json(data / "manifest.json", {
        "version": "minishop-modules-v2", "cases": len(cases), "test": len(cases), "dev": 0,
        "sha256": hashlib.sha256(raw).hexdigest(), "source": source_manifest,
        "human_signed_off": False, "label_method": "AI simulated, executable oracle checked",
        "scope": "4 dual-defect changes + 2 clean changes; recombined known mutations",
    })
    write_json(data / "probes.json", checks)
    write_json(data / "labels_simulated.json", [
        c.model_dump(exclude={"repo_files", "diff"}) for c in cases])
    for case in cases:
        folder = ROOT / "examples" / case.id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "change.diff").write_text(case.diff, encoding="utf-8", newline="\n")
        for name, content in case.repo_files.items():
            path = folder / "repo-after" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    print(f"Frozen {len(cases)} cases, {sum(len(c.gold_labels) for c in cases)} gold defects")


def validate():
    cases, manifest = load_corpus(ROOT / "data")
    probes = json.loads((ROOT / "data/probes.json").read_text(encoding="utf-8"))
    records = []
    for case in cases:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, content in baseline().items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="\n")
            def execute(code, root=root):
                result = subprocess.run([sys.executable, "-c", code], cwd=root,
                                        capture_output=True, timeout=30)
                return result.returncode
            for component, probe in probes[case.id].items():
                if execute(probe["code"]) != 0:
                    raise ValueError(f"Baseline oracle failed: {component}")
            patch = ROOT / "examples" / case.id / "change.diff"
            subprocess.run(["git", "apply", "--whitespace=nowarn", str(patch)],
                           cwd=root, capture_output=True, check=True)
            for name, content in case.repo_files.items():
                if (root / name).read_text(encoding="utf-8") != content:
                    raise ValueError(f"Patch mismatch: {case.id}/{name}")
            results = {}
            for component, probe in probes[case.id].items():
                passed = execute(probe["code"]) == 0
                if passed != probe["candidate_should_pass"]:
                    raise ValueError(f"Mutation oracle mismatch: {case.id}/{component}")
                results[component] = {"baseline_pass": True, "candidate_pass": passed}
            suite = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                                   cwd=root, capture_output=True, timeout=30)
            if not case.gold_labels and suite.returncode:
                raise ValueError(f"Clean suite failed: {case.id}")
            records.append({"case": case.id, "probes": results,
                            "suite_pass": suite.returncode == 0})
    write_json(ROOT / "validation.json", {"dataset_sha256": manifest["sha256"],
                                          "records": records, "passed": True})
    print(f"Validated {len(records)} patches and all component oracles")


async def run(output, live, variants, case_ids):
    from dotenv import load_dotenv
    load_dotenv(PROJECT / ".env", override=False)
    # Fixed model for paired comparisons. Do not modify the user's .env.
    os.environ["DASHSCOPE_FALLBACK_MODELS"] = ""
    cases, manifest = load_corpus(ROOT / "data")
    if case_ids:
        if set(case_ids) - {c.id for c in cases}:
            raise ValueError("Unknown case")
        cases = [c for c in cases if c.id in case_ids]
    validation = json.loads((ROOT / "validation.json").read_text(encoding="utf-8"))
    if not validation["passed"] or validation["dataset_sha256"] != manifest["sha256"]:
        raise ValueError("Label validation required")
    output.mkdir(parents=True, exist_ok=False)
    budget = ReviewBudget().model_dump()
    metadata = {
        "started_at": datetime.now(UTC).isoformat(), "live": live, "dataset": manifest,
        "model": config.llm_model() if live else "MockLLM", "budget": budget,
        "variants": {v: GROUPS[v] for v in variants}, "cases": [c.id for c in cases],
        "repeats": 1, "human_signed_off": False, "fallback_enabled": False,
        "order": "sequential, rotate variant order by case index",
        "source_sha256": {
            p.relative_to(PROJECT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for d in ("reviewer", "evals") for p in (PROJECT / d).rglob("*.py")},
    }
    write_json(output / "metadata.json", metadata)
    with (output / "runs.jsonl").open("x", encoding="utf-8", buffering=1) as stream:
        for n, case in enumerate(cases):
            shift = n % len(variants)
            for variant in variants[shift:] + variants[:shift]:
                start = time.perf_counter()
                try:
                    report = await run_case(case, variant, live, budget, variants=GROUPS)
                except Exception as exc:
                    report = {"status": "failed", "findings": [],
                              "agents_failed": [{"agent": "pipeline", "error": type(exc).__name__}]}
                record = {"key": f"{case.id}/{variant}/1", "case_id": case.id,
                          "variant": variant, "repeat": 1, "report": report,
                          "elapsed_seconds": time.perf_counter() - start}
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"{record['key']}: {report.get('status')} "
                      f"findings={len(report.get('findings', []))}", flush=True)
    write_json(output / "finished.json", {"runs": len(cases) * len(variants)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "validate", "run"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--variants", nargs="+", choices=list(GROUPS), default=list(GROUPS))
    parser.add_argument("--cases", nargs="+")
    args = parser.parse_args()
    if args.action == "run":
        if args.output is None:
            parser.error("--output required")
        asyncio.run(run(args.output, args.live, args.variants, args.cases))
    else:
        {"build": build, "validate": validate}[args.action]()


if __name__ == "__main__":
    main()
