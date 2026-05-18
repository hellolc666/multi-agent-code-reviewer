"""LangSmith tracing setup.

Optional. If ``LANGCHAIN_API_KEY`` is set, configure project + tracing env so
LangChain's auto-instrumentation captures every node call and LLM invocation.
If unset, log a warning and run with tracing disabled.

Usage::

    from reviewer.observability import langsmith_setup
    langsmith_setup.configure_tracing(pr_url="...", pr_author="...", repo_name="...")

This is called once from ``runner.run_review`` per request so the trace tags
are scoped to a single review.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PROJECT = "multi-agent-code-reviewer"
_TRACING_ENABLED_VAR = "LANGCHAIN_TRACING_V2"
_API_KEY_VAR = "LANGCHAIN_API_KEY"
_PROJECT_VAR = "LANGCHAIN_PROJECT"
_warned_once = False


def is_enabled() -> bool:
    """True if LangSmith tracing is fully configured (key + tracing flag)."""
    return (
        os.environ.get(_API_KEY_VAR, "").strip() != ""
        and os.environ.get(_TRACING_ENABLED_VAR, "").strip().lower()
        in {"1", "true", "yes", "on"}
    )


def configure_tracing(
    *,
    project: str | None = None,
    pr_url: str | None = None,
    pr_author: str | None = None,
    repo_name: str | None = None,
) -> dict[str, Any]:
    """Configure LangSmith for the current process / request.

    Returns a dict of "metadata" (tags + extras) that callers can attach to
    LangChain runnables via ``with_config(...)`` if they want per-call tagging.
    Idempotent across calls; only warns once if the API key is missing.
    """
    global _warned_once

    if not is_enabled():
        if not _warned_once:
            if not os.environ.get(_API_KEY_VAR, "").strip():
                log.warning(
                    "LangSmith tracing disabled: %s is not set. "
                    "Set it (and %s=true) to enable.",
                    _API_KEY_VAR, _TRACING_ENABLED_VAR,
                )
            else:
                log.warning(
                    "LangSmith API key set but %s is not 'true'; tracing disabled.",
                    _TRACING_ENABLED_VAR,
                )
            _warned_once = True
        return {"enabled": False}

    # Pin a project so traces aren't scattered across the user's default.
    if project:
        os.environ[_PROJECT_VAR] = project
    elif not os.environ.get(_PROJECT_VAR):
        os.environ[_PROJECT_VAR] = DEFAULT_PROJECT

    tags: list[str] = []
    metadata: dict[str, str] = {}
    if pr_url:
        metadata["pr_url"] = pr_url
        tags.append(f"pr_url:{pr_url}")
    if pr_author:
        metadata["pr_author"] = pr_author
        tags.append(f"author:{pr_author}")
    if repo_name:
        metadata["repo_name"] = repo_name
        tags.append(f"repo:{repo_name}")

    log.info(
        "LangSmith tracing enabled (project=%s, tags=%s)",
        os.environ[_PROJECT_VAR], tags,
    )
    return {
        "enabled": True,
        "project": os.environ[_PROJECT_VAR],
        "tags": tags,
        "metadata": metadata,
    }


def reset_warning_for_tests() -> None:
    """Internal: reset the once-warned flag so tests can re-trigger it."""
    global _warned_once
    _warned_once = False
