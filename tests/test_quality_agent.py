"""Tests for the Quality agent."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from reviewer.agents import quality as q_mod
from reviewer.agents.base import FindingList
from reviewer.agents.quality import (
    QUALITY_LABEL,
    _complexity_table,
    _python_targets,
    _ruff_severity,
    _ruff_to_seed_finding,
    quality_agent_node,
)
from reviewer.models.findings import Category, Finding, Severity
from reviewer.tools.ruff_runner import RuffFinding


def _state(repo_path: Path, *, files=None, diff="diff --git ...") -> dict:
    files = (
        files
        if files is not None
        else [
            {
                "path": "app/auth.py",
                "content": "def f(x):\n    if x: return 1\n    return 0\n",
                "change_type": "modified",
            },
        ]
    )
    return {
        "pr_url": "https://github.com/x/y/pull/1",
        "pr_diff": diff,
        "changed_files": files,
        "repo_path": str(repo_path),
    }


def _ruff(code="F401", path="app/auth.py", line=14) -> RuffFinding:
    return RuffFinding(
        code=code, path=path, line_start=line, line_end=line, column=1,
        message="msg", fix_applicable=True,
    )


def _llm_finding() -> Finding:
    return Finding(
        id="LLM-Q-1",
        agent="security",  # wrong on purpose
        category=Category.QUALITY,
        severity=Severity.MEDIUM,
        file_path="app/auth.py",
        line_start=10,
        line_end=10,
        title="t",
        explanation="e",
        suggestion="s",
        confidence=0.8,
        tool_source="llm-analysis",
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "code, expected",
    [
        ("F401", Severity.MEDIUM),
        ("E501", Severity.LOW),
        ("W291", Severity.LOW),
        ("B902", Severity.MEDIUM),
        ("UP035", Severity.LOW),
        ("I001", Severity.LOW),
        ("XYZ999", Severity.MEDIUM),  # unknown prefix
        ("", Severity.MEDIUM),
    ],
)
def test_ruff_severity(code, expected):
    assert _ruff_severity(code) == expected


def test_python_targets_filters():
    state = {
        "changed_files": [
            {"path": "a.py", "change_type": "modified"},
            {"path": "b.py", "change_type": "removed"},
            {"path": "c.md", "change_type": "modified"},
        ]
    }
    assert _python_targets(state) == ["a.py"]


def test_complexity_table_lists_functions(tmp_path):
    state = _state(
        tmp_path,
        files=[
            {
                "path": "x.py",
                "content": (
                    "def small(x):\n    return x\n\n"
                    "def big(x, y):\n    if x:\n        return y\n    return 0\n"
                ),
                "change_type": "modified",
            }
        ],
    )
    table = _complexity_table(state)
    assert "small" in table and "big" in table
    assert "complexity=" in table


def test_complexity_table_handles_syntax_error(tmp_path):
    state = _state(
        tmp_path,
        files=[{"path": "bad.py", "content": "def f(:\n", "change_type": "modified"}],
    )
    table = _complexity_table(state)
    assert "syntax error" in table


def test_ruff_to_seed_finding_includes_code():
    seed = _ruff_to_seed_finding(_ruff(code="F401", line=3), 0)
    assert seed.title == "F401"
    assert seed.tool_source == "ruff:F401"
    assert seed.id == "QUAL-RF-001"
    assert seed.severity == Severity.MEDIUM
    assert seed.line_start == 3


# --------------------------------------------------------------------------- #
# Node behavior                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_quality_agent_no_python_returns_succeeded(monkeypatch, tmp_path):
    async def boom(*a, **kw):  # noqa: ARG001
        raise AssertionError("must not run ruff")

    monkeypatch.setattr(q_mod, "run_ruff", boom)
    out = await quality_agent_node(
        _state(tmp_path, files=[{"path": "README.md", "change_type": "modified"}])
    )
    assert out == {"agents_succeeded": [QUALITY_LABEL]}


@pytest.mark.asyncio
async def test_quality_agent_happy_path_normalizes(monkeypatch, tmp_path):
    async def fake_ruff(*a, **kw):  # noqa: ARG001
        return [_ruff()]

    async def fake_analyze(prompt, schema, **kw):  # noqa: ARG001
        return FindingList(findings=[_llm_finding()])

    monkeypatch.setattr(q_mod, "run_ruff", fake_ruff)
    monkeypatch.setattr(q_mod, "analyze_with_schema", fake_analyze)

    out = await quality_agent_node(_state(tmp_path))
    assert out["agents_succeeded"] == [QUALITY_LABEL]
    assert len(out["findings"]) == 1
    assert out["findings"][0].agent == QUALITY_LABEL


@pytest.mark.asyncio
async def test_quality_agent_falls_back_to_ruff_seeds(monkeypatch, tmp_path):
    async def fake_ruff(*a, **kw):  # noqa: ARG001
        return [_ruff(code="F401", line=2)]

    async def boom_llm(*a, **kw):  # noqa: ARG001
        raise RuntimeError("llm down")

    monkeypatch.setattr(q_mod, "run_ruff", fake_ruff)
    monkeypatch.setattr(q_mod, "analyze_with_schema", boom_llm)

    out = await quality_agent_node(_state(tmp_path))
    assert out["agents_succeeded"] == [QUALITY_LABEL]
    assert len(out["findings"]) == 1
    assert out["findings"][0].tool_source == "ruff:F401"


@pytest.mark.asyncio
async def test_quality_agent_ruff_failure_continues(monkeypatch, tmp_path):
    async def boom_ruff(*a, **kw):  # noqa: ARG001
        raise RuntimeError("ruff crashed")

    async def fake_analyze(prompt, schema, **kw):  # noqa: ARG001
        assert "(no Ruff findings)" in prompt
        return FindingList(findings=[_llm_finding()])

    monkeypatch.setattr(q_mod, "run_ruff", boom_ruff)
    monkeypatch.setattr(q_mod, "analyze_with_schema", fake_analyze)

    out = await quality_agent_node(_state(tmp_path))
    assert out["agents_succeeded"] == [QUALITY_LABEL]
    assert len(out["findings"]) == 1


@pytest.mark.asyncio
async def test_quality_agent_timeout(monkeypatch, tmp_path):
    async def slow(*a, **kw):  # noqa: ARG001
        await asyncio.sleep(5)
        return []

    async def unused(*a, **kw):  # noqa: ARG001
        return FindingList()

    monkeypatch.setattr(q_mod, "run_ruff", slow)
    monkeypatch.setattr(q_mod, "analyze_with_schema", unused)
    monkeypatch.setattr(q_mod, "QUALITY_TIMEOUT_S", 0.05)

    out = await quality_agent_node(_state(tmp_path))
    assert "timeout" in out["agents_failed"][0]["error"]
