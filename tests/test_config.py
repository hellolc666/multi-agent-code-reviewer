"""Tests for ``reviewer.config``."""

from __future__ import annotations

from reviewer import config
from reviewer.config import (
    DEFAULT_LANGSMITH_PROJECT,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OPENAI_MODEL,
    SUPPORTED_LLM_PROVIDERS,
    snapshot,
)

_LLM_VARS = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GITHUB_TOKEN",
    "LANGCHAIN_API_KEY",
    "LANGCHAIN_PROJECT",
    "LANGCHAIN_TRACING_V2",
)


def _clear(monkeypatch):
    for var in _LLM_VARS:
        monkeypatch.delenv(var, raising=False)


def test_default_constants():
    assert DEFAULT_LLM_PROVIDER == "anthropic"
    assert DEFAULT_LLM_MODEL == "claude-sonnet-4-6"
    assert DEFAULT_OPENAI_MODEL == "gpt-5.5"
    assert SUPPORTED_LLM_PROVIDERS == ("anthropic", "openai")


def test_defaults_when_env_unset(monkeypatch):
    _clear(monkeypatch)
    assert config.llm_provider() == DEFAULT_LLM_PROVIDER
    assert config.llm_model() == DEFAULT_LLM_MODEL
    assert config.anthropic_api_key() is None
    assert config.openai_api_key() is None
    assert config.github_token() is None
    assert config.langsmith_api_key() is None
    assert config.langsmith_project() == DEFAULT_LANGSMITH_PROJECT
    assert config.langsmith_tracing_enabled() is False


def test_overrides_from_env(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-1")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "ls-1")
    monkeypatch.setenv("LANGCHAIN_PROJECT", "my-project")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

    s = snapshot()
    assert s.llm_provider == "openai"
    assert s.llm_model == "custom-model"
    assert s.anthropic_api_key == "sk-1"
    assert s.openai_api_key == "sk-oai"
    assert s.github_token == "ghp-1"
    assert s.langsmith_api_key == "ls-1"
    assert s.langsmith_project == "my-project"
    assert s.langsmith_tracing_enabled is True


def test_empty_string_treated_as_unset(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    assert config.anthropic_api_key() is None
    assert config.openai_api_key() is None
    assert config.github_token() is None


def test_tracing_flag_accepts_aliases(monkeypatch):
    _clear(monkeypatch)
    for val in ("true", "TRUE", "1", "yes", "on"):
        monkeypatch.setenv("LANGCHAIN_TRACING_V2", val)
        assert config.langsmith_tracing_enabled() is True

    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    assert config.langsmith_tracing_enabled() is False
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "")
    assert config.langsmith_tracing_enabled() is False


def test_snapshot_is_lazy(monkeypatch):
    """A second snapshot reflects env changes between calls."""
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "a")
    s1 = snapshot()
    monkeypatch.setenv("LLM_MODEL", "b")
    s2 = snapshot()
    assert s1.llm_model == "a"
    assert s2.llm_model == "b"


# ---------------------------------------------------------------------------
# Provider-aware llm_model() behaviour
# ---------------------------------------------------------------------------


def test_llm_provider_is_lowercased(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
    assert config.llm_provider() == "openai"


def test_llm_provider_empty_falls_back_to_default(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "   ")
    assert config.llm_provider() == DEFAULT_LLM_PROVIDER


def test_llm_model_default_for_openai_provider(monkeypatch):
    """Without LLM_MODEL, OpenAI provider gets gpt-5.5."""
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert config.llm_model() == DEFAULT_OPENAI_MODEL


def test_llm_model_default_for_anthropic_provider(monkeypatch):
    """Without LLM_MODEL, Anthropic provider gets claude-sonnet-4-6."""
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert config.llm_model() == DEFAULT_LLM_MODEL


def test_llm_model_explicit_overrides_provider_default(monkeypatch):
    """An explicit LLM_MODEL wins regardless of provider."""
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-custom")
    assert config.llm_model() == "gpt-custom"


def test_snapshot_includes_provider_and_openai_key(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-snap")
    s = snapshot()
    assert s.llm_provider == "openai"
    assert s.llm_model == DEFAULT_OPENAI_MODEL  # provider-default kicks in
    assert s.openai_api_key == "sk-oai-snap"


# ---------------------------------------------------------------------------
# agent_timeout() helper
# ---------------------------------------------------------------------------


def test_agent_timeout_returns_default_when_env_unset(monkeypatch):
    for label in ("security", "quality", "test_mapping", "docs"):
        monkeypatch.delenv(f"REVIEWER_{label.upper()}_TIMEOUT_S", raising=False)
    assert config.agent_timeout("security") == config.DEFAULT_AGENT_TIMEOUTS_S["security"]
    assert config.agent_timeout("quality") == config.DEFAULT_AGENT_TIMEOUTS_S["quality"]
    assert config.agent_timeout("test_mapping") == config.DEFAULT_AGENT_TIMEOUTS_S["test_mapping"]
    assert config.agent_timeout("docs") == config.DEFAULT_AGENT_TIMEOUTS_S["docs"]


def test_agent_timeout_explicit_default_wins_over_package_default(monkeypatch):
    monkeypatch.delenv("REVIEWER_SECURITY_TIMEOUT_S", raising=False)
    assert config.agent_timeout("security", default=15.0) == 15.0


def test_agent_timeout_env_override_takes_priority(monkeypatch):
    monkeypatch.setenv("REVIEWER_SECURITY_TIMEOUT_S", "180")
    assert config.agent_timeout("security") == 180.0
    # Even an explicit ``default`` is overridden by the env var.
    assert config.agent_timeout("security", default=15.0) == 180.0


def test_agent_timeout_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("REVIEWER_SECURITY_TIMEOUT_S", "not-a-number")
    assert config.agent_timeout("security", default=42.0) == 42.0


def test_agent_timeout_zero_or_negative_env_falls_back(monkeypatch):
    """Non-positive values are nonsensical for a timeout — fall back."""
    for bad in ("0", "-5", "-0.1"):
        monkeypatch.setenv("REVIEWER_QUALITY_TIMEOUT_S", bad)
        assert config.agent_timeout("quality", default=12.5) == 12.5


def test_agent_timeout_unknown_label_uses_safe_fallback(monkeypatch):
    """Unknown label without explicit default → 60s safety net."""
    monkeypatch.delenv("REVIEWER_NONESUCH_TIMEOUT_S", raising=False)
    assert config.agent_timeout("nonesuch") == 60.0


def test_default_agent_timeouts_are_bumped():
    """Sanity-check: defaults should be the post-2026-05-18 bumped values."""
    assert config.DEFAULT_AGENT_TIMEOUTS_S == {
        "security": 120.0,
        "quality": 90.0,
        "test_mapping": 60.0,
        "docs": 90.0,
    }
