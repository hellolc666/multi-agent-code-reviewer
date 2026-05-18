"""Tests for the aggregator node."""

from __future__ import annotations

import pytest

from reviewer.graph import aggregator as agg_mod
from reviewer.graph.aggregator import (
    DEFAULT_SUMMARY,
    _duration_seconds,
    _SummaryResponse,
    aggregate_findings_node,
    deduplicate,
    sort_findings,
)
from reviewer.models.findings import Category, Finding, Severity


def _f(
    *,
    id="X",
    sev=Severity.MEDIUM,
    cat=Category.SECURITY,
    file="a.py",
    line=10,
    title="t",
) -> Finding:
    return Finding(
        id=id,
        agent="security",
        category=cat,
        severity=sev,
        file_path=file,
        line_start=line,
        line_end=line,
        title=title,
        explanation="e",
        suggestion="s",
        confidence=0.9,
        tool_source=None,
    )


# --------------------------------------------------------------------------- #
# Dedup                                                                       #
# --------------------------------------------------------------------------- #


def test_dedup_keeps_highest_severity():
    a = _f(id="A", sev=Severity.LOW)
    b = _f(id="B", sev=Severity.HIGH)
    out = deduplicate([a, b])
    assert len(out) == 1
    assert out[0].severity == Severity.HIGH


def test_dedup_distinct_keys_pass_through():
    a = _f(id="A", file="a.py", line=10, cat=Category.SECURITY)
    b = _f(id="B", file="a.py", line=11, cat=Category.SECURITY)
    c = _f(id="C", file="b.py", line=10, cat=Category.SECURITY)
    d = _f(id="D", file="a.py", line=10, cat=Category.QUALITY)
    out = deduplicate([a, b, c, d])
    assert len(out) == 4


def test_dedup_handles_none_line_start():
    a = _f(id="A", line=None, sev=Severity.LOW)
    b = _f(id="B", line=None, sev=Severity.CRITICAL)
    out = deduplicate([a, b])
    assert len(out) == 1
    assert out[0].severity == Severity.CRITICAL


def test_dedup_first_seen_wins_on_tie():
    a = _f(id="A", sev=Severity.HIGH, title="rich-explanation")
    b = _f(id="B", sev=Severity.HIGH, title="seed")
    out = deduplicate([a, b])
    assert out[0].id == "A"


# --------------------------------------------------------------------------- #
# Sort                                                                        #
# --------------------------------------------------------------------------- #


def test_sort_severity_descending():
    items = [
        _f(id="info", sev=Severity.INFO),
        _f(id="critical", sev=Severity.CRITICAL),
        _f(id="medium", sev=Severity.MEDIUM),
        _f(id="high", sev=Severity.HIGH),
        _f(id="low", sev=Severity.LOW),
    ]
    out = sort_findings(items)
    assert [f.id for f in out] == ["critical", "high", "medium", "low", "info"]


def test_sort_ties_break_by_path_then_line():
    items = [
        _f(id="a-20", file="a.py", line=20),
        _f(id="b-5", file="b.py", line=5),
        _f(id="a-5", file="a.py", line=5),
        _f(id="a-none", file="a.py", line=None),
    ]
    out = sort_findings(items)
    assert [f.id for f in out] == ["a-5", "a-20", "a-none", "b-5"]


# --------------------------------------------------------------------------- #
# Duration parsing                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "start, end, expected",
    [
        ("2026-05-17T14:00:00Z", "2026-05-17T14:01:23Z", 83.0),
        ("2026-05-17T14:00:00+00:00", "2026-05-17T14:00:00+00:00", 0.0),
        ("not a date", "2026-05-17T14:00:00Z", 0.0),
        ("2026-05-17T14:01:00Z", "2026-05-17T14:00:00Z", 0.0),  # negative clamp
    ],
)
def test_duration_seconds(start, end, expected):
    assert _duration_seconds(start, end) == expected


# --------------------------------------------------------------------------- #
# Summary generation                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_summary_no_findings_no_failures_uses_clean_message(monkeypatch):
    async def boom(*a, **kw):  # noqa: ARG001
        raise AssertionError("LLM must not be called when there's nothing to summarize")

    monkeypatch.setattr(agg_mod, "analyze_with_schema", boom)
    out = await agg_mod._generate_summary([], [])
    assert "no issues" in out.lower()


@pytest.mark.asyncio
async def test_summary_uses_llm_when_findings_present(monkeypatch):
    async def fake_analyze(prompt, schema, **kw):  # noqa: ARG001
        assert "Findings" in prompt
        return _SummaryResponse(summary="One critical bug spotted in app/auth.py.")

    monkeypatch.setattr(agg_mod, "analyze_with_schema", fake_analyze)
    out = await agg_mod._generate_summary([_f(sev=Severity.CRITICAL)], [])
    assert "critical" in out


@pytest.mark.asyncio
async def test_summary_retries_then_falls_back(monkeypatch):
    calls = {"n": 0}

    async def flaky(*a, **kw):  # noqa: ARG001
        calls["n"] += 1
        raise RuntimeError("llm 503")

    monkeypatch.setattr(agg_mod, "analyze_with_schema", flaky)
    out = await agg_mod._generate_summary([_f()], [])
    assert out == DEFAULT_SUMMARY
    # SUMMARY_MAX_RETRIES + 1 attempts
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_summary_empty_response_falls_back(monkeypatch):
    async def empty(*a, **kw):  # noqa: ARG001
        return _SummaryResponse(summary="   ")

    monkeypatch.setattr(agg_mod, "analyze_with_schema", empty)
    out = await agg_mod._generate_summary([_f()], [])
    assert out == DEFAULT_SUMMARY


