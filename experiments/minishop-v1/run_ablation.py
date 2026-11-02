"""Reuse the project's A/B/C/D runner with a frozen full-repository corpus."""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL_PROJECT = HERE.parents[1]
PROJECT = (
    LOCAL_PROJECT
    if (LOCAL_PROJECT / "reviewer").is_dir()
    else Path(r"D:\desktop\AgentProjects\multi-agent-code-reviewer")
)
TOOLS = Path(os.environ.get("MINISHOP_TOOL_VENV", HERE.parent / "eval-tools-venv/Scripts"))
sys.path.insert(0, str(PROJECT))
os.environ["PATH"] = os.pathsep.join([str(PROJECT / ".venv/Scripts"), str(TOOLS), os.environ["PATH"]])
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["SEMGREP_SEND_METRICS"] = "off"
(HERE / "tmp").mkdir(exist_ok=True)
tempfile.tempdir = str(HERE / "tmp")

from evals.run import VARIANTS, load_corpus, run_case  # noqa: E402
from reviewer import config  # noqa: E402
from reviewer.reliability.harness import ReviewBudget  # noqa: E402


def code_fingerprint():
    entries = {}
    for directory in ("reviewer", "evals"):
        for p in (PROJECT / directory).rglob("*.py"):
            entries[p.relative_to(PROJECT).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return entries


def version(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("static tool preflight failed")
    return result.stdout.strip()


async def main(live):
    if live:
        from dotenv import load_dotenv
        load_dotenv(PROJECT / ".env", override=False)
        if config.llm_provider() != "qwen" or config.llm_model() != "qwen-max":
            raise ValueError("This frozen experiment requires qwen-max")
    cases, manifest = load_corpus(HERE / "data")
    validation = json.loads((HERE / "label_validation.json").read_text(encoding="utf-8"))
    assert validation["dataset_sha256"] == manifest["sha256"]
    cases = [c for c in cases if c.split == ("test" if live else "dev")]
    budget = ReviewBudget().model_dump()
    folder = HERE / ("results" if live else "offline-preflight")
    folder.mkdir(exist_ok=True)
    meta_path, runs_path = folder / "metadata.json", folder / "runs.jsonl"
    fingerprint = code_fingerprint()
    metadata = {
        "experiment":"minishop-abcd-v1", "live":live,
        "label_method":"AI-simulated human annotation, independently exercised with authored assertions",
        "human_signed_off":False, "dataset":manifest,
        "model":config.llm_model() if live else "MockLLM", "temperature":0,
        "budget":budget, "variants":VARIANTS, "cases":len(cases),
        "planned_runs":len(cases)*4, "repeats":1,
        "order":"Rotate ABCD by case index, execute sequentially; no rerun selection",
        "reviewer_source_sha256":fingerprint,
        "python":sys.version.split()[0],
        "packages":{name:importlib.metadata.version(name) for name in ("pydantic","langchain-openai","langgraph")},
        "tools":{"ruff":version([str(PROJECT / ".venv/Scripts/ruff.exe"),"--version"]),
                 "semgrep":version([str(TOOLS / "semgrep.exe"),"--version"]) if live else "not invoked",
                 "semgrep_rules":"p/python (registry ruleset; not content-pinned)"},
        "notes":["A and B emit unjudged findings; they are not auto-confirmed.",
                 "A receives the diff through the existing general-reviewer baseline; it has no retrieval tools.",
                 "All variants receive identical repository files and diff through run_case.",
                 "Test-split input contains no gold labels or external probe assertions.",
                 "No hypothetical cost; missing usage remains null."]
    }
    if meta_path.exists():
        previous = json.loads(meta_path.read_text(encoding="utf-8"))
        for field in ("dataset","model","budget","variants","reviewer_source_sha256"):
            assert previous[field] == metadata[field], f"resume mismatch: {field}"
    else:
        metadata["started_at"] = datetime.now(timezone.utc).isoformat()
        meta_path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    records = [json.loads(line) for line in runs_path.read_text(encoding="utf-8").splitlines()] if runs_path.exists() else []
    finished = {r["key"] for r in records}
    assert len(finished) == len(records)
    logging.basicConfig(level=logging.WARNING)
    with runs_path.open("a",encoding="utf-8",buffering=1) as stream:
        for index,case in enumerate(cases):
            variants = list("ABCD")
            variants = variants[index%4:] + variants[:index%4]
            for variant in variants:
                key=f"{case.id}/{variant}/1"
                if key in finished:
                    continue
                started=time.perf_counter()
                try:
                    report=await run_case(case,variant,live,budget)
                except Exception as exc:
                    report={"status":"failed","findings":[],"agents_failed":[{"agent":"pipeline","error":type(exc).__name__}]}
                record={"key":key,"case_id":case.id,"variant":variant,"repeat":1,
                        "report":report,"elapsed_seconds":time.perf_counter()-started}
                stream.write(json.dumps(record,ensure_ascii=False)+"\n")
                stream.flush()
                records.append(record)
                print(f"{len(records)}/{len(cases)*4} {key} status={report.get('status')} findings={len(report.get('findings',[]))}",flush=True)
    (folder / "finished.json").write_text(json.dumps({"runs":len(records),"completed_at":datetime.now(timezone.utc).isoformat()}),encoding="utf-8")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--live",action="store_true")
    args=parser.parse_args()
    asyncio.run(main(args.live))
