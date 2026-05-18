"""Tests for the LangGraph workflow assembly.

Covers:
    - ``dispatch_specialists`` purity + Send payload shape.
    - End-to-end graph run with mocked tool runners + LLM, asserting that
      all four agents contribute findings and the state's reducers (add)
      accumulate them across parallel branches.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.types import Send

from reviewer.agents import docs as docs_mod
from reviewer.agents import quality as q_mod
from reviewer.agents import security as sec_mod
from reviewer.agents import test_mapping as tm_mod
from reviewer.agents.base import FindingList
from reviewer.graph import aggregator as agg_mod
from reviewer.graph.builder import build_review_graph, dispatch_specialists
from reviewer.graph.state import AGENT_NODES
from reviewer.models.findings import Category, Finding, Severity
from reviewer.tools.ruff_runner import RuffFinding
from reviewer.tools.semgrep_runner import SemgrepFinding

# --------------------------------------------------------------------------- #
# dispatch_specialists                                                        #
# --------------------------------------------------------------------------- #


def test_dispatch_returns_send_per_agent():
    state = {"agents_to_run": ["security_agent", "quality_agent"]}
    sends = dispatch_specialists(state)
    assert all(isinstance(s, Send) for s in sends)
    names = [s.node for s in sends]
    assert names == ["security_agent", "quality_agent"]


def test_dispatch_payload_is_full_state():
    state = {
        "agents_to_run": ["docs_agent"],
        "pr_url": "x",
        "changed_files": [{"path": "a.py"}],
    }
    sends = dispatch_specialists(state)
    assert sends[0].arg == state  # full-state passthrough, not delta


def test_dispatch_drops_unknown_agents():
    """Defensive: typo'd planner names get filtered."""
    state = {"agents_to_run": ["security_agent", "bogus_agent"]}
    sends = dispatch_specialists(state)
    assert [s.node for s in sends] == ["security_agent"]


def test_dispatch_pure_does_not_mutate():
    """Router must not mutate state."""
    state = {"agents_to_run": ["security_agent"], "pr_url": "x"}
    snapshot = dict(state)
    dispatch_specialists(state)
    assert state == snapshot


def test_dispatch_empty_falls_through_to_aggregate():
    """Empty agents_to_run must route directly to aggregate.

    Regression for the 'silent {}' bug where a no-Python PR dead-ended the
    graph because dispatch returned an empty list.
    """
    sends = dispatch_specialists({"agents_to_run": []})
    assert len(sends) == 1
    assert sends[0].node == "aggregate"

    sends = dispatch_specialists({})
    assert len(sends) == 1
    assert sends[0].node == "aggregate"


# --------------------------------------------------------------------------- #
# Integration: full graph run with mocked tools + LLM                          #
# --------------------------------------------------------------------------- #


def _finding(agent: str, idx: int, file="app/auth.py", line=10) -> Finding:
    cat_map = {
        "security": Category.SECURITY,
        "quality": Category.QUALITY,
        "test_mapping": Category.TEST_COVERAGE,
        "docs": Category.DOCUMENTATION,
    }
    sev_map = {
        "security": Severity.HIGH,
        "quality": Severity.MEDIUM,
        "test_mapping": Severity.MEDIUM,
        "docs": Severity.LOW,
    }
    return Finding(
        id=f"{agent.upper()}-{idx:03d}",
        agent=agent,
        category=cat_map[agent],
        severity=sev_map[agent],
        file_path=file,
        line_start=line,
        line_end=line,
        title=f"{agent} finding",
        explanation=f"detected by {agent}",
        suggestion="...",
        confidence=0.9,
        tool_source="llm-analysis",
    )


