import pytest

from scripts.demo import run_demo


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected", [("clean", 0), ("logic", 1), ("security", 1)])
async def test_scripted_demos_use_shared_graph_and_judge(tmp_path, case, expected):
    report = await run_demo(case, tmp_path / f"{case}.json")
    assert report["status"] == "completed"
    assert report["execution_mode"] == "mock"
    assert len(report["findings"]) == expected
    assert all(f["verification_status"] == "confirmed" for f in report["findings"])
    if expected:
        assert report["verified_findings"][0]["can_comment"] is True
    assert report["publication"]["status"] == "dry_run"
    assert (tmp_path / f"{case}.diff").exists()
