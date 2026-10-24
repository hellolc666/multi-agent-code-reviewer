"""Quality agent: Ruff + AST complexity + LLM analysis.

Workflow:
    1. Run Ruff on changed Python files (deterministic style/lint signal).
    2. Compute cyclomatic complexity per changed function via AST.
    3. Pass Ruff diagnostics + complexity table + diff to the LLM.
    4. LLM identifies code smells, naming issues, and DRY violations beyond Ruff.

Same reliability shape as the Security agent: catches everything, returns an
``agents_failed`` patch on failure rather than raising.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from reviewer import config
from reviewer.agents.base import FindingList, analyze_with_schema
from reviewer.graph.state import AGENT_NODE_TO_LABEL, ReviewState
from reviewer.models.findings import Category, Finding, Severity
from reviewer.tools.ast_inspector import find_functions
from reviewer.tools.ruff_runner import RuffFinding, run_ruff

log = logging.getLogger(__name__)

QUALITY_LABEL = AGENT_NODE_TO_LABEL["quality_agent"]
QUALITY_TIMEOUT_S = 90.0  # default; override via REVIEWER_QUALITY_TIMEOUT_S
COMPLEXITY_WARN_THRESHOLD = 10  # functions at/above this get flagged in the prompt

# Ruff codes -> our severity. Default MEDIUM for unknown prefixes.
_RUFF_PREFIX_TO_SEVERITY: dict[str, Severity] = {
    "E": Severity.LOW,    # pycodestyle errors (mostly cosmetic)
    "W": Severity.LOW,    # pycodestyle warnings
    "F": Severity.MEDIUM, # Pyflakes (real bugs: unused imports, undefined names)
    "B": Severity.MEDIUM, # flake8-bugbear (likely bugs)
    "I": Severity.LOW,    # import sorting
    "UP": Severity.LOW,   # pyupgrade
}


QUALITY_SYSTEM_PROMPT = (
    "You are a senior Python code reviewer focused on code quality. "
    "You receive Ruff diagnostics, a per-function complexity table, and a "
    "unified PR diff. Identify code smells, naming issues, DRY violations, "
    "and excessive complexity that Ruff alone wouldn't catch. Use the "
    "FindingList schema. Set ``category`` to \"quality\". Use ``agent`` "
    "\"quality\". Severity guidance: bug-likely issues are medium/high; "
    "stylistic suggestions are low; only mark high if the issue is likely "
    "to cause real defects."
)


def _ruff_severity(code: str) -> Severity:
    if not code:
        return Severity.MEDIUM
    # Match the longest prefix (e.g. "UP" before "U").
    for prefix in ("UP", "F", "E", "W", "B", "I"):
        if code.startswith(prefix):
            return _RUFF_PREFIX_TO_SEVERITY[prefix]
    return Severity.MEDIUM


def _ruff_summary(findings: list[RuffFinding]) -> str:
    if not findings:
        return "(no Ruff findings)"
    lines = []
    for r in findings:
        lines.append(
            f"- {r.code} at {r.path}:{r.line_start}: {r.message}"
            + (" [auto-fixable]" if r.fix_applicable else "")
        )
    return "\n".join(lines)


def _complexity_table(state: ReviewState) -> str:
    """Build a per-function complexity table from changed Python sources."""
    rows: list[str] = []
    for f in state.get("changed_files", []):
        path = f.get("path", "")
        if not path.endswith(".py") or f.get("change_type") == "removed":
            continue
        try:
            for fn in find_functions(f.get("content", "")):
                marker = " ⚠" if fn.complexity >= COMPLEXITY_WARN_THRESHOLD else ""
                rows.append(
                    f"- {path}:{fn.line_start} {fn.qualname} "
                    f"(args={len(fn.args)}, complexity={fn.complexity}){marker}"
                )
        except SyntaxError as e:
            rows.append(f"- {path}: <syntax error: {e}>")
    return "\n".join(rows) if rows else "(no functions)"


def _build_quality_prompt(
    diff: str, ruff_findings: list[RuffFinding], complexity_table: str
) -> str:
    return (
        "## Ruff diagnostics\n"
        f"{_ruff_summary(ruff_findings)}\n\n"
        "## Function complexity (changed files)\n"
        f"{complexity_table}\n\n"
        "## PR unified diff\n"
        f"{diff}\n\n"
        "Return findings in the FindingList schema. Empty list if nothing to flag."
    )


def _ruff_to_seed_finding(rf: RuffFinding, idx: int) -> Finding:
    return Finding(
        source="static_seed",
        id=f"QUAL-RF-{idx + 1:03d}",
        agent=QUALITY_LABEL,
        category=Category.QUALITY,
        severity=_ruff_severity(rf.code),
        file_path=rf.path,
        line_start=rf.line_start or None,
        line_end=rf.line_end or None,
        title=rf.code or "Ruff finding",
        explanation=rf.message or "Reported by Ruff.",
        suggestion=(
            "Apply the auto-fix or refactor accordingly."
            if rf.fix_applicable
            else "Refactor to address the lint warning."
        ),
        confidence=0.85,
        tool_source=f"ruff:{rf.code}" if rf.code else "ruff",
    )


def _python_targets(state: ReviewState) -> list[str]:
    return [
        f["path"]
        for f in state.get("changed_files", [])
        if f.get("path", "").endswith(".py") and f.get("change_type") != "removed"
    ]


async def quality_agent_node(state: ReviewState) -> dict[str, Any]:
    """LangGraph node entrypoint. Always returns a state patch — never raises."""
    timeout_s = config.agent_timeout("quality", QUALITY_TIMEOUT_S)
    try:
        async with asyncio.timeout(timeout_s):
            return await _run(state)
    except TimeoutError:
        log.warning("quality agent timed out after %.0fs", timeout_s)
        return {
            "agents_failed": [
                {"agent": QUALITY_LABEL, "error": f"timeout after {timeout_s:.0f}s"}
            ]
        }
    except Exception as e:  # noqa: BLE001
        log.error("quality agent crashed: %s", type(e).__name__)
        return {"agents_failed": [{"agent": QUALITY_LABEL, "error": type(e).__name__}]}


async def _run(state: ReviewState) -> dict[str, Any]:
    targets = _python_targets(state)
    if not targets:
        return {"agents_succeeded": [QUALITY_LABEL]}

    repo_path = Path(state["repo_path"])

    try:
        ruff_findings = await run_ruff(repo_path, targets)
    except Exception as e:  # noqa: BLE001
        log.warning("ruff failed; continuing with LLM-only analysis: %s", type(e).__name__)
        ruff_findings = []

    table = _complexity_table(state)
    prompt = _build_quality_prompt(state.get("pr_diff", ""), ruff_findings, table)

    try:
        result = await analyze_with_schema(
            prompt, FindingList, system=QUALITY_SYSTEM_PROMPT
        )
        llm_findings = list(result.findings) if isinstance(result, FindingList) else []
    except Exception as e:  # noqa: BLE001
        log.warning("quality LLM call failed; falling back to Ruff seeds: %s", type(e).__name__)
        llm_findings = [
            _ruff_to_seed_finding(rf, i) for i, rf in enumerate(ruff_findings)
        ]

    if not llm_findings and ruff_findings:
        llm_findings = [
            _ruff_to_seed_finding(rf, i) for i, rf in enumerate(ruff_findings)
        ]

    normalized = [f.model_copy(update={"agent": QUALITY_LABEL}) for f in llm_findings]
    return {
        "findings": normalized,
        "agents_succeeded": [QUALITY_LABEL],
    }
