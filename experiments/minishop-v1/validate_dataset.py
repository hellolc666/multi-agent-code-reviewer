"""Validate authored labels with separate assertions; never feed them to the model."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from evals.run import load_corpus

HERE = Path(__file__).resolve().parent


def run(root, *args):
    result = subprocess.run(
        [sys.executable, *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def write_files(root, files):
    for name, content in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")


def main():
    cases, manifest = load_corpus(HERE / "data")
    probes = json.loads((HERE / "data/probes.json").read_text(encoding="utf-8"))
    base = {
        p.relative_to(HERE / "base").as_posix(): p.read_text(encoding="utf-8")
        for p in (HERE / "base").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    tmp = HERE / "tmp"
    tmp.mkdir(exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(dir=tmp) as directory:
        root = Path(directory)
        write_files(root, base)
        baseline = run(root, "-m", "unittest", "discover", "-s", "tests", "-v")
        assert baseline["returncode"] == 0, baseline
        for case in cases:
            before = run(root, "-c", probes[case.id]["code"])
            assert before["returncode"] == 0, (case.id, before)
            with tempfile.TemporaryDirectory(dir=tmp) as case_directory:
                target = Path(case_directory)
                write_files(target, base)
                patch = HERE / "data/patches" / (case.id + ".diff")
                applied = subprocess.run(
                    ["git", "apply", "--whitespace=nowarn", str(patch)],
                    cwd=target,
                    capture_output=True,
                    text=True,
                )
                assert applied.returncode == 0, (case.id, applied.stderr)
                for path, content in case.repo_files.items():
                    assert (target / path).read_text(encoding="utf-8") == content, (case.id, path)
                after = run(target, "-c", probes[case.id]["code"])
                tests = run(target, "-m", "unittest", "discover", "-s", "tests", "-v")
                expected = probes[case.id]["candidate_should_pass"]
                assert (after["returncode"] == 0) == expected, (case.id, after)
                if expected:
                    assert tests["returncode"] == 0, (case.id, tests)
                results.append(
                    {
                        "case_id": case.id,
                        "split": case.split,
                        "base_probe": before,
                        "candidate_probe": after,
                        "candidate_tests": tests,
                        "expected_candidate_pass": expected,
                    }
                )
    output = {
        "dataset_sha256": manifest["sha256"],
        "baseline_tests": baseline,
        "status": "all_20_labels_validated_by_authored_oracles",
        "records": results,
        "note": "These assertions support simulated labels; the review pipeline does not execute them.",
    }
    (HERE / "label_validation.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"status": output["status"], "cases": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
