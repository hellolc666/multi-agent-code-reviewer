import json
from pathlib import Path

import pytest

from evals.build_dataset import build
from evals.metrics import quality, score
from evals.run import DATA, VARIANTS, evaluate, load_corpus, run_case


def test_frozen_corpus_independent_cases_and_exact_gold():
    cases, manifest = load_corpus()
    assert len(cases) == 20
    assert len({case.diff for case in cases}) == 20
    assert sum(not c.gold_labels for c in cases) == 4
    assert len([g for c in cases for g in c.gold_labels]) == 16
    assert [c.model_dump() for c in cases] == [c.model_dump() for c in build()]
    assert manifest["dev"] == manifest["test"] == 10


def test_sha_change_refused(tmp_path):
    for name in ("cases.jsonl", "manifest.json"):
        (tmp_path / name).write_bytes((DATA / name).read_bytes())
    with (tmp_path / "cases.jsonl").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="SHA mismatch"):
        load_corpus(tmp_path)


@pytest.mark.asyncio
async def test_gold_never_crosses_runner_boundary(monkeypatch):
    case = build()[0]
    case.gold_labels[0].defect = "GOLD_ONLY_SECRET_CANARY"
    case.review_note = "GOLD_ONLY_SECRET_CANARY"

    async def inspect(patch, root, **kwargs):
        assert "GOLD_ONLY_SECRET_CANARY" not in Path(patch).read_text()
        for file in Path(root).rglob("*"):
            if file.is_file():
                assert "GOLD_ONLY_SECRET_CANARY" not in file.read_text()
        assert "GOLD_ONLY_SECRET_CANARY" not in json.dumps(kwargs)
        assert set(
            p.relative_to(root).as_posix() for p in Path(root).rglob("*") if p.is_file()
        ) == set(case.repo_files)
        return {"status": "completed"}

    monkeypatch.setattr("evals.run.run_review_diff", inspect)
    for variant in VARIANTS:
        assert (await run_case(case, variant, False, {}))["status"] == "completed"


def test_human_semantics_required_even_when_same_line():
    predictions = [{"file_path": "a.py", "line_start": 10}, {"file_path": "a.py", "line_start": 10}]
    gold = [{"id": "g", "file_path": "a.py", "line": 11}]
    counts = score(predictions, gold, [{"prediction_index": 1, "gold_id": "g"}])
    assert counts["tp"] == 1 and counts["fp"] == 1 and counts["fn"] == 0
    assert quality([counts], 1)["precision"] == 0.5
    assert score(predictions, gold, [])["tp"] == 0
    with pytest.raises(ValueError):
        score(
            predictions,
            gold,
            [{"prediction_index": 0, "gold_id": "g"}, {"prediction_index": 1, "gold_id": "g"}],
        )


def test_semantic_hit_wrong_location_is_false_positive_and_miss():
    counts = score(
        [{"file_path": "other.py", "line_start": 11}],
        [{"id": "g", "file_path": "a.py", "line": 11}],
        [{"prediction_index": 0, "gold_id": "g"}],
    )
    assert (counts["tp"], counts["fp"], counts["fn"]) == (0, 1, 1)
    assert quality([counts], 1)["location_accuracy"] == 0


@pytest.mark.asyncio
async def test_mock_ablation_and_three_runs_do_not_claim_accuracy(tmp_path):
    result = await evaluate(output=tmp_path / "report.json", limit=1, repeats=3)
    assert len(result["records"]) == 12
    assert result["metadata"]["status"] == "not_live_benchmark"
    for variant in VARIANTS:
        assert result["metrics"][variant]["quality"] is None
        assert result["metrics"][variant]["input_tokens"] is None
        assert result["three_run_consistency"][variant][0]["runs"] == 3
    assert "gold_labels" not in json.dumps(result)
    with pytest.raises(ValueError, match="output exists"):
        await evaluate(output=tmp_path / "report.json", limit=1)


@pytest.mark.asyncio
async def test_evaluation_retains_failure_runs(tmp_path, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("sensitive error")

    monkeypatch.setattr("evals.run.run_case", broken)
    result = await evaluate(output=tmp_path / "failed.json", limit=1)
    assert len(result["records"]) == 4
    assert all(r["report"]["status"] == "failed" for r in result["records"])
    assert "sensitive error" not in json.dumps(result)
    assert all(m["completion_rate"] == 0 for m in result["metrics"].values())
