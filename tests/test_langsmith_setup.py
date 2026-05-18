"""Tests for ``reviewer.observability.langsmith_setup``."""

from __future__ import annotations

import logging

from reviewer.observability import langsmith_setup
from reviewer.observability.langsmith_setup import (
    DEFAULT_PROJECT,
    configure_tracing,
    is_enabled,
)


def _clear_env(monkeypatch):
    for var in ("LANGCHAIN_API_KEY", "LANGCHAIN_TRACING_V2", "LANGCHAIN_PROJECT"):
        monkeypatch.delenv(var, raising=False)


def test_is_enabled_requires_both_key_and_flag(monkeypatch):
    _clear_env(monkeypatch)
    assert is_enabled() is False

    monkeypatch.setenv("LANGCHAIN_API_KEY", "abc")
    assert is_enabled() is False  # flag still missing

    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    assert is_enabled() is True


def test_is_enabled_accepts_alternative_truthy_values(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_API_KEY", "abc")
    for val in ("true", "TRUE", "1", "yes", "on"):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", val)
        assert is_enabled() is True
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    assert is_enabled() is False


def test_configure_tracing_disabled_warns_once(monkeypatch, caplog):
    _clear_env(monkeypatch)
    langsmith_setup.reset_warning_for_tests()

    with caplog.at_level(logging.WARNING, logger="reviewer.observability.langsmith_setup"):
        result1 = configure_tracing(pr_url="https://x.com/y/z/pull/1")
        result2 = configure_tracing(pr_url="https://x.com/y/z/pull/2")

    assert result1 == {"enabled": False}
    assert result2 == {"enabled": False}
    # Only ONE warning total (idempotent).
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "not set" in warnings[0].getMessage().lower()


def test_configure_tracing_warns_when_key_set_but_flag_off(monkeypatch, caplog):
    _clear_env(monkeypatch)
    monkeypatch.setenv("LANGCHAIN_API_KEY", "abc")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    langsmith_setup.reset_warning_for_tests()

    with caplog.at_level(logging.WARNING):
        result = configure_tracing()
    assert result["enabled"] is False
    assert any("not 'true'" in r.getMessage() for r in caplog.records)


def test_configure_tracing_enabled_sets_project_and_returns_metadata(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("LANGCHAIN_API_KEY", "abc")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    langsmith_setup.reset_warning_for_tests()

    out = configure_tracing(
        project=None,
        pr_url="https://github.com/x/y/pull/1",
        pr_author="alice",
        repo_name="x/y",
    )
    assert out["enabled"] is True
    assert out["project"] == DEFAULT_PROJECT
    assert "pr_url:https://github.com/x/y/pull/1" in out["tags"]
    assert "author:alice" in out["tags"]
    assert "repo:x/y" in out["tags"]
    assert out["metadata"]["pr_author"] == "alice"


def test_configure_tracing_respects_explicit_project(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("LANGCHAIN_API_KEY", "abc")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    langsmith_setup.reset_warning_for_tests()

    out = configure_tracing(project="custom-project")
    assert out["project"] == "custom-project"
