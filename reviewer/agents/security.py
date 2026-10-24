"""Security agent: Semgrep + LLM analysis.

Workflow per the BUILD_SPEC:
    1. Run Semgrep on the changed Python files (deterministic).
    2. Pass Semgrep results + PR diff to the LLM for explanation, fix
       suggestions, and any vulnerabilities Semgrep missed.
    3. Return Pydantic-validated ``Finding`` objects.

The whole node is wrapped in ``asyncio.timeout(SECURITY_TIMEOUT_S)`` and any
exception (timeout, Semgrep crash, LLM 4xx, malformed structured output) is
captured into ``agents_failed`` rather than raised — LangGraph must always see
a normal patch from this node so the rest of the graph completes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from reviewer import config
from reviewer.agents.base import FindingList, analyze_with_schema
from reviewer.agents.evidence import collect_evidence
from reviewer.graph.state import AGENT_NODE_TO_LABEL, ReviewState
from reviewer.models.findings import Category, Finding, Severity
from reviewer.tools.semgrep_runner import SemgrepFinding, run_semgrep

log = logging.getLogger(__name__)

SECURITY_LABEL = AGENT_NODE_TO_LABEL["security_agent"]
SECURITY_TIMEOUT_S = 120.0  # default; override via REVIEWER_SECURITY_TIMEOUT_S

# Map Semgrep's severity strings onto our enum. Fallback is MEDIUM so an unknown
# value never silently becomes the lowest severity.
SEMGREP_TO_SEVERITY = {
    "ERROR": Severity.HIGH,
    "WARNING": Severity.MEDIUM,
    "INFO": Severity.LOW,
}


SECURITY_SYSTEM_PROMPT = (
    "You are a senior application-security reviewer for Python code. "
    "You receive Semgrep static-analysis findings and a unified PR diff. "
    "For each Semgrep finding, write a clear explanation and a concrete "
    "remediation. Then add any additional vulnerabilities you spot in the "
    "diff that Semgrep missed (e.g. timing attacks, SSRF, unsafe "
    "deserialization, hardcoded secrets, weak crypto). "
    "Set ``confidence`` between 0.0 and 1.0. Use ``severity`` from "
    '{"critical","high","medium","low","info"}. Use ``category`` "security". '
    "Use ``agent`` \"security\". Be precise about file paths and line numbers — "
    "only include line numbers you can verify from the diff."
)


def _semgrep_summary(findings: list[SemgrepFinding]) -> str:
    if not findings:
        return "(no Semgrep findings)"
    lines = []
    for sf in findings:
        lines.append(
            f"- [{sf.severity}] {sf.check_id} at {sf.path}:"
            f"{sf.line_start}-{sf.line_end}: {sf.message}"
        )
        if sf.code_snippet:
            lines.append(f"    code: {sf.code_snippet[:200]}")
    return "\n".join(lines)


def _build_security_prompt(diff: str, semgrep_findings: list[SemgrepFinding]) -> str:
    return (
        "## Semgrep findings\n"
        f"{_semgrep_summary(semgrep_findings)}\n\n"
        "## PR unified diff\n"
        f"{diff}\n\n"
        "Respond with the structured FindingList schema. "
        "If there are no security issues, return an empty list."
    )


def _semgrep_to_seed_finding(sf: SemgrepFinding, idx: int) -> Finding:
    """Convert a Semgrep result into a deterministic seed Finding.

    Used as a fallback when the LLM call fails: we still emit the Semgrep-derived
    findings so the user gets *something* even with a degraded LLM.
    """
    sev = SEMGREP_TO_SEVERITY.get(sf.severity, Severity.MEDIUM)
    return Finding(
        id=f"SEC-SG-{idx + 1:03d}",
        agent=SECURITY_LABEL,
        category=Category.SECURITY,
        severity=sev,
        file_path=sf.path,
        line_start=sf.line_start or None,
        line_end=sf.line_end or None,
        title=sf.check_id or "Semgrep finding",
        explanation=sf.message or "Reported by Semgrep.",
        suggestion="Review and address per Semgrep guidance.",
        confidence=0.85,
        tool_source=f"semgrep:{sf.check_id}" if sf.check_id else "semgrep",
    )


def _python_targets(state: ReviewState) -> list[str]:
    return [
        f["path"]
        for f in state.get("changed_files", [])
        if f.get("path", "").endswith(".py") and f.get("change_type") != "removed"
    ]


async def security_agent_node(state: ReviewState) -> dict[str, Any]:
    """LangGraph node entrypoint. Always returns a state patch — never raises."""
    timeout_s = config.agent_timeout("security", SECURITY_TIMEOUT_S)
    try:
        async with asyncio.timeout(timeout_s):
            return await _run(state)
    except TimeoutError:
        log.warning("security agent timed out after %.0fs", timeout_s)
        return {
            "agents_failed": [
                {"agent": SECURITY_LABEL, "error": f"timeout after {timeout_s:.0f}s"}
            ]
        }
    except Exception as e:  # noqa: BLE001 — node must never propagate
        log.exception("security agent crashed: %s", e)
        return {"agents_failed": [{"agent": SECURITY_LABEL, "error": str(e)}]}


async def _run(state: ReviewState) -> dict[str, Any]:
    targets = _python_targets(state)
    if not targets:
        # Nothing to scan; succeed trivially.
        return {"agents_succeeded": [SECURITY_LABEL]}

    repo_path = Path(state["repo_path"])

    # Step 1: deterministic scan.
    try:
        semgrep_findings = await run_semgrep(repo_path, targets)
    except Exception as e:  # noqa: BLE001
        log.warning("semgrep failed; continuing with LLM-only analysis: %s", e)
        semgrep_findings = []

    # Step 2: LLM enrichment + extra detection.
    evidence, budget_events = await collect_evidence(state, SECURITY_LABEL)
    prompt = _build_security_prompt(state.get("pr_diff", ""), semgrep_findings)
    if evidence:
        prompt += "\nRead-only evidence:\n" + json.dumps(evidence)
    try:
        result = await analyze_with_schema(
            prompt, FindingList, system=SECURITY_SYSTEM_PROMPT
        )
        llm_findings = list(result.findings) if isinstance(result, FindingList) else []
    except Exception as e:  # noqa: BLE001
        log.warning("security LLM call failed; falling back to Semgrep seeds: %s", e)
        llm_findings = [
            _semgrep_to_seed_finding(sf, i) for i, sf in enumerate(semgrep_findings)
        ]

    # Backstop: if both empty but Semgrep had results, surface the seeds.
    if not llm_findings and semgrep_findings:
        llm_findings = [
            _semgrep_to_seed_finding(sf, i) for i, sf in enumerate(semgrep_findings)
        ]

    # Force the agent label on all returned findings (LLM might mis-set it).
    normalized = [f.model_copy(update={"agent": SECURITY_LABEL}) for f in llm_findings]

    return {
        "findings": normalized,
        "agents_succeeded": [SECURITY_LABEL],
        "tool_evidence": evidence,
        "budget_events": budget_events,
    }
