import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from reviewer import runner
from reviewer.agents.base import FindingList, analyze_with_schema
from reviewer.api import app
from reviewer.publication import PublicationError, post_comment, prepare_publication, publish_report
from reviewer.reliability.harness import (
    BudgetExceeded,
    ReviewBudget,
    RunHarness,
    current_run,
    observed_node,
)
from reviewer.tools import github_client
from reviewer.tools.diff_input import make_input


async def test_parallel_request_budget_and_semaphore():
    runtime = RunHarness(ReviewBudget(max_llm_requests=3, max_concurrent=2))
    active = peak = 0

    class Runnable:
        async def ainvoke(self, _messages):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.005)
            active -= 1
            return AIMessage(content="ok")

    results = await asyncio.gather(
        *(runtime.invoke(Runnable(), []) for _ in range(10)), return_exceptions=True
    )
    assert sum(isinstance(r, BudgetExceeded) for r in results) == 7
    assert runtime.requests == 3
    assert peak <= 2
    assert runtime.summary({})["input_tokens"] is None


async def test_usage_is_real_metadata_or_unknown():
    runtime = RunHarness(ReviewBudget())

    class Runnable:
        async def ainvoke(self, _messages):
            return {
                "raw": AIMessage(
                    content="",
                    usage_metadata={
                        "input_tokens": 7,
                        "output_tokens": 11,
                        "total_tokens": 18,
                    },
                ),
                "parsed": FindingList(findings=[]),
            }

    await runtime.invoke(Runnable(), [])
    summary = runtime.summary({"price_per_million_tokens": {"input": 2, "output": 4}})
    assert summary["input_tokens"] == 7
    assert summary["estimated_cost"] == pytest.approx(58 / 1_000_000)


async def test_llm_failure_cannot_be_hidden_by_legacy_seed_fallback(caplog, tmp_path):
    runtime = RunHarness(ReviewBudget())

    class Failing:
        metadata = {}

        def with_structured_output(self, _schema):
            return self

        async def ainvoke(self, _messages):
            raise RuntimeError("SECRET-PROMPT-AND-KEY")

    async def legacy(state):
        try:
            await analyze_with_schema("SENSITIVE-SOURCE", FindingList, llm=Failing())
        except RuntimeError:
            pass
        return {"agents_succeeded": ["security"], "findings": []}

    token = current_run.set(runtime)
    try:
        result = await observed_node(legacy, "security")({"repo_path": str(tmp_path)})
    finally:
        current_run.reset(token)
    assert result["agents_succeeded"] == []
    assert result["agents_failed"][0]["error"] == "RuntimeError"
    assert "SECRET-" not in caplog.text
    assert "SENSITIVE-SOURCE" not in str(runtime.events)


async def test_overall_timeout_retains_successes_and_candidates(tmp_path, monkeypatch):
    value, diff = make_input("", mode="diff")

    class SlowGraph:
        async def ainvoke(self, state, config=None):
            runtime = current_run.get()
            runtime.capture(
                {
                    "agents_to_run": ["quality_agent", "correctness_agent"],
                    "agents_succeeded": ["quality"],
                }
            )
            await asyncio.sleep(1)

    monkeypatch.setattr(runner, "build_review_graph", lambda **_kwargs: SlowGraph())
    from reviewer.reliability.harness import RunHarness

    runtime = RunHarness(ReviewBudget(total_seconds=0.02))
    result = await runner._execute_review(
        value, diff, tmp_path, options={"budget": {"total_seconds": 0.02}}, runtime=runtime
    )
    assert result["status"] == "partial"
    assert result["agents_succeeded"] == ["quality"]
    assert result["verification_incomplete"]
    assert any(f["agent"] == "correctness" for f in result["agents_failed"])


async def test_input_fetch_is_inside_total_deadline(monkeypatch):
    @asynccontextmanager
    async def slow_fetch(*_args, **_kwargs):
        await asyncio.sleep(1)
        yield None

    monkeypatch.setattr(runner, "fetch_pr", slow_fetch)
    result = await runner.run_review(
        "https://github.com/x/y/pull/1", options={"budget": {"total_seconds": 0.02}}
    )
    assert result["status"] == "failed"
    assert result["verification_incomplete"]


