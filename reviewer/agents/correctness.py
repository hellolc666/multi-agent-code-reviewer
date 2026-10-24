"""Concrete logic, boundary, exception and state-consistency review."""

from __future__ import annotations

import asyncio
import json

from reviewer import config
from reviewer.agents.base import FindingList, analyze_with_schema
from reviewer.agents.evidence import UNTRUSTED_SYSTEM, collect_evidence, untrusted
from reviewer.models.findings import Category

SYSTEM = UNTRUSTED_SYSTEM + (
    " You are the correctness reviewer. Identify only defects caused or exposed by this change: "
    "logic/boundary conditions, exceptions, state/transaction consistency, resource leaks, "
    "concurrency or N+1 requests. Require an explicit trigger_condition, real file/RIGHT line "
    "and exact evidence_snippet. Explain the causal chain using related context. "
    "Do not emit style opinions, speculative defects, unrelated pre-existing issues, "
    "or assertions about tests that were not run. Use agent/category correctness."
)


async def correctness_agent_node(state):
    try:
        async with asyncio.timeout(config.agent_timeout("correctness", 90.0)):
            evidence, events = await collect_evidence(state, "correctness")
            result = await analyze_with_schema(
                untrusted(state.get("pr_diff", "") + "\nEvidence:\n" + json.dumps(evidence)),
                FindingList,
                system=SYSTEM,
            )
            findings = [
                f.model_copy(update={"agent": "correctness", "category": Category.CORRECTNESS})
                for f in result.findings
            ]
            return {
                "findings": findings,
                "agents_succeeded": ["correctness"],
                "tool_evidence": evidence,
                "budget_events": events,
            }
    except Exception as exc:
        return {"agents_failed": [{"agent": "correctness", "error": type(exc).__name__}]}
