"""Tests for the Documentation agent."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from reviewer.agents import docs as docs_mod
from reviewer.agents.base import FindingList
from reviewer.agents.docs import (
    DOCS_LABEL,
    _entity_to_seed_finding,
    _undocumented,
    docs_agent_node,
)
from reviewer.models.findings import Category, Finding, Severity

SOURCE = '''def documented(a):
    """has doc"""
    return a

def undocumented(a, b):
    return a + b

def _private():
    return 1

class Public:
    """has doc"""
    def m(self):
        return self

class Undocumented:
    def public_method(self, x):
        return x

    def _private_method(self):
        return None
'''


def _state(tmp_path: Path) -> dict:
    return {
        "pr_diff": "",
        "changed_files": [
            {"path": "app/x.py", "content": SOURCE, "change_type": "modified"},
        ],
        "repo_path": str(tmp_path),
    }


def _llm_finding(line=5, sev=Severity.MEDIUM) -> Finding:
    return Finding(
        id="LLM-D-1",
        agent="security",  # wrong
        category=Category.DOCUMENTATION,
        severity=sev,  # wrong; node should normalize to LOW
        file_path="app/x.py",
        line_start=line,
        line_end=line,
        title="t",
        explanation="e",
        suggestion="s",
        confidence=0.9,
        tool_source="llm-analysis",
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def test_undocumented_finds_public_only(tmp_path):
    state = _state(tmp_path)
    found = _undocumented(state)
    qualnames = {e.qualname for e in found}
    # Public, undocumented entries
    assert "undocumented" in qualnames
    assert "Undocumented" in qualnames
    assert "Undocumented.public_method" in qualnames
    # Documented or private entries excluded
    assert "documented" not in qualnames
    assert "_private" not in qualnames
    assert "Public" not in qualnames  # class itself has docstring
    assert "Undocumented._private_method" not in qualnames  # private method


def test_undocumented_includes_undocumented_methods_of_documented_class(tmp_path):
    state = _state(tmp_path)
    qualnames = {e.qualname for e in _undocumented(state)}
    assert "Public.m" in qualnames


def test_undocumented_skips_syntax_errors(tmp_path):
    state = {
        "changed_files": [{"path": "bad.py", "content": "def f(:\n", "change_type": "modified"}],
        "repo_path": str(tmp_path),
    }
    assert _undocumented(state) == []


def test_undocumented_skips_removed_and_non_py(tmp_path):
    state = {
        "changed_files": [
            {"path": "a.py", "content": "def x(): pass\n", "change_type": "removed"},
            {"path": "b.md", "content": "# heading", "change_type": "modified"},
        ],
        "repo_path": str(tmp_path),
    }
    assert _undocumented(state) == []


def test_entity_to_seed_finding_severity_low():
    from reviewer.agents.docs import UndocumentedEntity

    e = UndocumentedEntity(
        file_path="a.py", qualname="foo", line_start=3, kind="function", args=("x",),
    )
    seed = _entity_to_seed_finding(e, 0)
    assert seed.severity == Severity.LOW
    assert seed.id == "DOC-001"
    assert "foo" in seed.title
    assert seed.tool_source == "ast-docs"


# --------------------------------------------------------------------------- #
# Node behavior                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_docs_agent_no_entities_succeeds(monkeypatch, tmp_path):
    state = {
        "changed_files": [
            {
                "path": "a.py",
                "content": '"""mod doc"""\ndef _hidden(): pass\n',
                "change_type": "modified",
            }
        ],
        "repo_path": str(tmp_path),
    }

    async def boom(*a, **kw):  # noqa: ARG001
        raise AssertionError("LLM should not run")

    monkeypatch.setattr(docs_mod, "analyze_with_schema", boom)
    out = await docs_agent_node(state)
    assert out == {"agents_succeeded": [DOCS_LABEL]}


@pytest.mark.asyncio
async def test_docs_agent_normalizes_label_and_severity(monkeypatch, tmp_path):
    async def fake_analyze(prompt, schema, **kw):  # noqa: ARG001
        return FindingList(findings=[_llm_finding(sev=Severity.HIGH)])

    monkeypatch.setattr(docs_mod, "analyze_with_schema", fake_analyze)

    out = await docs_agent_node(_state(tmp_path))
    assert out["agents_succeeded"] == [DOCS_LABEL]
    assert len(out["findings"]) == 1
    f = out["findings"][0]
    assert f.agent == DOCS_LABEL
    assert f.severity == Severity.LOW  # normalized from HIGH


@pytest.mark.asyncio
async def test_docs_agent_falls_back_to_seeds(monkeypatch, tmp_path):
    async def boom_llm(*a, **kw):  # noqa: ARG001
        raise RuntimeError("llm down")

    monkeypatch.setattr(docs_mod, "analyze_with_schema", boom_llm)
    out = await docs_agent_node(_state(tmp_path))
    assert out["agents_succeeded"] == [DOCS_LABEL]
    assert all(f.tool_source == "ast-docs" for f in out["findings"])
    assert all(f.severity == Severity.LOW for f in out["findings"])


@pytest.mark.asyncio
async def test_docs_agent_timeout(monkeypatch, tmp_path):
    async def slow(*a, **kw):  # noqa: ARG001
        await asyncio.sleep(5)
        return FindingList()

    monkeypatch.setattr(docs_mod, "analyze_with_schema", slow)
    monkeypatch.setattr(docs_mod, "DOCS_TIMEOUT_S", 0.05)

    out = await docs_agent_node(_state(tmp_path))
    assert "timeout" in out["agents_failed"][0]["error"]