async def test_caller_cancellation_propagates(monkeypatch):
    started = asyncio.Event()

    @asynccontextmanager
    async def slow_fetch(*_args, **_kwargs):
        started.set()
        await asyncio.sleep(1)
        yield None

    monkeypatch.setattr(runner, "fetch_pr", slow_fetch)
    task = asyncio.create_task(runner.run_review("https://github.com/x/y/pull/1"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def valid_report():
    return {
        "input_mode": "github",
        "execution_mode": "live",
        "status": "completed",
        "judge_enabled": True,
        "verification_incomplete": False,
        "agents_failed": [],
        "head_sha": "a" * 40,
        "pr_url": "https://github.com/x/y/pull/1",
        "findings": [],
        "review_version": "2.0.0",
        "summary": "Checked review.",
    }


@pytest.mark.parametrize(
    "update",
    [
        {"execution_mode": "mock"},
        {"status": "partial"},
        {"head_sha": "not-a-sha"},
        {"judge_enabled": False},
        {"verification_incomplete": True},
        {"findings": [{"verification_status": "candidate"}]},
    ],
)
def test_unapproved_or_unverified_publishing_is_blocked(update):
    with pytest.raises(PublicationError):
        prepare_publication({**valid_report(), **update})


def test_preview_never_calls_github():
    def forbidden(*_args, **_kwargs):
        pytest.fail("preview must not access GitHub")

    result = publish_report(valid_report(), None, poster=forbidden)
    assert result["status"] == "preview"
    assert result["body"].startswith("<!-- multi-agent-pr-review:")


def test_head_sha_and_remote_idempotency():
    class Comment:
        html_url = "https://github.com/x/y/pull/1#issuecomment-1"

        def __init__(self, body):
            self.body = body
            self.edits = 0

        def edit(self, body):
            self.body = body
            self.edits += 1

    class Pull:
        head = SimpleNamespace(sha="a" * 40)

        def __init__(self):
            self.comments = []

        def get_issue_comments(self):
            return self.comments

        def create_issue_comment(self, body):
            comment = Comment(body)
            self.comments.append(comment)
            return comment

        def update(self):
            return False

    pull = Pull()
    github = SimpleNamespace(
        get_repo=lambda _name: SimpleNamespace(get_pull=lambda _num: pull), close=lambda: None
    )
    preview = prepare_publication(valid_report())

    def invoke(**options):
        return post_comment(
            valid_report()["pr_url"],
            "dummy",
            preview["body"],
            expected_head_sha="a" * 40,
            marker=preview["marker"],
            github_factory=lambda _token: github,
            **options,
        )

    assert invoke()["status"] == "published"
    assert invoke()["status"] == "already_published"
    assert len(pull.comments) == 1
    assert invoke(allow_update=True)["status"] == "updated"
    assert pull.comments[0].edits == 1
    pull.head.sha = "b" * 40
    with pytest.raises(PublicationError, match="head changed"):
        invoke()


def test_api_cannot_publish_with_body_flag():
    response = TestClient(app).post(
        "/review",
        json={
            "pr_url": "https://github.com/x/y/pull/1",
            "post_comments": True,
        },
    )
    assert response.status_code == 403


async def test_clone_pins_sha_without_credentials_in_argv(tmp_path, monkeypatch):
    calls = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def execute(*args, **kwargs):
        calls.append((args, kwargs))
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    await github_client._shallow_clone(
        "https://github.com/x/y.git", "branch", "SECRET-TOKEN", tmp_path, sha="a" * 40
    )
    assert len(calls) == 3
    assert "SECRET-TOKEN" not in str([c[0] for c in calls])
    assert "checkout" in calls[-1][0] and "a" * 40 in calls[-1][0]
    with pytest.raises(github_client.GitHubClientError):
        await github_client._shallow_clone("file:///host/repo", "branch", None, tmp_path)
