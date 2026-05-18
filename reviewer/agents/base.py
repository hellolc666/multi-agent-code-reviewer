"""Shared agent plumbing.

Centralizes LLM client construction so every specialist agent and the
aggregator share the same model, temperature, and reliability wrapping.

Two providers are supported, selected via the ``LLM_PROVIDER`` env var:

- ``anthropic`` (default) — ``langchain_anthropic.ChatAnthropic``.
  Default model: ``claude-sonnet-4-6`` (current Sonnet generation, May 2026).
- ``openai`` — ``langchain_openai.ChatOpenAI``.
  Default model: ``gpt-5.5`` (released 2026-04-23, available in
  Chat Completions + Responses APIs).

Override the model with the ``LLM_MODEL`` env var (applies to whichever
provider is active). Provider SDKs are imported lazily inside :func:`get_llm`
so an unused provider never has to be imported even though both ship as
runtime deps.

``analyze_with_schema`` is the high-level entry point agents use: it binds a
Pydantic schema via ``with_structured_output`` and ``ainvoke`` s the prompt,
returning the validated model. The underlying LLM call is wrapped with
exponential-backoff retry plus a per-process circuit breaker so transient
provider errors don't propagate. Tests monkeypatch ``get_llm`` (or pass
``llm=`` directly) to inject canned responses without touching the network.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, Field

from reviewer.models.findings import Finding
from reviewer.reliability.circuit_breaker import CircuitBreaker
from reviewer.reliability.retry import async_retry

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

# ---------------------------------------------------------------------------
# Provider + model defaults
# ---------------------------------------------------------------------------

# Anthropic default. Kept as ``DEFAULT_MODEL`` (no rename) so existing imports
# in tests and downstream modules don't break.
DEFAULT_MODEL = "claude-sonnet-4-6"

# OpenAI default. ``gpt-5.5`` is OpenAI's flagship as of 2026-04-23, available
# through both the Chat Completions and Responses APIs (langchain-openai
# routes appropriately).
DEFAULT_OPENAI_MODEL = "gpt-5.5"

DEFAULT_PROVIDER = "anthropic"
SUPPORTED_PROVIDERS: tuple[str, ...] = ("anthropic", "openai")

DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 4096


class FindingList(BaseModel):
    """Wrapper schema for ``with_structured_output``.

    LangChain's structured-output API works most reliably with a single
    top-level Pydantic model; wrapping the list in a field keeps the JSON-schema
    shape simple and makes the LLM contract obvious.
    """

    findings: list[Finding] = Field(
        default_factory=list, description="All findings emitted by this agent."
    )


_T = TypeVar("_T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------


def _resolve_provider(explicit: str | None = None) -> str:
    """Return the active provider name in lowercase.

    Resolution order: explicit argument > ``LLM_PROVIDER`` env var > default.
    Raises ``ValueError`` for unsupported providers so misconfiguration fails
    fast with a clear message instead of bubbling up as a missing-import error.
    """
    raw = explicit if explicit is not None else os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER)
    provider = (raw or DEFAULT_PROVIDER).strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported LLM_PROVIDER {provider!r}. "
            f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}."
        )
    return provider


def _default_model_for(provider: str) -> str:
    """Per-provider default model ID."""
    if provider == "openai":
        return DEFAULT_OPENAI_MODEL
    return DEFAULT_MODEL  # anthropic


def get_llm(
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> BaseChatModel:
    """Build a chat LLM client for the active provider.

    Parameters
    ----------
    provider : optional override; otherwise read from ``LLM_PROVIDER`` env var.
        Currently ``"anthropic"`` (default) or ``"openai"``.
    model : optional override; otherwise ``LLM_MODEL`` env var, otherwise the
        provider-specific default (``DEFAULT_MODEL`` / ``DEFAULT_OPENAI_MODEL``).
    temperature, max_tokens : passed through to the underlying client.

    The provider SDK is imported lazily so users who pick one provider don't
    incur the import cost of the other. API keys are not validated here — the
    underlying SDK validates them on first request, so this function is safe
    to call from tests without setting any credentials.
    """
    resolved_provider = _resolve_provider(provider)
    resolved_model = model or os.environ.get("LLM_MODEL") or _default_model_for(resolved_provider)

    if resolved_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=resolved_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if resolved_provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=resolved_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    # _resolve_provider already validated, but be defensive in case the
    # supported set is extended without updating this branch.
    raise ValueError(f"Unsupported provider {resolved_provider!r}")


# ---------------------------------------------------------------------------
# Structured-output wrapper
# ---------------------------------------------------------------------------


async def analyze_with_schema(
    prompt: str,
    schema: type[_T],
    *,
    llm: BaseChatModel | None = None,
    system: str | None = None,
) -> _T:
    """Run ``prompt`` through the LLM and validate the response against ``schema``.

    Wrapped with retry + circuit-breaker on the underlying LLM call so transient
    provider 5xx errors don't propagate to the agent (which would record a
    failure). Cancellation always passes through (see ``async_retry`` docs).

    Parameters
    ----------
    prompt: full user-message prompt; agents interpolate Semgrep/Ruff/AST data here.
    schema: Pydantic model class (typically ``FindingList`` or a subclass).
    llm: optional injected client. ``None`` builds the default via ``get_llm``,
        which honours ``LLM_PROVIDER`` / ``LLM_MODEL``.
    system: optional system prompt. Sent as a separate ``SystemMessage``-equivalent
        when provided.

    Returns the validated schema instance.
    """
    client = llm or get_llm()
    structured = client.with_structured_output(schema)
    messages: list[tuple[str, str]] = []
    if system:
        messages.append(("system", system))
    messages.append(("human", prompt))
    return await _resilient_invoke(structured, messages)


# Module-level breaker for the LLM endpoint. 5 failures / 60s → OPEN 30s.
# One breaker covers both providers — if the active provider is degraded, we
# don't want a single review to retry it forever.
_LLM_BREAKER = CircuitBreaker(
    name="llm",
    failure_threshold=5,
    window_seconds=60.0,
    open_seconds=30.0,
    failure_exceptions=(Exception,),
)


@async_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
async def _resilient_invoke(structured, messages):
    """Single retryable + breakered invocation against the LLM."""
    return await _LLM_BREAKER.call(structured.ainvoke, messages)
