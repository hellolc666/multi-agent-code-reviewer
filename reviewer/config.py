"""Centralized configuration.

Single place to read environment variables. Modules that need config can either
read directly from ``os.environ`` (legacy code paths still in place) or import
the typed accessors here. New code should prefer this module.

Reading is lazy — values are looked up on each call so tests can monkeypatch
``os.environ`` between calls without re-importing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# ----- LLM ----------------------------------------------------------------- #

# Defaults track ``reviewer.agents.base`` — kept in sync deliberately rather
# than imported to avoid a circular dependency (``base`` would otherwise import
# ``config`` and vice-versa).
DEFAULT_LLM_PROVIDER = "anthropic"
SUPPORTED_LLM_PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "qwen")
DEFAULT_LLM_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-5.5"


def llm_provider() -> str:
    """Active LLM provider in lowercase. Override via ``LLM_PROVIDER`` env var.

    Falls back to the default provider on unset / empty values; unsupported
    values are returned verbatim so the caller (typically
    ``reviewer.agents.base.get_llm``) can raise a clear ``ValueError`` at the
    point of use.
    """
    raw = os.environ.get("LLM_PROVIDER", "").strip().lower()
    return raw or DEFAULT_LLM_PROVIDER


def llm_model() -> str:
    """LLM model ID. Override via ``LLM_MODEL`` env var.

    When unset, returns the provider-appropriate default
    (``claude-sonnet-4-6`` for Anthropic, ``gpt-5.5`` for OpenAI).
    """
    if llm_provider() == "qwen":
        return _required_env("DASHSCOPE_MODEL")
    explicit = os.environ.get("LLM_MODEL", "").strip()
    if explicit:
        return explicit
    if llm_provider() == "openai":
        return DEFAULT_OPENAI_MODEL
    return DEFAULT_LLM_MODEL


def anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or None


def openai_api_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY") or None


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or value.startswith("<"):
        raise ValueError(f"{name} must be configured (non-empty, no placeholder)")
    return value


@dataclass(frozen=True)
class QwenConfig:
    api_key: str = field(repr=False)
    base_url: str
    model: str


def qwen_config(*, model: str | None = None) -> QwenConfig:
    """Require an explicit endpoint/model; never use an OpenAI key for DashScope."""
    key = _required_env("DASHSCOPE_API_KEY")
    url = _required_env("DASHSCOPE_BASE_URL")
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.port != 0
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and not any(c.isspace() for c in url)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "DASHSCOPE_BASE_URL must be an HTTPS URL without credentials/query/fragment"
        )
    resolved_model = model.strip() if model is not None else _required_env("DASHSCOPE_MODEL")
    if not resolved_model or resolved_model.startswith("<"):
        raise ValueError("DASHSCOPE_MODEL must be configured (non-empty, no placeholder)")
    return QwenConfig(api_key=key, base_url=url.rstrip("/"), model=resolved_model)


def structured_output_retries() -> int:
    """At most two repair requests, shared by native and plain-JSON modes."""
    raw = os.environ.get("REVIEWER_JSON_REPAIR_RETRIES", "2").strip()
    try:
        retries = int(raw)
    except ValueError:
        raise ValueError("REVIEWER_JSON_REPAIR_RETRIES must be an integer from 0 to 2") from None
    if not 0 <= retries <= 2:
        raise ValueError("REVIEWER_JSON_REPAIR_RETRIES must be an integer from 0 to 2")
    return retries


# ----- GitHub -------------------------------------------------------------- #


def github_token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or None


# ----- LangSmith ----------------------------------------------------------- #

DEFAULT_LANGSMITH_PROJECT = "multi-agent-code-reviewer"


def langsmith_api_key() -> str | None:
    return os.environ.get("LANGCHAIN_API_KEY") or None


def langsmith_project() -> str:
    return os.environ.get("LANGCHAIN_PROJECT", DEFAULT_LANGSMITH_PROJECT)


def langsmith_tracing_enabled() -> bool:
    return os.environ.get("LANGCHAIN_TRACING_V2", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# ----- Per-agent timeouts -------------------------------------------------- #

# Default per-agent timeouts (seconds). These are upper bounds; agents finish
# much faster on typical PRs. Set generously enough that the LLM retry stack
# (3 attempts on transient 5xx) doesn't blow the budget on slow days.
DEFAULT_AGENT_TIMEOUTS_S: dict[str, float] = {
    "security": 120.0,  # Semgrep + LLM with full diff
    "quality": 90.0,  # Ruff + LLM
    "test_mapping": 60.0,  # AST walk + LLM (no Semgrep / Ruff)
    "docs": 90.0,  # AST walk + LLM (potentially many entities)
}


def agent_timeout(label: str, default: float | None = None) -> float:
    """Resolved per-agent timeout in seconds.

    Reads ``REVIEWER_<LABEL>_TIMEOUT_S`` (e.g. ``REVIEWER_SECURITY_TIMEOUT_S``);
    falls back to ``default`` (when given) or to the package-level default in
    :data:`DEFAULT_AGENT_TIMEOUTS_S`. Invalid / non-positive values fall back
    to the default with a warning logged at the call site so misconfiguration
    is loud.

    ``label`` matches the short agent label
    (``security`` / ``quality`` / ``test_mapping`` / ``docs``), not the node
    name. The env var is upper-cased.
    """
    fallback = default if default is not None else DEFAULT_AGENT_TIMEOUTS_S.get(label, 60.0)
    raw = os.environ.get(f"REVIEWER_{label.upper()}_TIMEOUT_S", "").strip()
    if not raw:
        return fallback
    try:
        v = float(raw)
    except ValueError:
        return fallback
    return v if v > 0 else fallback


# ----- Snapshot ------------------------------------------------------------ #


@dataclass(frozen=True)
class Config:
    """Snapshot of all env-derived configuration at a single point in time."""

    llm_provider: str
    llm_model: str
    anthropic_api_key: str | None = field(repr=False)
    openai_api_key: str | None = field(repr=False)
    github_token: str | None = field(repr=False)
    langsmith_api_key: str | None = field(repr=False)
    langsmith_project: str
    langsmith_tracing_enabled: bool
    dashscope_api_key: str | None = field(default=None, repr=False)
    dashscope_base_url: str | None = field(default=None, repr=False)
    dashscope_model: str | None = None


def snapshot() -> Config:
    """Capture the current env state. Useful for logging the active config."""
    return Config(
        llm_provider=llm_provider(),
        llm_model=llm_model(),
        anthropic_api_key=anthropic_api_key(),
        openai_api_key=openai_api_key(),
        github_token=github_token(),
        langsmith_api_key=langsmith_api_key(),
        langsmith_project=langsmith_project(),
        langsmith_tracing_enabled=langsmith_tracing_enabled(),
        dashscope_api_key=os.environ.get("DASHSCOPE_API_KEY") or None,
        dashscope_base_url=os.environ.get("DASHSCOPE_BASE_URL") or None,
        dashscope_model=os.environ.get("DASHSCOPE_MODEL") or None,
    )
