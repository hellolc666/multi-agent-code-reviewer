import json

import pytest

from evals.module_ablation import ROOT, write_json
from evals.module_scoring import calculate, export, inputs
from evals.run import load_corpus


def fixture_run(folder, live=True):
    cases, manifest = load_corpus(ROOT / "data")
    case = cases[0]
    gold = case.gold_labels[0]
    write_json(folder / "metadata.json", {
        "live": live, "dataset": manifest, "cases": [case.id], "variants": {"D": {}},
    })
    record = {"key": f"{case.id}/D/1", "case_id": case.id, "variant": "D",
              "elapsed_seconds": 1, "report": {"status": "partial", "findings": [
                  {"file_path": gold.file_path, "line_start": gold.line, "title": "one"},
                  {"file_path": gold.file_path, "line_start": gold.line, "title": "duplicate"},
              ]}}
    (folder / "runs.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    return gold.id


def test_partial_and_duplicates_stay_in_denominator(tmp_path):
    gid = fixture_run(tmp_path)
    export(tmp_path)
    decisions = json.loads((tmp_path / "scoring/decisions_template.json").read_text())
    for d in decisions:
        d.update(gold_id=gid, reason="Equivalent root cause for this test fixture")
    write_json(tmp_path / "scoring/decisions_simulated.json", decisions)
    calculate(tmp_path)
    result = json.loads((tmp_path / "evaluation.json").read_text())
    metrics = result["metrics"]["D"]
    assert (metrics["tp"], metrics["fp"], metrics["fn"]) == (1, 1, 1)
    assert metrics["completion_rate"] == 0
    assert metrics["precision"] == metrics["recall"] == 0.5
    assert result["human_signed_off"] is False
    assert result["benchmark_status"] == "exploratory_degraded"
    assert result["causal_claims_supported"] is False


def test_incomplete_plan_refused(tmp_path):
    fixture_run(tmp_path)
    (tmp_path / "runs.jsonl").write_text("")
    with pytest.raises(ValueError, match="Incomplete"):
        inputs(tmp_path)


def test_mock_quality_refused(tmp_path):
    fixture_run(tmp_path, live=False)
    with pytest.raises(ValueError, match="Mock"):
        calculate(tmp_path)


def test_unannotated_or_modified_blind_input_refused(tmp_path):
    fixture_run(tmp_path)
    export(tmp_path)
    template = json.loads((tmp_path / "scoring/decisions_template.json").read_text())
    write_json(tmp_path / "scoring/decisions_simulated.json", template)
    with pytest.raises(ValueError, match="explicit semantic"):
        calculate(tmp_path)
    with (tmp_path / "scoring/blind.json").open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="inputs changed"):
        calculate(tmp_path)
