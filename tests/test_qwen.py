"""Qwen compatibility regression tests. No live model/network calls."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from openai import APIStatusError, BadRequestError

from reviewer import config
from reviewer import runner as runner_mod
from reviewer.agents import base
from reviewer.agents import quality as quality_mod
from reviewer.agents import security as security_mod
from reviewer.agents.base import FindingList, StructuredOutputError, analyze_with_schema, get_llm
from reviewer.graph.aggregator import _SummaryResponse
from reviewer.reliability.circuit_breaker import CircuitBreaker, CircuitState
from reviewer.tools.github_client import PRBundle, PRFile, PRMetadata
from scripts import smoke_qwen


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    for key in (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "DASHSCOPE_MODEL",
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "REVIEWER_JSON_REPAIR_RETRIES",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        base,
        "_LLM_BREAKER",
        CircuitBreaker(
            name="test_llm",
            failure_exceptions=(base._TransientLLMError,),
        ),
    )


@pytest.fixture
def qwen_settings(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dummy-dashscope-secret")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://workspace.example.test/compatible-mode/v1/")
    monkeypatch.setenv("DASHSCOPE_MODEL", "configured-qwen-model")


def status_error(status, body):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.test/v1"))
    error_type = BadRequestError if status == 400 else APIStatusError
    return error_type("test error", response=response, body=body)


class FakeQwen:
    metadata = {"reviewer_provider": "qwen"}

    def __init__(self, native=None, raw=None, bind_error=None):
        self.native = list(native or [])
        self.raw = list(raw or [])
        self.bind_error = bind_error
        self.native_calls = []
        self.raw_calls = []

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema
        assert kwargs == {"method": "function_calling"}
        if self.bind_error:
            raise self.bind_error
        parent = self

        class Structured:
            async def ainvoke(self, messages):
                parent.native_calls.append(messages)
                return parent._next(parent.native)

        return Structured()

    @staticmethod
    def _next(queue):
        value = queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def ainvoke(self, messages):
        self.raw_calls.append(messages)
        return self._next(self.raw)


def test_qwen_client_uses_only_dashscope_settings(qwen_settings, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-openai-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unrelated.example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "unrelated-legacy-model")
    llm = get_llm()
    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "configured-qwen-model"
    assert llm.openai_api_base == "https://workspace.example.test/compatible-mode/v1"
    assert llm.openai_api_key.get_secret_value() == "dummy-dashscope-secret"
    assert llm.max_retries == 0
    assert config.llm_model() == llm.model_name
    assert "dummy-dashscope-secret" not in repr(config.qwen_config())


def test_explicit_qwen_overrides_provider_and_model(qwen_settings, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("DASHSCOPE_MODEL")
    assert get_llm(provider="QWEN", model="explicit-model").model_name == "explicit-model"


@pytest.mark.parametrize("key", ["DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "DASHSCOPE_MODEL"])
@pytest.mark.parametrize("value", [None, "", "   ", "<placeholder>"])
def test_required_qwen_env(qwen_settings, monkeypatch, key, value):
    if value is None:
        monkeypatch.delenv(key)
    else:
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        get_llm()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/v1",
        "file:///tmp/test",
        "https://",
        "not-a-url",
        "https://secret@example.test/v1",
        "https://example.test/v1?api_key=secret",
        "https://example.test/v1#secret",
        "https://example.test:wrong/v1",
        "https://exa mple.test/v1",
    ],
)
def test_invalid_endpoint_is_rejected_without_echoing_secrets(qwen_settings, monkeypatch, url):
    monkeypatch.setenv("DASHSCOPE_BASE_URL", url)
    with pytest.raises(ValueError, match="DASHSCOPE_BASE_URL") as exc:
        get_llm()
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("value", ["-1", "3", "wrong", "", "1.5"])
def test_invalid_repair_budget(monkeypatch, value):
    monkeypatch.setenv("REVIEWER_JSON_REPAIR_RETRIES", value)
    with pytest.raises(ValueError, match="REVIEWER_JSON_REPAIR_RETRIES"):
        config.structured_output_retries()


def test_snapshot_hides_credentials(qwen_settings, monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "LANGCHAIN_API_KEY"):
        monkeypatch.setenv(name, "hidden-" + name)
    value = config.snapshot()
    assert value.dashscope_model == "configured-qwen-model"
    assert value.dashscope_api_key == "dummy-dashscope-secret"
    assert "secret" not in repr(value)
    assert "hidden-" not in repr(value)


async def test_native_success_does_not_use_fallback():
    llm = FakeQwen(native=[{"findings": []}])
    result = await analyze_with_schema("diff", FindingList, llm=llm)
    assert result.findings == []
    assert len(llm.native_calls) == 1
    assert llm.raw_calls == []


@pytest.mark.parametrize(
    "schema,payload",
    [
        (FindingList, {"findings": []}),
        (_SummaryResponse, {"summary": "No blocking defect identified."}),
    ],
)
async def test_explicit_capability_failure_falls_back_to_validated_json(schema, payload):
    exc = status_error(
        400,
        {
            "error": {
                "param": "tools",
                "code": "unsupported_parameter",
                "message": "tools not supported",
            }
        },
    )
    llm = FakeQwen(native=[exc], raw=[AIMessage(content=json.dumps(payload))])
    result = await analyze_with_schema("diff", schema, llm=llm, system="review carefully")
    assert isinstance(result, schema)
    assert len(llm.native_calls) == len(llm.raw_calls) == 1
    assert "JSON schema" in llm.raw_calls[0][0][1]
    assert llm.raw_calls[0][-1] == ("human", "diff")
    assert base._LLM_BREAKER.state == CircuitState.CLOSED


async def test_bind_capability_failure_and_fenced_json():
    llm = FakeQwen(bind_error=NotImplementedError(), raw=['```json\n{"findings": []}\n```'])
    result = await analyze_with_schema("diff", FindingList, llm=llm)
    assert result.findings == []
    assert len(llm.raw_calls) == 1


@pytest.mark.parametrize(
    "status,body",
    [
        (401, {"message": "tools not supported"}),
        (403, {}),
        (404, {}),
        (400, {"error": {"code": "invalid_api_key", "message": "bad key"}}),
        (400, {"error": {"param": "tools", "message": "invalid JSON schema"}}),
        (400, {"message": "model does not exist"}),
    ],
)
async def test_operational_errors_never_enable_fallback_or_retry(status, body):
    error = status_error(status, body)
    llm = FakeQwen(native=[error])
    with pytest.raises(APIStatusError) as caught:
        await analyze_with_schema("diff", FindingList, llm=llm)
    assert caught.value is error
    assert len(llm.native_calls) == 1
    assert llm.raw_calls == []


async def test_invalid_native_output_is_repaired_without_fallback():
    llm = FakeQwen(native=[None, {"findings": []}])
    assert (await analyze_with_schema("diff", FindingList, llm=llm)).findings == []
    assert len(llm.native_calls) == 2
    assert llm.raw_calls == []


async def test_structured_missing_findings_is_not_fake_success():
    llm = FakeQwen(native=[{}, {}, {}])
    with pytest.raises(StructuredOutputError, match="budget exhausted"):
        await analyze_with_schema("diff", FindingList, llm=llm)
    assert len(llm.native_calls) == 3


async def test_native_parser_default_is_not_accepted_as_model_output():
    llm = FakeQwen(native=[FindingList(), FindingList(), FindingList()])
    with pytest.raises(StructuredOutputError):
        await analyze_with_schema("diff", FindingList, llm=llm)
    assert len(llm.native_calls) == 3


@pytest.mark.parametrize(
    "invalid",
    [
        "not JSON",
        "{}",
        '{"findings": null}',
        '{"findings": [{"severity": "made-up"}]}',
        '{"summary": ""}',
    ],
)
async def test_invalid_json_exhausts_bounded_budget(invalid):
    llm = FakeQwen(bind_error=NotImplementedError(), raw=[invalid] * 3)
    with pytest.raises(StructuredOutputError, match="budget exhausted"):
        await analyze_with_schema("diff", FindingList, llm=llm)
    assert len(llm.raw_calls) == 3


async def test_json_repair_can_succeed_and_does_not_log_content(caplog):
    llm = FakeQwen(
        bind_error=NotImplementedError(), raw=["PRIVATE-SOURCE-OR-KEY", '{"findings": []}']
    )
    assert (await analyze_with_schema("PRIVATE-PROMPT", FindingList, llm=llm)).findings == []
    assert len(llm.raw_calls) == 2
    assert "PRIVATE-" not in caplog.text


async def test_repair_budget_shared_across_modes():
    llm = FakeQwen(native=[{}, NotImplementedError()], raw=["bad JSON", '{"findings": []}'])
    result = await analyze_with_schema("diff", FindingList, llm=llm)
    assert result.findings == []
    assert len(llm.native_calls) + len(llm.raw_calls) == 4


async def test_zero_repairs(monkeypatch):
    monkeypatch.setenv("REVIEWER_JSON_REPAIR_RETRIES", "0")
    llm = FakeQwen(native=[None])
    with pytest.raises(StructuredOutputError):
        await analyze_with_schema("diff", FindingList, llm=llm)
    assert len(llm.native_calls) == 1


@pytest.mark.parametrize("error", [TimeoutError(), status_error(429, {}), status_error(503, {})])
async def test_transient_failure_retries_three_times_without_fallback(monkeypatch, error):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    llm = FakeQwen(native=[error] * 3)
    with pytest.raises(type(error)):
        await analyze_with_schema("diff", FindingList, llm=llm)
    assert len(llm.native_calls) == 3
    assert llm.raw_calls == []


async def test_cancellation_is_not_retried():
    llm = FakeQwen(native=[asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await analyze_with_schema("diff", FindingList, llm=llm)
    assert len(llm.native_calls) == 1
    assert not llm.raw_calls


async def test_actual_langchain_sdk_tool_response_parses_without_network():
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        name = payload["tools"][0]["function"]["name"]
        result = {"findings": []} if name == "FindingList" else {"summary": "Reviewed candidates."}
        return httpx.Response(
            200,
            json={
                "id": "mock",
                "object": "chat.completion",
                "model": "mock-qwen",
                "created": 0,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(result),
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        llm = ChatOpenAI(
            model="mock-qwen",
            api_key="dummy",
            base_url="https://example.test/v1",
            http_async_client=transport,
            max_retries=0,
            metadata={"reviewer_provider": "qwen"},
        )
        assert isinstance(await analyze_with_schema("diff", FindingList, llm=llm), FindingList)
        assert isinstance(
            await analyze_with_schema("summary", _SummaryResponse, llm=llm), _SummaryResponse
        )
    assert len(requests) == 2


async def test_actual_sdk_rejected_tools_fall_back_to_plain_json():
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if "tools" in payload:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "unsupported_parameter",
                        "param": "tools",
                        "message": "tools unsupported",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "mock",
                "object": "chat.completion",
                "model": "mock-qwen",
                "created": 0,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"findings": []}',
                        },
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        llm = ChatOpenAI(
            model="mock-qwen",
            api_key="dummy",
            base_url="https://example.test/v1",
            http_async_client=transport,
            max_retries=0,
            metadata={"reviewer_provider": "qwen"},
        )
        assert (await analyze_with_schema("diff", FindingList, llm=llm)).findings == []
    assert len(requests) == 2
    assert "tools" not in requests[1]
    assert "response_format" not in requests[1]


async def test_qwen_runner_end_to_end_with_mock_http_and_fixture(monkeypatch):
    """Real shared runner/graph/agents/SDK, only external services are replaced."""
    fixture = Path(__file__).parent / "fixtures" / "sample_repo"
    calls = []

    @asynccontextmanager
    async def fake_fetch(url, **_kwargs):
        yield PRBundle(
            pr_url=url,
            metadata=PRMetadata(
                owner="fixture",
                repo="repo",
                number=1,
                title="Offline fixture",
                author="test",
                head_sha="mock-head",
                head_ref="test",
                base_ref="main",
                head_repo_clone_url="https://example.test/unused.git",
            ),
            pr_diff="diff --git a/app/auth.py b/app/auth.py\n",
            changed_files=[PRFile(path="app/auth.py", change_type="modified")],
            repo_path=fixture,
        )

    async def empty_static(*_args, **_kwargs):
        return []

    def forbidden_post(*_args, **_kwargs):
        pytest.fail("default runner must never post GitHub comments")

    def respond(request):
        payload = json.loads(request.content)
        name = payload["tools"][0]["function"]["name"]
        calls.append(name)
        value = {
            "FindingList": {"findings": []},
            "EvidenceTurn": {"requests": []},
            "_SummaryResponse": {"summary": "Offline mock report."},
        }[name]
        return httpx.Response(
            200,
            json={
                "id": "mock",
                "object": "chat.completion",
                "model": "mock",
                "created": 0,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(value)},
                                }
                            ],
                        },
                    }
                ],
            },
        )

    monkeypatch.setattr(runner_mod, "fetch_pr", fake_fetch)
    monkeypatch.setattr(runner_mod, "_post_pr_comment", forbidden_post)
    monkeypatch.setattr(security_mod, "run_semgrep", empty_static)
    monkeypatch.setattr(quality_mod, "run_ruff", empty_static)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        llm = ChatOpenAI(
            model="mock",
            api_key="dummy",
            base_url="https://example.test/v1",
            http_async_client=transport,
            max_retries=0,
            metadata={"reviewer_provider": "qwen"},
        )
        monkeypatch.setattr(base, "get_llm", lambda: llm)
        report = await runner_mod.run_review("https://github.com/fixture/repo/pull/1")
    assert set(report["agents_succeeded"]) == {
        "security",
        "quality",
        "test_mapping",
        "docs",
        "correctness",
    }
    assert report["agents_failed"] == []
    assert report["summary"].startswith("No confirmed findings.")
    assert report["raw_findings"]
    assert report["findings"] == []
    assert calls.count("FindingList") == 5
    assert calls.count("_SummaryResponse") == 0
    assert calls.count("EvidenceTurn") == 2


def test_smoke_without_live_never_constructs_client(monkeypatch, capsys):
    def forbidden(**_kwargs):
        pytest.fail("must not construct a client without --live")

    monkeypatch.setattr(smoke_qwen, "get_llm", forbidden)
    assert smoke_qwen.main([]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_RUN"


def test_smoke_live_failure_is_explicit_and_sanitized(monkeypatch, capsys):
    def forbidden(**_kwargs):
        raise ValueError("secret-key-and-source")

    monkeypatch.setattr(smoke_qwen, "get_llm", forbidden)
    assert smoke_qwen.main(["--live"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "FAIL"
    assert "secret-key-and-source" not in output


async def test_smoke_uses_both_shared_schemas(monkeypatch):
    requested = []

    async def fake_analyze(_prompt, schema, **_kwargs):
        requested.append(schema)
        return FindingList() if schema is FindingList else _SummaryResponse(summary="Mock only.")

    monkeypatch.setattr(smoke_qwen, "get_llm", lambda **_kwargs: SimpleNamespace(model_name="fake"))
    monkeypatch.setattr(smoke_qwen, "analyze_with_schema", fake_analyze)
    result = await smoke_qwen.smoke("diff")
    assert requested == [FindingList, _SummaryResponse]
    assert result["usage"] is None
