"""Tests for the Security agent node.

The node is a thin orchestrator over ``run_semgrep`` + ``analyze_with_schema``.
We monkeypatch both seams so tests are fully offline and deterministic.
A LangGraph integration test lives at Step 6.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from reviewer.agents import security as sec_mod
from reviewer.agents.base import FindingList
from reviewer.agents.security import (
    SECURITY_LABEL,
    _build_security_prompt,
    _python_targets,
    _semgrep_summary,
    _semgrep_to_seed_finding,
    security_agent_node,
)
from reviewer.models.findings import Category, Finding, Severity
from reviewer.tools.semgrep_runner import SemgrepFinding


def _state(repo_path: Path, *, files=None, diff="diff --git ...") -> dict:
    files = (
        files
        if files is not None
        else [
            {"path": "app/auth.py", "content": "x = 1\n", "change_type": "modified"},
            {"path": "docs/readme.md", "content": "...", "change_type": "modified"},
        ]
    )
    return {
        "pr_url": "https://github.com/x/y/pull/1",
        "pr_diff": diff,
        "changed_files": files,
        "repo_path": str(repo_path),
    }


def _llm_finding(title="t", line=10, sev=Severity.HIGH) -> Finding:
    return Finding(
        id="LLM-1",
        agent="quality",  # intentionally wrong; node should normalize to "security"
        category=Category.SECURITY,
        severity=sev,
        file_path="app/auth.py",
        line_start=line,
        line_end=line,
        title=title,
        explanation="e",
        suggestion="s",
        confidence=0.9,
        tool_source="llm-analysis",
    )


def _semgrep(check="check.id", path="app/auth.py", line=10, sev="WARNING") -> SemgrepFinding:
    return SemgrepFinding(
        check_id=check,
        path=path,
        line_start=line,
        line_end=line,
        severity=sev,
        message="msg",
        code_snippet="code",
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def test_python_targets_filters_non_py_and_removed():
    state = {
        "changed_files": [
            {"path": "a.py", "change_type": "modified"},
            {"path": "b.py", "change_type": "removed"},
            {"path": "c.md", "change_type": "modified"},
            {"path": "d.py", "change_type": "added"},
        ]
    }
    assert _python_targets(state) == ["a.py", "d.py"]


def test_semgrep_summary_empty():
    assert "no Semgrep" in _semgrep_summary([])


def test_semgrep_summary_includes_check_and_path():
    s = _semgrep_summary([_semgrep(check="py.x", path="a.py", line=3, sev="ERROR")])
    assert "py.x" in s
    assert "a.py:3" in s
    assert "[ERROR]" in s


def test_build_security_prompt_includes_diff_and_findings():
    prompt = _build_security_prompt(
        "diff content", [_semgrep(check="r1"), _semgrep(check="r2")]
    )
    assert "diff content" in prompt
    assert "r1" in prompt and "r2" in prompt
    assert "FindingList" in prompt


def test_semgrep_to_seed_finding_severity_mapping():
    seed = _semgrep_to_seed_finding(_semgrep(sev="ERROR"), 0)
    assert seed.severity == Severity.HIGH
    assert seed.agent == SECURITY_LABEL
    assert seed.tool_source.startswith("semgrep:")
    assert seed.id == "SEC-SG-001"


def test_semgrep_to_seed_finding_unknown_severity_defaults_medium():
    seed = _semgrep_to_seed_finding(_semgrep(sev="MYSTERY"), 5)
    assert seed.severity == Severity.MEDIUM
    assert seed.id == "SEC-SG-006"


# --------------------------------------------------------------------------- #
# security_agent_node — happy path                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_security_agent_no_python_files_succeeds_trivially(monkeypatch, tmp_path):
    async def boom_run_semgrep(*a, **kw):  # noqa: ARG001
        raise AssertionError("must not call semgrep with no targets")

    monkeypatch.setattr(sec_mod, "run_semgrep", boom_run_semgrep)
    state = _state(tmp_path, files=[{"path": "README.md", "change_type": "modified"}])

    out = await security_agent_node(state)

    assert out == {"agents_succeeded": [SECURITY_LABEL]}


@pytest.mark.asyncio
async def test_security_agent_happy_path_normalizes_agent_label(monkeypatch, tmp_path):
    async def fake_semgrep(repo_path, target_files, **kw):  # noqa: ARG001
        return [_semgrep(check="py.unsafe-eq", line=23)]

    async def fake_analyze(prompt, schema, *, system=None, llm=None):  # noqa: ARG001
        return FindingList(findings=[_llm_finding()])

    monkeypatch.setattr(sec_mod, "run_semgrep", fake_semgrep)
    monkeypatch.setattr(sec_mod, "analyze_with_schema", fake_analyze)

    out = await security_agent_node(_state(tmp_path))

    assert out["agents_succeeded"] == [SECURITY_LABEL]
    findings = out["findings"]
    assert len(findings) == 1
    # Agent label normalized even if LLM returned "quality"
    assert findings[0].agent == SECURITY_LABEL
    assert "agents_failed" not in out


# --------------------------------------------------------------------------- #
# security_agent_node — failure modes                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_security_agent_falls_back_to_seeds_when_llm_fails(monkeypatch, tmp_path):
    sg = [_semgrep(check="py.x", line=5, sev="ERROR")]

    async def fake_semgrep(*a, **kw):  # noqa: ARG001
        return sg

    async def boom_llm(*a, **kw):  # noqa: ARG001
        raise RuntimeError("llm 503")

    monkeypatch.setattr(sec_mod, "run_semgrep", fake_semgrep)
    monkeypatch.setattr(sec_mod, "analyze_with_schema", boom_llm)

    out = await security_agent_node(_state(tmp_path))

    # Node still succeeded; findings come from Semgrep seeds.
    assert out["agents_succeeded"] == [SECURITY_LABEL]
    assert "agents_failed" not in out
    assert len(out["findings"]) == 1
    seed = out["findings"][0]
    assert seed.tool_source == "semgrep:py.x"
    assert seed.severity == Severity.HIGH


@pytest.mark.asyncio
async def test_security_agent_continues_when_semgrep_fails(monkeypatch, tmp_path):
    """Semgrep crash should not block the LLM-only analysis path."""

    async def boom_semgrep(*a, **kw):  # noqa: ARG001
        raise RuntimeError("semgrep crashed")

    async def fake_analyze(prompt, schema, **kw):  # noqa: ARG001
        # Verify Semgrep section says (no Semgrep findings)
        assert "(no Semgrep findings)" in prompt
        return FindingList(findings=[_llm_finding()])

    monkeypatch.setattr(sec_mod, "run_semgrep", boom_semgrep)
    monkeypatch.setattr(sec_mod, "analyze_with_schema", fake_analyze)

    out = await security_agent_node(_state(tmp_path))
    assert out["agents_succeeded"] == [SECURITY_LABEL]
    assert len(out["findings"]) == 1


@pytest.mark.asyncio
async def test_security_agent_timeout_records_failure(monkeypatch, tmp_path):
    async def slow_semgrep(*a, **kw):  # noqa: ARG001
        await asyncio.sleep(5.0)
        return []

    async def unused_llm(*a, **kw):  # noqa: ARG001
        return FindingList()

    monkeypatch.setattr(sec_mod, "run_semgrep", slow_semgrep)
    monkeypatch.setattr(sec_mod, "analyze_with_schema", unused_llm)
    monkeypatch.setattr(sec_mod, "SECURITY_TIMEOUT_S", 0.05)

    out = await security_agent_node(_state(tmp_path))

    assert "agents_succeeded" not in out
    assert out["agents_failed"]
    rec = out["agents_failed"][0]
    assert rec["agent"] == SECURITY_LABEL
    assert "timeout" in rec["error"]


@pytest.mark.asyncio
async def test_security_agent_unexpected_exception_recorded(monkeypatch, tmp_path):
    """Anything other than TimeoutError still becomes an ``agents_failed`` entry."""

    async def fake_semgrep(*a, **kw):  # noqa: ARG001
        return []

    async def fake_analyze(*a, **kw):  # noqa: ARG001
        return FindingList()

    # Force the very-outer ``await _run`` to blow up.
    async def boom_run(state):  # noqa: ARG001
        raise ValueError("boom")

    monkeypatch.setattr(sec_mod, "run_semgrep", fake_semgrep)
    monkeypatch.setattr(sec_mod, "analyze_with_schema", fake_analyze)
    monkeypatch.setattr(sec_mod, "_run", boom_run)

    out = await security_agent_node(_state(tmp_path))
    assert out == {"agents_failed": [{"agent": SECURITY_LABEL, "error": "ValueError"}]}


@pytest.mark.asyncio
async def test_security_agent_empty_llm_falls_back_to_seeds(monkeypatch, tmp_path):
    """If LLM returns no findings but Semgrep had results, surface the seeds."""
    sg = [_semgrep(check="py.x", line=5, sev="ERROR")]

    async def fake_semgrep(*a, **kw):  # noqa: ARG001
        return sg

    async def fake_analyze(*a, **kw):  # noqa: ARG001
        return FindingList(findings=[])

    monkeypatch.setattr(sec_mod, "run_semgrep", fake_semgrep)
    monkeypatch.setattr(sec_mod, "analyze_with_schema", fake_analyze)

    out = await security_agent_node(_state(tmp_path))
    assert len(out["findings"]) == 1
    assert out["findings"][0].tool_source == "semgrep:py.x"


@pytest.mark.asyncio
async def test_security_agent_respects_env_timeout_override(monkeypatch, tmp_path):
    """``REVIEWER_SECURITY_TIMEOUT_S`` env var must drive the actual timeout.

    Set the env to 0.05s, give the inner ``_run`` plenty of time, and assert
    we still record a timeout failure with the env-derived value reflected
    in the error message.
    """
    import asyncio as _asyncio

    async def slow_run(_state):  # noqa: ARG001
        await _asyncio.sleep(1.0)
        return {"agents_succeeded": ["security"]}

    monkeypatch.setattr(sec_mod, "_run", slow_run)
    monkeypatch.setenv("REVIEWER_SECURITY_TIMEOUT_S", "0.05")

    state = {
        "pr_url": "x",
        "changed_files": [{"path": "a.py", "content": "x=1\n", "change_type": "modified"}],
        "repo_path": str(tmp_path),
        "pr_diff": "",
    }
    out = await sec_mod.security_agent_node(state)
    failed = out.get("agents_failed", [])
    assert len(failed) == 1
    assert failed[0]["agent"] == "security"
    # Resolved timeout (0.05s) must show up in the error string, not the default.
    assert "timeout after 0s" in failed[0]["error"]  # 0.05 → "0s" via :.0f
