"""Deterministic risk floor with optional validated model suggestions."""

from __future__ import annotations

import os
import re
from pathlib import PurePosixPath

from reviewer.agents.base import analyze_with_schema
from reviewer.agents.evidence import UNTRUSTED_SYSTEM, untrusted
from reviewer.graph.state import AGENT_NODE_TO_LABEL
from reviewer.models.plan import ReviewPlan

DEFAULT_TEST_DIRS = ("tests", "test")
RISK_PATTERNS = {
    "authentication/authorization": r"auth|password|permission|credential|token|session|role",
    "SQL/data integrity": (
        r"execute\(|SELECT |INSERT |UPDATE |DELETE |"
        r"transaction|commit\(|rollback"
    ),
    "filesystem/deserialization": r"open\(|unlink|rmtree|pickle|yaml\.load|eval\(|exec\(",
    "network/concurrency": r"requests\.|httpx\.|urllib|async |await |thread|Lock\(|socket",
}
ORDER = ("security", "correctness", "quality", "test_mapping", "docs")


def _is_test_path(path, test_dirs=DEFAULT_TEST_DIRS):
    parts = PurePosixPath(path.replace("\\", "/")).parts
    return bool(parts) and (
        parts[0] in test_dirs or parts[-1].startswith("test_") or parts[-1].endswith("_test.py")
    )


async def plan_review_node(state):
    options = state.get("review_options", {})
    mode = options.get("planner_mode", os.environ.get("REVIEWER_PLANNER_MODE", "rules"))
    if mode not in {"rules", "hybrid"}:
        raise ValueError("planner_mode must be rules or hybrid")
    maximum = max(1, min(5, int(options.get("max_reviewers", 5))))
    files = state.get("changed_files", [])
    python = [f for f in files if f.get("path", "").endswith(".py")]
    source = [f for f in python if not _is_test_path(f["path"])]
    present = [f for f in source if f.get("change_type") != "removed"]
    text = (
        state.get("pr_diff", "")
        + "\n"
        + "\n".join(f["path"] + "\n" + f.get("content", "") for f in source)
    )
    risks = [
        name for name, pattern in RISK_PATTERNS.items() if source and re.search(pattern, text, re.I)
    ]
    reasons = {}
    if python:
        reasons.update(
            correctness="Executable Python change: inspect behavior and boundaries",
            quality="Python AST/lint and maintainability review",
        )
    if risks:
        reasons["security"] = "; ".join(risks)
    if present:
        reasons["test_mapping"] = "Source change: statically inspect related regression tests"
    if any(
        re.search(r"^\s*(?:async )?(?:def|class) [A-Za-z]", f.get("content", ""), re.M)
        for f in present
    ):
        reasons["docs"] = "Public Python API present in changed source"
    plan = ReviewPlan(
        risk="high" if risks else "medium" if len(text) > 12000 else "low",
        change_summary=f"{len(files)} files; {len(python)} Python changes",
        affected_files=[f["path"] for f in files],
        selected_agents=[a for a in ORDER if a in reasons],
        focus_points=risks or (["logic and boundary conditions"] if python else []),
        routing_reasons=reasons,
        mode=mode,
    )
    errors = []
    if mode == "hybrid" and python:
        try:
            suggestion = await analyze_with_schema(
                untrusted(state.get("pr_diff", "") + "\nRules plan:\n" + plan.model_dump_json()),
                ReviewPlan,
                system=UNTRUSTED_SYSTEM + " Suggest review agents/focus only. "
                "Allowed agents: security, correctness, quality, test_mapping, docs. "
                "The deterministic safety floor cannot be removed.",
            )
            for agent in suggestion.selected_agents:
                reasons.setdefault(agent, "Validated hybrid planner suggestion")
            plan.selected_agents = [a for a in ORDER if a in reasons]
            plan.routing_reasons = reasons
            plan.focus_points = list(dict.fromkeys([*plan.focus_points, *suggestion.focus_points]))[
                :12
            ]
        except Exception as exc:
            errors.append({"agent": "planner", "error": type(exc).__name__, "fallback": "rules"})
    omitted = plan.selected_agents[maximum:]
    plan.selected_agents = plan.selected_agents[:maximum]
    plan.budget = {"max_reviewers": maximum, "omitted_agents": omitted}
    label_to_node = {v: k for k, v in AGENT_NODE_TO_LABEL.items()}
    return {
        "agents_to_run": [label_to_node[a] for a in plan.selected_agents],
        "review_plan": plan.model_dump(mode="json"),
        "planner_errors": errors,
    }
