"""Documentation agent.

Walks AST of each changed Python file, identifies public functions / methods /
classes (no leading underscore) lacking docstrings, asks the LLM for one-liner
docstring suggestions, and returns ``Severity.LOW`` findings (informational).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from reviewer import config
from reviewer.agents.base import FindingList, analyze_with_schema
from reviewer.graph.state import AGENT_NODE_TO_LABEL, ReviewState
from reviewer.models.findings import Category, Finding, Severity
from reviewer.tools.ast_inspector import find_classes, find_functions

log = logging.getLogger(__name__)

DOCS_LABEL = AGENT_NODE_TO_LABEL["docs_agent"]
DOCS_TIMEOUT_S = 90.0  # default; override via REVIEWER_DOCS_TIMEOUT_S


@dataclass(frozen=True)
class UndocumentedEntity:
    file_path: str
    qualname: str
    line_start: int
    kind: str  # "function" | "method" | "class"
    args: tuple[str, ...] = ()


DOCS_SYSTEM_PROMPT = (
    "You are a documentation reviewer. You receive a list of public Python "
    "entities (functions, methods, classes) that are missing docstrings. "
    "For each, write a finding suggesting a brief docstring (one sentence) "
    "based on the name and arguments. Use FindingList schema. ``category`` "
    "is \"documentation\". ``agent`` is \"docs\". Severity is always LOW "
    "(missing docstring is informational, never blocks a merge)."
)


def _undocumented(state: ReviewState) -> list[UndocumentedEntity]:
    out: list[UndocumentedEntity] = []
    for f in state.get("changed_files", []):
        path = f.get("path", "")
        if not path.endswith(".py") or f.get("change_type") == "removed":
            continue
        try:
            content = f.get("content", "")
            for fn in find_functions(content):
                if fn.is_public and not fn.has_docstring:
                    out.append(
                        UndocumentedEntity(
                            file_path=path,
                            qualname=fn.qualname,
                            line_start=fn.line_start,
                            kind="method" if fn.is_method else "function",
                            args=fn.args,
                        )
                    )
            for cls in find_classes(content):
                if cls.is_public and not cls.has_docstring:
                    out.append(
                        UndocumentedEntity(
                            file_path=path,
                            qualname=cls.name,
                            line_start=cls.line_start,
                            kind="class",
                        )
                    )
        except SyntaxError:
            log.warning("skipping %s for docs: syntax error", path)
            continue
    return out


def _entity_summary(entities: list[UndocumentedEntity]) -> str:
    if not entities:
        return "(no public entities missing docstrings)"
    lines = []
    for e in entities:
        sig = f"({', '.join(e.args)})" if e.args else ""
        lines.append(f"- {e.file_path}:{e.line_start} [{e.kind}] {e.qualname}{sig}")
    return "\n".join(lines)


def _build_docs_prompt(entities: list[UndocumentedEntity]) -> str:
    return (
        "## Public entities missing docstrings\n"
        f"{_entity_summary(entities)}\n\n"
        "Produce one finding per entry, suggesting a one-line docstring. "
        "Use the FindingList schema."
    )


def _entity_to_seed_finding(e: UndocumentedEntity, idx: int) -> Finding:
    return Finding(
        source="static_seed",
        id=f"DOC-{idx + 1:03d}",
        agent=DOCS_LABEL,
        category=Category.DOCUMENTATION,
        severity=Severity.LOW,
        file_path=e.file_path,
        line_start=e.line_start,
        line_end=e.line_start,
        title=f"Missing docstring on {e.kind} {e.qualname}",
        explanation=(
            f"Public {e.kind} {e.qualname} has no docstring. "
            "Documenting public APIs improves maintainability."
        ),
        suggestion=f"Add a one-sentence docstring describing what {e.qualname} does.",
        confidence=0.9,
        tool_source="ast-docs",
    )


async def docs_agent_node(state: ReviewState) -> dict[str, Any]:
    """LangGraph node entrypoint. Always returns a state patch — never raises."""
    timeout_s = config.agent_timeout("docs", DOCS_TIMEOUT_S)
    try:
        async with asyncio.timeout(timeout_s):
            return await _run(state)
    except TimeoutError:
        log.warning("docs agent timed out after %.0fs", timeout_s)
        return {
            "agents_failed": [
                {"agent": DOCS_LABEL, "error": f"timeout after {timeout_s:.0f}s"}
            ]
        }
    except Exception as e:  # noqa: BLE001
        log.error("docs agent crashed: %s", type(e).__name__)
        return {"agents_failed": [{"agent": DOCS_LABEL, "error": type(e).__name__}]}


async def _run(state: ReviewState) -> dict[str, Any]:
    entities = _undocumented(state)
    if not entities:
        return {"agents_succeeded": [DOCS_LABEL]}

    prompt = _build_docs_prompt(entities)
    try:
        result = await analyze_with_schema(
            prompt, FindingList, system=DOCS_SYSTEM_PROMPT
        )
        llm_findings = list(result.findings) if isinstance(result, FindingList) else []
    except Exception as e:  # noqa: BLE001
        log.warning("docs LLM call failed; using AST seeds: %s", type(e).__name__)
        llm_findings = [_entity_to_seed_finding(e, i) for i, e in enumerate(entities)]

    if not llm_findings:
        llm_findings = [_entity_to_seed_finding(e, i) for i, e in enumerate(entities)]

    # Force severity LOW + agent label.
    normalized = [
        f.model_copy(update={"agent": DOCS_LABEL, "severity": Severity.LOW})
        for f in llm_findings
    ]
    return {
        "findings": normalized,
        "agents_succeeded": [DOCS_LABEL],
    }
