"""Tests for the deterministic planner."""

from __future__ import annotations

import pytest

from reviewer.graph.planner import _is_test_path, plan_review_node


@pytest.mark.parametrize(
    "path, expected",
    [
        ("tests/test_x.py", True),
        ("test/foo.py", True),
        ("app/test_helpers.py", True),
        ("app/auth_test.py", True),
        ("app/auth.py", False),
        ("docs/readme.md", False),
        ("src/test_module/file.py", False),
    ],
)
def test_is_test_path(path, expected):
    assert _is_test_path(path) is expected


def _files(*specs):
    return [{"path": p, "change_type": ct, "content": ""} for (p, ct) in specs]


@pytest.mark.asyncio
async def test_planner_no_python_runs_nothing():
    state = {"changed_files": _files(("README.md", "modified"), ("docs/x.md", "added"))}
    out = await plan_review_node(state)
    assert out["agents_to_run"] == []
    assert out["review_plan"]["selected_agents"] == []


@pytest.mark.asyncio
async def test_planner_python_source_runs_all_four():
    state = {"changed_files": _files(("app/auth.py", "modified"))}
    out = await plan_review_node(state)
    assert set(out["agents_to_run"]) == {
        "security_agent",
        "quality_agent",
        "correctness_agent",
        "test_mapping_agent",
    }


@pytest.mark.asyncio
async def test_planner_only_test_changes_skips_test_mapping():
    """If ALL Python changes are in tests, test_mapping has nothing to map."""
    state = {"changed_files": _files(("tests/test_auth.py", "modified"))}
    out = await plan_review_node(state)
    agents = out["agents_to_run"]
    assert "test_mapping_agent" not in agents
    assert "correctness_agent" in agents
    assert "quality_agent" in agents
    assert "docs_agent" not in agents
    assert "security_agent" not in agents


@pytest.mark.asyncio
async def test_planner_mixed_runs_all_four():
    state = {
        "changed_files": _files(
            ("app/auth.py", "modified"),
            ("tests/test_auth.py", "added"),
        )
    }
    out = await plan_review_node(state)
    assert set(out["agents_to_run"]) == {
        "security_agent",
        "quality_agent",
        "correctness_agent",
        "test_mapping_agent",
    }


@pytest.mark.asyncio
async def test_planner_removed_python_file_does_not_trigger_test_mapping():
    """A removed source file isn't 'present' for the test_mapping agent to scan."""
    state = {"changed_files": _files(("app/auth.py", "removed"))}
    out = await plan_review_node(state)
    # Security/quality/docs still run (planner only checks .py extension).
    # test_mapping needs a *non-removed* non-test source to be useful — should skip.
    assert "test_mapping_agent" not in out["agents_to_run"]


@pytest.mark.asyncio
async def test_planner_dedupes_and_keeps_order():
    state = {
        "changed_files": _files(
            ("a.py", "modified"),
            ("b.py", "modified"),
            ("c.py", "added"),
        )
    }
    out = await plan_review_node(state)
    # Each agent appears exactly once.
    assert len(out["agents_to_run"]) == len(set(out["agents_to_run"]))
    assert out["agents_to_run"][0] == "correctness_agent"
