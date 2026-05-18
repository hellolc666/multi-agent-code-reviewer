"""Tests for the Typer CLI."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from reviewer import cli as cli_mod
from reviewer.cli import app


@pytest.fixture
def patched_run_review(monkeypatch):
    """Replace ``run_review`` (imported into ``cli`` namespace) with a stub."""
    captured: dict = {}

    async def fake_run_review(pr_url, *, github_token=None, only=None, post_comments=False):
        captured["pr_url"] = pr_url
        captured["github_token"] = github_token
        captured["only"] = only
        captured["post_comments"] = post_comments
        return {
            "pr_url": pr_url,
            "summary": "stub",
            "findings": [],
            "agents_succeeded": [],
            "agents_failed": [],
            "files_changed": 0,
        }

    monkeypatch.setattr(cli_mod, "run_review", fake_run_review)
    return captured


def test_cli_help_contains_review_subcommand():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "review" in result.stdout


def test_cli_review_writes_report_to_stdout(patched_run_review):
    runner = CliRunner()
    result = runner.invoke(
        app, ["review", "https://github.com/x/y/pull/1"]
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["pr_url"] == "https://github.com/x/y/pull/1"
    assert patched_run_review["only"] is None
    assert patched_run_review["post_comments"] is False


def test_cli_review_writes_report_to_file(patched_run_review, tmp_path):
    out = tmp_path / "report.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["review", "https://github.com/x/y/pull/1", "--output", str(out)],
    )
    assert result.exit_code == 0
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["pr_url"] == "https://github.com/x/y/pull/1"


def test_cli_review_only_parses_comma_list(patched_run_review):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "review",
            "https://github.com/x/y/pull/1",
            "--only", "security,quality",
        ],
    )
    assert result.exit_code == 0
    assert patched_run_review["only"] == ["security", "quality"]


def test_cli_review_post_comments_flag(patched_run_review):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["review", "https://github.com/x/y/pull/1", "--post-comments"],
    )
    assert result.exit_code == 0
    assert patched_run_review["post_comments"] is True


def test_cli_review_token_via_flag(patched_run_review, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "review",
            "https://github.com/x/y/pull/1",
            "--github-token", "abc123",
        ],
    )
    assert result.exit_code == 0
    assert patched_run_review["github_token"] == "abc123"


def test_cli_review_token_via_env(patched_run_review, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    runner = CliRunner()
    result = runner.invoke(
        app, ["review", "https://github.com/x/y/pull/1"]
    )
    assert result.exit_code == 0
    assert patched_run_review["github_token"] == "from-env"


def test_cli_review_handles_runner_exception(monkeypatch):
    async def boom(*a, **kw):  # noqa: ARG001
        raise RuntimeError("upstream broke")

    monkeypatch.setattr(cli_mod, "run_review", boom)

    runner = CliRunner()
    result = runner.invoke(app, ["review", "https://github.com/x/y/pull/1"])
    assert result.exit_code == 1
    assert "upstream broke" in result.stderr or "upstream broke" in result.stdout


def test_cli_review_missing_url():
    runner = CliRunner()
    result = runner.invoke(app, ["review"])
    assert result.exit_code != 0
