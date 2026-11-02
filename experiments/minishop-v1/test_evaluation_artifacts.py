"""Offline checks for corpus identity and the simulated-annotation boundary."""

import json

import pytest

import build_dataset
import score_ablation as scoring


def test_formatted_builder_still_reproduces_frozen_corpus(tmp_path, monkeypatch):
    frozen = scoring.read_jsonl(scoring.HERE / "data/cases.jsonl")
    monkeypatch.setattr(build_dataset, "HERE", tmp_path)
    build_dataset.build()
    assert scoring.read_jsonl(tmp_path / "data/cases.jsonl") == frozen


def test_blind_export_and_one_to_one_scoring(tmp_path, monkeypatch):
    cases = {"example": {"gold_labels": [{"id": "g", "file_path": "a.py", "line": 10}]}}
    records = []
    for variant, locations in [("A", [10, 11]), ("B", []), ("C", [20]), ("D", [10])]:
        records.append(
            {
                "key": f"example/{variant}/1",
                "case_id": "example",
                "variant": variant,
                "elapsed_seconds": 1,
                "report": {
                    "status": "partial" if variant == "B" else "completed",
                    "findings": [{"file_path": "a.py", "line_start": n} for n in locations],
                },
            }
        )
    monkeypatch.setattr(scoring, "HERE", tmp_path)
    monkeypatch.setattr(scoring, "inputs", lambda: (cases, {"sha256": "fixture"}, records))
    (tmp_path / "results").mkdir()
    (tmp_path / "results/runs.jsonl").write_text("fixture", encoding="utf-8")
    scoring.export_blind()
    blinded = scoring.read_jsonl(tmp_path / "scoring/blind_predictions.jsonl")
    assert len(blinded) == 4
    assert all("variant" not in x and "key" not in x for x in blinded)
    decisions = [
        {"blind_id": x["blind_id"], "gold_id": "g", "reason": "Equivalent defect"} for x in blinded
    ]
    scoring.write_json(tmp_path / "scoring/decisions_simulated.json", decisions)
    scoring.calculate()
    metrics = json.loads((tmp_path / "results/evaluation.json").read_text())["metrics"]
    assert (metrics["A"]["quality"]["tp"], metrics["A"]["quality"]["fp"]) == (1, 1)
    assert metrics["B"]["quality"]["fn"] == 1
    assert metrics["B"]["statuses"] == {"partial": 1}
    assert (metrics["C"]["quality"]["tp"], metrics["C"]["quality"]["fn"]) == (0, 1)
    assert metrics["D"]["quality"]["recall"] == 1
    assert metrics["D"]["operational"]["estimated_cost"] is None
    decisions[0]["reason"] = ""
    scoring.write_json(tmp_path / "scoring/decisions_simulated.json", decisions)
    with pytest.raises(AssertionError, match="explicit semantic judgment"):
        scoring.calculate()
