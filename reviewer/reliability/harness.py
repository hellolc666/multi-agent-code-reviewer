"""Per-run concurrency, request budgets and sanitized trace/usage accounting."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)
current_run = ContextVar("current_review_run", default=None)
current_agent = ContextVar("current_review_agent", default="pipeline")


class BudgetExceeded(RuntimeError):
    pass


class ReviewBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_reviewers: int = Field(default=5, ge=1, le=5)
    max_llm_requests: int = Field(default=30, ge=1, le=100)
    max_concurrent: int = Field(default=3, ge=1, le=5)
    max_tool_calls: int = Field(default=6, ge=0, le=6)
    max_tool_rounds: int = Field(default=2, ge=0, le=3)
    max_tool_characters: int = Field(default=8192, ge=256, le=8192)
    max_context_characters: int = Field(default=64000, ge=256, le=96000)
    total_seconds: float = Field(default=180.0, gt=0, le=900)
    call_seconds: float = Field(default=30.0, gt=0, le=120)


class RunHarness:
    def __init__(self, budget: ReviewBudget, *, run_id=None):
        self.budget = budget
        self.run_id = run_id or str(uuid.uuid4())
        self.started = time.perf_counter()
        self.semaphore = asyncio.Semaphore(budget.max_concurrent)
        self.lock = asyncio.Lock()
        self.requests = 0
        self.tool_calls = {}
        self.events = []
        self.failures = []
        self.snapshot = {}
        self.exhausted = False

    def remaining(self):
        return max(0.0, self.budget.total_seconds - (time.perf_counter() - self.started))

    def event(self, kind, status, *, agent=None, duration=None, usage=None, **metadata):
        value = {
            "run_id": self.run_id,
            "trace_id": self.run_id,
            "agent": agent or current_agent.get(),
            "kind": kind,
            "status": status,
            "duration_seconds": duration,
            "usage": usage,
            **metadata,
        }
        self.events.append(value)
        # Never log messages, arguments, results, code or exception messages.
        log.info("review_event %s", value)

    def fail(self, stage, error):
        value = {"agent": current_agent.get(), "stage": stage, "error": type(error).__name__}
        if value not in self.failures:
            self.failures.append(value)
        if isinstance(error, BudgetExceeded):
            self.exhausted = True
            self.event("budget", "budget_exhausted", reason=stage)

    async def invoke(self, runnable, messages):
        async with self.semaphore:
            async with self.lock:
                if self.requests >= self.budget.max_llm_requests or self.remaining() <= 0:
                    raise BudgetExceeded("llm_request_or_time_budget")
                self.requests += 1
                request = self.requests
            started = time.perf_counter()
            try:
                async with asyncio.timeout(min(self.budget.call_seconds, self.remaining())):
                    result = await runnable.ainvoke(messages)
            except BaseException as exc:
                self.event(
                    "llm",
                    "cancelled" if isinstance(exc, asyncio.CancelledError) else "error",
                    duration=time.perf_counter() - started,
                    error_type=type(exc).__name__,
                    request=request,
                )
                raise
            self.event(
                "llm",
                "completed",
                duration=time.perf_counter() - started,
                request=request,
                usage=self._usage(result),
            )
            return result

    @staticmethod
    def _usage(result):
        message = result.get("raw") if isinstance(result, dict) and "raw" in result else result
        usage = getattr(message, "usage_metadata", None)
        if not usage:
            usage = (getattr(message, "response_metadata", {}) or {}).get("token_usage")
        if not isinstance(usage, dict):
            return None
        input_count = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_count = usage.get("output_tokens", usage.get("completion_tokens"))
        if not isinstance(input_count, int) or not isinstance(output_count, int):
            return None
        return {"input_tokens": input_count, "output_tokens": output_count}

    def summary(self, options):
        calls = [e for e in self.events if e["kind"] == "llm"]
        known = [e["usage"] for e in calls if e.get("usage") is not None]
        complete = bool(calls) and len(known) == len(calls)
        inputs = sum(u["input_tokens"] for u in known) if complete else None
        outputs = sum(u["output_tokens"] for u in known) if complete else None
        prices = options.get("price_per_million_tokens", {})
        cost = None
        if complete and all(
            isinstance(prices.get(k), (int, float)) and prices[k] >= 0 for k in ("input", "output")
        ):
            cost = (inputs * prices["input"] + outputs * prices["output"]) / 1_000_000
        return {
            "llm_requests": self.requests,
            "input_tokens": inputs,
            "output_tokens": outputs,
            "unknown_usage_requests": len(calls) - len(known),
            "estimated_cost": cost,
            "cost_method": "actual tokens * configured per-million rates"
            if cost is not None
            else "unknown: missing real usage or configured prices",
            "elapsed_seconds": round(time.perf_counter() - self.started, 6),
        }

    def capture(self, patch):
        for key, value in patch.items():
            if key in {
                "findings",
                "agents_succeeded",
                "agents_failed",
                "tool_evidence",
                "budget_events",
            }:
                self.snapshot.setdefault(key, []).extend(value)
            elif key != "final_report":
                self.snapshot[key] = value


def observed_node(node, label):
    @wraps(node)
    async def wrapped(state):
        token = current_agent.set(label)
        runtime = current_run.get()
        started = time.perf_counter()
        try:
            patch = await node(state)
            if runtime:
                normalized = []
                for finding in patch.get("findings", []):
                    path = finding.file_path
                    candidate_path = Path(path)
                    root = Path(state["repo_path"]).resolve()
                    if candidate_path.is_absolute() and candidate_path.resolve().is_relative_to(
                        root
                    ):
                        path = candidate_path.resolve().relative_to(root).as_posix()
                    update = {"file_path": path}
                    if finding.source == "static_seed" and not finding.evidence_snippet:
                        content = next(
                            (
                                f.get("content", "")
                                for f in state.get("changed_files", [])
                                if f["path"] == path
                            ),
                            "",
                        ).splitlines()
                        if finding.line_start and 1 <= finding.line_start <= len(content):
                            update["evidence_snippet"] = content[finding.line_start - 1]
                    normalized.append(finding.model_copy(update=update))
                if "findings" in patch:
                    patch["findings"] = normalized
                failures = [f for f in runtime.failures if f["agent"] == label]
                if failures:
                    patch["agents_succeeded"] = [
                        a for a in patch.get("agents_succeeded", []) if a != label
                    ]
                    patch["agents_failed"] = [*patch.get("agents_failed", []), *failures]
                runtime.capture(patch)
                runtime.event(
                    "node",
                    "partial" if failures or patch.get("agents_failed") else "completed",
                    duration=time.perf_counter() - started,
                )
            return patch
        finally:
            current_agent.reset(token)

    return wrapped


def observed_tool(name):
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            runtime = current_run.get()
            started = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                if runtime:
                    runtime.fail("tool:" + name, exc)
                    runtime.event("tool", "error", tool=name, error_type=type(exc).__name__)
                raise
            if runtime:
                from reviewer.offline import offline_mode

                runtime.event(
                    "tool",
                    "mock_skipped" if offline_mode.get() else "completed",
                    tool=name,
                    duration=time.perf_counter() - started,
                )
            return result

        return wrapped

    return decorate