@pytest.fixture
def state_with_pr(tmp_path: Path) -> dict:
    """Pre-seeded state simulating ``fetch_pr`` having already run."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_auth.py").write_text(
        "from app.auth import login\ndef test_x():\n    assert login('a','a') is True\n",
        encoding="utf-8",
    )

    src = (
        "def login(password, stored):\n"
        "    if password == stored:\n"
        "        return True\n"
        "    return False\n"
        "\n"
        "def helper():\n"
        "    return 1\n"
    )
    return {
        "pr_url": "https://github.com/x/y/pull/1",
        "pr_diff": "diff --git a/app/auth.py b/app/auth.py\n+def login(...): ...\n",
        "changed_files": [
            {"path": "app/auth.py", "content": src, "change_type": "modified"},
        ],
        "test_files": [],
        "repo_path": str(repo),
        "trace_id": "test-trace",
    }


@pytest.fixture
def patched_tools_and_llm(monkeypatch):
    """Mock all tool subprocesses + LLM calls for deterministic graph runs."""

    async def fake_semgrep(*a, **kw):  # noqa: ARG001
        return [
            SemgrepFinding(
                check_id="py.unsafe-eq", path="app/auth.py", line_start=2,
                line_end=2, severity="WARNING", message="==", code_snippet="x == y",
            )
        ]

    async def fake_ruff(*a, **kw):  # noqa: ARG001
        return [
            RuffFinding(
                code="F401", path="app/auth.py", line_start=1, line_end=1, column=1,
                message="unused", fix_applicable=True,
            )
        ]

    async def fake_analyze_security(*a, **kw):  # noqa: ARG001
        return FindingList(findings=[_finding("security", 1, line=2)])

    async def fake_analyze_quality(*a, **kw):  # noqa: ARG001
        return FindingList(findings=[_finding("quality", 1, line=1)])

    async def fake_analyze_test_mapping(*a, **kw):  # noqa: ARG001
        return FindingList(findings=[_finding("test_mapping", 1, line=6)])

    async def fake_analyze_docs(*a, **kw):  # noqa: ARG001
        return FindingList(findings=[_finding("docs", 1, line=1)])

    monkeypatch.setattr(sec_mod, "run_semgrep", fake_semgrep)
    monkeypatch.setattr(sec_mod, "analyze_with_schema", fake_analyze_security)
    monkeypatch.setattr(q_mod, "run_ruff", fake_ruff)
    monkeypatch.setattr(q_mod, "analyze_with_schema", fake_analyze_quality)
    monkeypatch.setattr(tm_mod, "analyze_with_schema", fake_analyze_test_mapping)
    monkeypatch.setattr(docs_mod, "analyze_with_schema", fake_analyze_docs)

    # Mock the aggregator's summary call so we don't hit the real LLM
    # (which would burn retry+breaker time with no API key).
    async def fake_summary(findings, agents_failed):  # noqa: ARG001
        return "Mocked summary."

    monkeypatch.setattr(agg_mod, "_generate_summary", fake_summary)


@pytest.mark.asyncio
async def test_graph_runs_all_four_agents_in_parallel(state_with_pr, patched_tools_and_llm):
    graph = build_review_graph()
    result = await graph.ainvoke(state_with_pr)

    # Each agent contributed at least one finding via the ``add`` reducer.
    assert "findings" in result
    by_agent = {f.agent for f in result["findings"]}
    assert by_agent == {"security", "quality", "test_mapping", "docs"}

    # All four reported success.
    assert set(result["agents_succeeded"]) == {
        "security", "quality", "test_mapping", "docs"
    }
    assert result.get("agents_failed", []) == []


@pytest.mark.asyncio
async def test_graph_skips_agents_planner_excludes(state_with_pr, patched_tools_and_llm):
    """Markdown-only PR runs no agents — but still produces a structured report.

    Regression: the graph used to dead-end here (dispatch_specialists returned
    an empty list of Sends, aggregate never ran, ``final_report`` was unset).
    Now dispatch falls through to aggregate which writes an explainer summary.
    """
    state_with_pr["changed_files"] = [
        {"path": "README.md", "content": "# x", "change_type": "modified"},
    ]
    graph = build_review_graph()
    result = await graph.ainvoke(state_with_pr)

    assert result.get("findings", []) == []
    assert result.get("agents_succeeded", []) == []

    report = result.get("final_report")
    assert report is not None, "aggregate must run even when no agents apply"
    assert report["findings"] == []
    assert report["summary"].startswith("No applicable agents")
    assert "Python" in report["summary"]
    assert report["files_changed"] == 1


@pytest.mark.asyncio
async def test_graph_partial_failure_still_completes(
    state_with_pr, patched_tools_and_llm, monkeypatch
):
    """One agent's crash must not block the others; aggregate runs anyway."""

    async def boom_security(*a, **kw):  # noqa: ARG001
        raise RuntimeError("security exploded")

    monkeypatch.setattr(sec_mod, "_run", boom_security)

    graph = build_review_graph()
    result = await graph.ainvoke(state_with_pr)

    # Security failure recorded, but the others succeeded.
    failed = {rec["agent"] for rec in result.get("agents_failed", [])}
    assert "security" in failed
    succeeded = set(result.get("agents_succeeded", []))
    assert succeeded == {"quality", "test_mapping", "docs"}


def test_compiled_graph_has_all_nodes():
    graph = build_review_graph()
    nodes = set(graph.get_graph().nodes)
    expected = {"fetch_pr", "plan_review", "aggregate", *AGENT_NODES}
    assert expected <= nodes
