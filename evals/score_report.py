"""Score an existing run after human review; never re-call a model."""

import argparse
import json
from pathlib import Path

from evals.run import load_corpus, summarize


def rescore(report_path, matches_path, output):
    if Path(output).exists():
        raise ValueError("output already exists")
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    matches = json.loads(Path(matches_path).read_text(encoding="utf-8"))
    cases, manifest = load_corpus()
    if report["metadata"]["dataset"]["sha256"] != manifest["sha256"]:
        raise ValueError("report dataset differs from current frozen corpus")
    live = report["metadata"]["provider"] == "qwen"
    report["metrics"], report["three_run_consistency"] = summarize(
        report["records"], cases, live=live, manifest=manifest, matches=matches
    )
    report["human_review"] = {
        "reviewed_by": matches.get("reviewed_by"),
        "dataset_sha256": matches.get("dataset_sha256"),
    }
    Path(output).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--matches", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rescore(args.report, args.matches, args.output)
