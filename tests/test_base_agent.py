"""Tests for ``reviewer.agents.base`` — LLM factory + structured-output wrapper."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from reviewer.agents import base as base_mod
from reviewer.agents.base import DEFAULT_MODEL, FindingList, analyze_with_schema, get_llm
from reviewer.models.findings import Category, Finding, Severity


def test_findinglist_default_empty():
    fl = FindingList()
    assert fl.findings == []


def test_findinglist_validates_findings():
    f = Finding(
        id="X-1",
        agent="security",
        category=Category.SECURITY,
        severity=Severity.HIGH,
        file_path="a.py",
        line_start=1,
        line_end=1,
        title="t",
        explanation="e",
        suggestion="s",
        confidence=0.9,
        tool_source=None,
    )
    fl = FindingList(findings=[f])
    assert fl.findings[0].id == "X-1"


def test_get_llm_uses_env_var(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-test-override")
    llm = get_llm()
    assert llm.model == "claude-test-override"


def test_get_llm_explicit_override(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "from-env")
    llm = get_llm(model="explicit-model")
    assert llm.model == "explicit-model"


def test_get_llm_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    llm = get_llm()
    assert llm.model == DEFAULT_MODEL


class _Echo(BaseModel):
    text: str


@pytest.mark.asyncio
async def test_analyze_with_schema_invokes_structured(monkeypatch):
    """``analyze_with_schema`` should bind the schema and ainvoke with messages."""
    captured: dict = {}

    class FakeStructured:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return _Echo(text="ok")

    class FakeLLM:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return FakeStructured()

    monkeypatch.setattr(base_mod, "get_llm", lambda: FakeLLM())

    result = await analyze_with_schema("hello", _Echo, system="be brief")

    assert isinstance(result, _Echo)
    assert result.text == "ok"
    assert captured["schema"] is _Echo
    msgs = captured["messages"]
    assert msgs[0] == ("system", "be brief")
    assert msgs[-1] == ("human", "hello")


@pytest.mark.asyncio
async def test_analyze_with_schema_no_system(monkeypatch):
    captured: dict = {}

    class FakeStructured:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return _Echo(text="ok")

    class FakeLLM:
        def with_structured_output(self, schema):
            return FakeStructured()

    await analyze_with_schema("just a prompt", _Echo, llm=FakeLLM())
    assert captured["messages"] == [("human", "just a prompt")]


@pytest.mark.asyncio
async def test_analyze_with_schema_uses_injected_llm(monkeypatch):
    """When ``llm=`` is passed, ``get_llm`` must NOT be called."""

    def fail_get_llm():
        raise AssertionError("get_llm should not be called")

    monkeypatch.setattr(base_mod, "get_llm", fail_get_llm)

    class FakeStructured:
        async def ainvoke(self, messages):
            return _Echo(text="injected")

    class FakeLLM:
        def with_structured_output(self, schema):
            return FakeStructured()

    out = await analyze_with_schema("p", _Echo, llm=FakeLLM())
    assert out.text == "injected"


# ---------------------------------------------------------------------------
# Provider routing
# ---------------------------------------------------------------------------


def _clear_llm_env(monkeypatch):
    for var in ("LLM_PROVIDER", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_get_llm_default_provider_is_anthropic(monkeypatch):
    """Unset LLM_PROVIDER → ChatAnthropic with the Anthropic default model."""
    _clear_llm_env(monkeypatch)
    llm = get_llm()
    assert type(llm).__name__ == "ChatAnthropic"
    assert llm.model == DEFAULT_MODEL


def test_get_llm_openai_provider_via_env(monkeypatch):
    """``LLM_PROVIDER=openai`` → ChatOpenAI with the OpenAI default model.

    ChatOpenAI exposes ``model_name`` (not ``model``); we assert on that.
    """
    from reviewer.agents.base import DEFAULT_OPENAI_MODEL

    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # avoid SDK init complaints
    llm = get_llm()
    assert type(llm).__name__ == "ChatOpenAI"
    assert llm.model_name == DEFAULT_OPENAI_MODEL


def test_get_llm_provider_kwarg_overrides_env(monkeypatch):
    """Explicit ``provider=`` beats the env var."""
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm = get_llm(provider="openai")
    assert type(llm).__name__ == "ChatOpenAI"


def test_get_llm_provider_is_case_insensitive(monkeypatch):
    """``LLM_PROVIDER=OpenAI`` resolves the same as ``openai``."""
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm = get_llm()
    assert type(llm).__name__ == "ChatOpenAI"


def test_get_llm_unsupported_provider_raises(monkeypatch):
    """Unknown provider → clear ValueError, not a missing-import error."""
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "cohere")
    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        get_llm()


def test_get_llm_openai_uses_llm_model_override(monkeypatch):
    """``LLM_MODEL`` overrides the provider default for OpenAI too."""
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-custom")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm = get_llm()
    assert llm.model_name == "gpt-custom"


def test_get_llm_anthropic_default_model_is_sonnet_46(monkeypatch):
    """Sanity check: the package-level Anthropic default is 4.6."""
    _clear_llm_env(monkeypatch)
    assert DEFAULT_MODEL == "claude-sonnet-4-6"