# --------------------------------------------------------------------------- #
# aggregate_findings_node                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_aggregate_node_builds_review_report(monkeypatch):
    async def fake_summary(findings, agents_failed):  # noqa: ARG001
        return "All good."

    monkeypatch.setattr(agg_mod, "_generate_summary", fake_summary)

    state = {
        "pr_url": "https://github.com/x/y/pull/1",
        "pr_metadata": {
            "title": "Fix bug",
            "author": "alice",
            "started_at": "2026-05-17T14:00:00Z",
        },
        "changed_files": [{"path": "a.py"}, {"path": "b.py"}],
        "findings": [
            _f(id="DUP", sev=Severity.LOW),
            _f(id="DUP-HIGHER", sev=Severity.HIGH),  # same key, wins
            _f(id="OTHER", file="b.py", sev=Severity.MEDIUM, line=3),
        ],
        "agents_succeeded": ["security", "security", "quality"],  # dedupe
        "agents_failed": [{"agent": "docs", "error": "timeout"}],
        "trace_id": "trace-42",
    }

    out = await aggregate_findings_node(state)

    assert "final_report" in out
    rep = out["final_report"]
    assert rep["pr_url"] == state["pr_url"]
    assert rep["pr_title"] == "Fix bug"
    assert rep["pr_author"] == "alice"
    assert rep["files_changed"] == 2
    assert rep["summary"] == "All good."
    assert rep["trace_id"] == "trace-42"
    assert rep["agents_succeeded"] == ["security", "quality"]  # deduped & ordered
    assert rep["agents_failed"][0]["agent"] == "docs"

    findings = rep["findings"]
    # Dedupe collapsed the two same-key entries; HIGH wins
    assert len(findings) == 2
    assert findings[0]["severity"] == "high"
    assert findings[0]["id"] == "DUP-HIGHER"
    # Sort: HIGH first, then MEDIUM
    assert findings[1]["severity"] == "medium"


@pytest.mark.asyncio
async def test_aggregate_node_generates_trace_id_if_missing(monkeypatch):
    async def fake_summary(*a, **kw):  # noqa: ARG001
        return "ok"

    monkeypatch.setattr(agg_mod, "_generate_summary", fake_summary)

    # Include a succeeded agent so we take the LLM-summary path (the
    # no-agents-no-findings path skips ``_generate_summary`` entirely).
    out = await aggregate_findings_node(
        {
            "pr_url": "x",
            "findings": [],
            "changed_files": [],
            "agents_succeeded": ["security"],
        }
    )
    rep = out["final_report"]
    # UUID4 form
    assert len(rep["trace_id"]) >= 32
    assert rep["summary"] == "ok"
    assert rep["findings"] == []


# --------------------------------------------------------------------------- #
# No-applicable-agents path (e.g. non-Python PR in v1)                        #
# --------------------------------------------------------------------------- #


def test_no_agents_summary_lists_extensions_present():
    """Helper should enumerate file extensions from the diff."""
    from reviewer.graph.aggregator import _no_agents_summary

    out = _no_agents_summary(
        [
            {"path": "src/main.rs", "content": "", "change_type": "modified"},
            {"path": "src/lib.rs", "content": "", "change_type": "modified"},
            {"path": "config.json", "content": "", "change_type": "modified"},
        ]
    )
    assert "Python" in out
    assert "2 .rs" in out
    assert "1 .json" in out


def test_no_agents_summary_handles_no_files():
    from reviewer.graph.aggregator import _no_agents_summary

    out = _no_agents_summary([])
    assert "no files" in out.lower()
    assert "Python" in out


@pytest.mark.asyncio
async def test_aggregate_node_empty_agents_path_skips_llm(monkeypatch):
    """When no agents ran, aggregate must skip the LLM and emit the explainer."""

    async def boom(*a, **kw):  # noqa: ARG001
        raise AssertionError("LLM must not be called on the no-agents path")

    monkeypatch.setattr(agg_mod, "analyze_with_schema", boom)

    state = {
        "pr_url": "https://github.com/owner/repo/pull/1",
        "pr_metadata": {
            "title": "Rust changes",
            "author": "x",
            "started_at": "2026-05-18T00:00:00Z",
        },
        "changed_files": [
            {"path": "src/main.rs", "content": "", "change_type": "modified"},
        ],
        "findings": [],
        "agents_to_run": [],
        "agents_succeeded": [],
        "agents_failed": [],
        "trace_id": "trace-1",
    }
    patch = await aggregate_findings_node(state)
    report = patch["final_report"]
    assert report["summary"].startswith("No applicable agents")
    assert "Python" in report["summary"]
    assert ".rs" in report["summary"]
    assert report["findings"] == []
    assert report["files_changed"] == 1
    assert report["agents_succeeded"] == []


@pytest.mark.asyncio
async def test_aggregate_node_with_findings_uses_llm_path(monkeypatch):
    """Sanity: when agents DID run, the LLM path is still taken."""
    captured = {"called": False}

    async def fake_analyze(prompt, schema, **kw):  # noqa: ARG001
        captured["called"] = True
        return _SummaryResponse(summary="One issue.")

    monkeypatch.setattr(agg_mod, "analyze_with_schema", fake_analyze)

    state = {
        "pr_url": "https://github.com/owner/repo/pull/1",
        "pr_metadata": {"title": "x", "author": "y", "started_at": "2026-05-18T00:00:00Z"},
        "changed_files": [{"path": "a.py", "content": "x=1\n", "change_type": "modified"}],
        "findings": [_f(sev=Severity.HIGH)],
        "agents_to_run": ["security_agent"],
        "agents_succeeded": ["security"],
        "agents_failed": [],
        "trace_id": "trace-2",
    }
    patch = await aggregate_findings_node(state)
    assert captured["called"] is True
    assert patch["final_report"]["summary"] == "One issue."
