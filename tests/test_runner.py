"""Tests for ``reviewer.runner.run_review`` and helper functions."""

from __future__ import annotations

from pathlib import Path

import pytest

from reviewer import runner as runner_mod
from reviewer.runner import (
    LABEL_TO_NODE,
    _format_comment_body,
    _resolve_only,
    run_review,
)
from reviewer.tools.github_client import PRBundle, PRMetadata

# --------------------------------------------------------------------------- #
# _resolve_only                                                               #
# --------------------------------------------------------------------------- #


def test_resolve_only_none():
    assert _resolve_only(None) is None
    assert _resolve_only([]) is None


def test_resolve_only_short_labels():
    assert _resolve_only(["security", "quality"]) == [
        "security_agent", "quality_agent",
    ]


def test_resolve_only_node_names_pass_through():
    assert _resolve_only(["docs_agent"]) == ["docs_agent"]


def test_resolve_only_dedupes_and_drops_unknown():
    assert _resolve_only(["security", "security", "bogus", "quality"]) == [
        "security_agent", "quality_agent",
    ]


def test_resolve_only_handles_whitespace():
    assert _resolve_only(["  security ", "", "  "]) == ["security_agent"]


def test_label_to_node_mapping_consistent():
    # All labels round-trip through both maps.
    assert LABEL_TO_NODE["security"] == "security_agent"
    assert LABEL_TO_NODE["test_mapping"] == "test_mapping_agent"


# --------------------------------------------------------------------------- #
# _format_comment_body                                                        #
# --------------------------------------------------------------------------- #


def test_format_comment_body_groups_by_severity():
    report = {
        "summary": "1 high, 1 low.",
        "files_changed": 2,
        "agents_succeeded": ["security", "docs"],
        "agents_failed": [],
        "findings": [
            {
                "severity": "high",
                "file_path": "a.py",
                "line_start": 5,
                "title": "SQLi",
                "explanation": "tainted input",
                "suggestion": "parameterize",
            },
            {
                "severity": "low",
                "file_path": "a.py",
                "line_start": None,
                "title": "missing docstring",
                "explanation": "",
                "suggestion": "add one",
            },
        ],
    }
    body = _format_comment_body(report)
    # First line must be the loud attribution warning.
    assert body.splitlines()[0].startswith("## ⚠️ This is an automated comment")
    assert "1 high, 1 low" in body
    assert "### HIGH (1)" in body
    assert "### LOW (1)" in body
    assert "`a.py:5`" in body
    assert "`a.py`" in body  # no line number
    assert "💡 parameterize" in body


def test_format_comment_body_includes_failures():
    report = {
        "summary": "x", "files_changed": 1, "findings": [],
        "agents_succeeded": ["security"],
        "agents_failed": [{"agent": "docs", "error": "timeout"}],
    }
    body = _format_comment_body(report)
    assert "docs (timeout)" in body


# --------------------------------------------------------------------------- #
# run_review                                                                  #
# --------------------------------------------------------------------------- #


