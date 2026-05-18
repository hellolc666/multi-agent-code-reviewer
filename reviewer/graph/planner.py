"""Deterministic planner.

Decides which specialists to run based on what the PR touches. No LLM call —
the rules are trivial and pure-Python is faster, cheaper, and deterministic.

Returns a state patch that fills ``agents_to_run``; the dispatcher in
``builder.py`` then fans out via ``Send`` to those node names.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

from reviewer.graph.state import AGENT_NODES, ReviewState

DEFAULT_TEST_DIRS: tuple[str, ...] = ("tests", "test")


def _is_test_path(path: str, test_dirs: tuple[str, ...] = DEFAULT_TEST_DIRS) -> bool:
    """Path-segment aware test detection (not a string ``startswith``)."""
    parts = PurePath(path).parts
    if not parts:
        return False
    if parts[0] in test_dirs:
        return True
    name = parts[-1]
    return name.startswith("test_") or name.endswith("_test.py")


async def plan_review_node(state: ReviewState) -> dict[str, Any]:
    """Choose the agents to run for this PR.

    Rules:
        - Any changed ``.py`` file → security, quality, docs.
        - Any changed file that is NOT itself a test file → test_mapping
          (the agent only makes sense when there is non-test source to map).
    """
    files = state.get("changed_files", [])
    agents: list[str] = []

    has_python = any(f.get("path", "").endswith(".py") for f in files)
    if has_python:
        agents.extend(["security_agent", "quality_agent", "docs_agent"])

    has_non_test_source = any(
        not _is_test_path(f.get("path", ""))
        and f.get("path", "").endswith(".py")
        and f.get("change_type") != "removed"
        for f in files
    )
    if has_non_test_source:
        agents.append("test_mapping_agent")

    # Preserve ordering & uniqueness.
    seen: set[str] = set()
    unique: list[str] = []
    for a in agents:
        if a in AGENT_NODES and a not in seen:
            seen.add(a)
            unique.append(a)

    return {"agents_to_run": unique}