@pytest.fixture
def fake_bundle(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app").mkdir()
    (repo / "app" / "auth.py").write_text(
        "def login(p, s):\n    return p == s\n", encoding="utf-8",
    )

    bundle = PRBundle(
        pr_url="https://github.com/x/y/pull/42",
        metadata=PRMetadata(
            owner="x", repo="y", number=42, title="Fix",
            author="alice", head_sha="deadbeef", head_ref="feature",
            base_ref="main", head_repo_clone_url="https://github.com/x/y.git",
        ),
        pr_diff="diff --git a/app/auth.py b/app/auth.py\n",
        changed_files=[],
        repo_path=repo,
    )
    return bundle


@pytest.fixture
def patched_pipeline(monkeypatch, fake_bundle):
    """Mock fetch_pr + the entire graph so we test the runner's plumbing."""
    captured = {}

    class _FakeCM:
        async def __aenter__(self):
            return fake_bundle

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_fetch_pr(pr_url, github_token=None, **kw):  # noqa: ARG001
        captured["fetch_pr_url"] = pr_url
        captured["fetch_token"] = github_token
        return _FakeCM()

    class _FakeGraph:
        async def ainvoke(self, state, config=None):  # noqa: ARG002
            captured["state"] = state
            captured["config"] = config
            return {
                "final_report": {
                    "pr_url": state["pr_url"],
                    "files_changed": len(state.get("changed_files", [])),
                    "summary": "ok",
                    "findings": [],
                    "agents_succeeded": ["security"],
                    "agents_failed": [],
                    "trace_id": state["trace_id"],
                }
            }

    def fake_build(*, fetch_node=None, aggregate_node=None, planner_node=None):  # noqa: ARG001
        captured["planner_override"] = planner_node is not None
        return _FakeGraph()

    monkeypatch.setattr(runner_mod, "fetch_pr", fake_fetch_pr)
    monkeypatch.setattr(runner_mod, "build_review_graph", fake_build)
    return captured


@pytest.mark.asyncio
async def test_run_review_seeds_state_and_returns_report(patched_pipeline):
    report = await run_review("https://github.com/x/y/pull/42", github_token="tk")

    assert report["pr_url"] == "https://github.com/x/y/pull/42"
    state = patched_pipeline["state"]
    assert state["pr_url"] == "https://github.com/x/y/pull/42"
    assert state["pr_metadata"]["author"] == "alice"
    assert state["trace_id"]
    assert "started_at" in state["pr_metadata"]
    assert patched_pipeline["fetch_token"] == "tk"
    assert patched_pipeline["planner_override"] is False  # no --only


@pytest.mark.asyncio
async def test_run_review_uses_planner_override_when_only_set(patched_pipeline):
    await run_review(
        "https://github.com/x/y/pull/42", only=["security", "quality"]
    )
    assert patched_pipeline["planner_override"] is True


@pytest.mark.asyncio
async def test_run_review_post_comments_skipped_without_token(
    patched_pipeline, monkeypatch, caplog
):
    posted = {"called": False}

    def fake_post(*a, **kw):  # noqa: ARG001
        posted["called"] = True

    monkeypatch.setattr(runner_mod, "_post_pr_comment", fake_post)

    await run_review("https://github.com/x/y/pull/42", post_comments=True)
    assert posted["called"] is False  # no token → skipped


@pytest.mark.asyncio
async def test_run_review_posts_pr_comment_when_token_present(
    patched_pipeline, monkeypatch
):
    captured: dict = {}

    def fake_post(pr_url, github_token, body):
        captured["url"] = pr_url
        captured["token"] = github_token
        captured["body"] = body

    monkeypatch.setattr(runner_mod, "_post_pr_comment", fake_post)

    await run_review(
        "https://github.com/x/y/pull/42",
        github_token="tk",
        post_comments=True,
    )
    assert captured["url"] == "https://github.com/x/y/pull/42"
    assert captured["token"] == "tk"
    assert captured["body"].startswith("## ⚠️ This is an automated comment")


@pytest.mark.asyncio
async def test_run_review_post_comment_failure_is_swallowed(
    patched_pipeline, monkeypatch
):
    def boom(*a, **kw):  # noqa: ARG001
        raise RuntimeError("network error")

    monkeypatch.setattr(runner_mod, "_post_pr_comment", boom)

    # Should not raise even though comment posting fails.
    report = await run_review(
        "https://github.com/x/y/pull/42",
        github_token="tk",
        post_comments=True,
    )
    assert report["pr_url"]


@pytest.mark.asyncio
async def test_run_review_attaches_langsmith_tags_when_enabled(
    patched_pipeline, monkeypatch
):
    """When LangSmith is configured, tags + metadata flow into graph.ainvoke."""
    monkeypatch.setenv("LANGCHAIN_API_KEY", "abc")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

    # Force the warn-once flag to fresh state so configure_tracing logs cleanly.
    from reviewer.observability import langsmith_setup

    langsmith_setup.reset_warning_for_tests()

    await run_review("https://github.com/x/y/pull/42")
    cfg = patched_pipeline["config"]
    assert cfg["run_name"] == "pr_review"
    # PR URL + author + repo are tagged
    assert any("pr_url:" in t for t in cfg["tags"])
    assert any("author:alice" in t for t in cfg["tags"])
    assert any("repo:x/y" in t for t in cfg["tags"])
    assert cfg["metadata"]["pr_author"] == "alice"


@pytest.mark.asyncio
async def test_run_review_no_langsmith_tags_when_disabled(
    patched_pipeline, monkeypatch
):
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)

    from reviewer.observability import langsmith_setup

    langsmith_setup.reset_warning_for_tests()

    await run_review("https://github.com/x/y/pull/42")
    cfg = patched_pipeline["config"]
    assert "tags" not in cfg
    assert "metadata" not in cfg
    assert cfg["run_name"] == "pr_review"


def test_format_comment_body_includes_bot_attribution():
    """The comment body must clearly disclose that it's bot-generated.

    GitHub attributes the comment to whoever owns the API token used to post
    it — usually the PR author. Without an explicit disclaimer in the body,
    a reader could mistake the comment for human-written feedback. We assert
    BOTH a header disclaimer (above the summary) AND a footer attribution
    are present.
    """
    report = {
        "summary": "looks fine",
        "files_changed": 1,
        "findings": [],
        "agents_succeeded": ["security"],
        "agents_failed": [],
        "trace_id": "abc-123",
    }
    body = _format_comment_body(report)
    first_line = body.splitlines()[0]

    # Default behaviour (no pr_author in report) → generic warning.
    assert first_line == "## ⚠️ This is an automated comment — NOT written by the PR author"

    # Sub-header still includes the bot attribution + project link.
    assert "🤖" in body
    assert "multi-agent-code-reviewer" in body
    assert "did not write any of" in body

    # Footer with trace ID for traceability.
    assert "Trace ID: `abc-123`" in body


def test_format_comment_body_handles_missing_trace_id():
    """Missing trace_id should render as 'n/a' rather than crash or print 'None'."""
    report = {
        "summary": "x",
        "files_changed": 0,
        "findings": [],
        "agents_succeeded": [],
        "agents_failed": [],
    }
    body = _format_comment_body(report)
    assert "Trace ID: `n/a`" in body
    assert "None" not in body  # specifically: no stringified None leaks through


def test_format_comment_body_handles_empty_summary():
    """An empty/missing summary must show a placeholder rather than a blank gap."""
    report = {
        "files_changed": 0,
        "findings": [],
        "agents_succeeded": [],
        "agents_failed": [],
    }
    body = _format_comment_body(report)
    assert "_(no summary generated)_" in body


def test_format_comment_body_uses_at_mention_when_author_known():
    """When ``pr_author`` is set, the warning must @mention them."""
    body = _format_comment_body(
        {
            "pr_author": "OmkumarSolanki",
            "summary": "x",
            "files_changed": 0,
            "findings": [],
            "agents_succeeded": [],
            "agents_failed": [],
        }
    )
    first_line = body.splitlines()[0]
    assert first_line == (
        "## ⚠️ This is an automated comment — NOT written by @OmkumarSolanki"
    )
