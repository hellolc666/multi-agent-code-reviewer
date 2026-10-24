"""Test Mapping agent.

Static analysis of test coverage based on AST reference counting. NOT real
coverage measurement — we don't execute tests. Workflow:

    1. Extract changed entities (functions + classes) from the diff via AST.
    2. Walk the repo's test directories (configurable; default ``tests``).
    3. For each test file, AST-find references to changed entities. Score with
       ``CONFIDENCE_HEURISTIC`` (call+import => 0.95 covered;
       module-import+name => 0.75; string literal only => 0.5 partial; no ref
       => 0.9 uncovered).
    4. LLM enriches: writes findings recommending what tests to add for
       uncovered/partial entities.

Limitations documented in the README (Step 13).
"""

from __future__ import annotations

import asyncio
import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reviewer import config
from reviewer.agents.base import FindingList, analyze_with_schema
from reviewer.graph.state import AGENT_NODE_TO_LABEL, ReviewState
from reviewer.models.findings import Category, Finding, Severity
from reviewer.tools.ast_inspector import find_classes, find_functions, find_references
from reviewer.tools.context import CodeContext

log = logging.getLogger(__name__)

TEST_MAPPING_LABEL = AGENT_NODE_TO_LABEL["test_mapping_agent"]
TEST_MAPPING_TIMEOUT_S = 60.0  # default; override via REVIEWER_TEST_MAPPING_TIMEOUT_S
DEFAULT_TEST_DIRS = ("tests",)

# Map a set of reference kinds to (status, confidence).
# Covered confidence is "we are sure tests exist"; uncovered confidence is
# "we are sure no tests exist." Partial sits in between (string-only / weak).
_HIGH_KINDS = {"import_from", "call"}
_MEDIUM_KINDS = {"import_module", "name", "attribute"}
_LOW_KINDS = {"string"}


@dataclass(frozen=True)
class CoverageRow:
    file_path: str
    qualname: str
    name: str
    line_start: int
    status: str  # "covered" | "partially_covered" | "uncovered"
    confidence: float
    referenced_in: tuple[str, ...]


TEST_MAPPING_SYSTEM_PROMPT = (
    "You are a senior Python test reviewer. You receive a list of changed "
    "functions / classes and their static test-coverage status (covered, "
    "partially_covered, or uncovered) determined by AST reference analysis. "
    "Write findings ONLY for entries that are uncovered or partially_covered. "
    "For each, suggest what test should be added (e.g. 'add a unit test for "
    "the X case'). Use FindingList; ``category`` is \"test_coverage\"; "
    "``agent`` is \"test_mapping\"; severity LOW for partial, MEDIUM for "
    "uncovered. Use the file_path of the source entity, not the test file."
)


def _read_test_dirs(repo_path: Path) -> tuple[str, ...]:
    """Read ``[tool.reviewer] test_dirs`` from the cloned repo's pyproject.toml."""
    pp = repo_path / "pyproject.toml"
    if not pp.exists():
        return DEFAULT_TEST_DIRS
    try:
        data = tomllib.loads(CodeContext(repo_path).text("pyproject.toml"))
    except (ValueError, OSError, tomllib.TOMLDecodeError):
        return DEFAULT_TEST_DIRS
    cfg = data.get("tool", {}).get("reviewer", {}) or {}
    dirs = cfg.get("test_dirs")
    if isinstance(dirs, list) and all(isinstance(d, str) for d in dirs):
        return tuple(dirs) or DEFAULT_TEST_DIRS
    return DEFAULT_TEST_DIRS


def _is_test_file(path: Path) -> bool:
    name = path.name
    return path.suffix == ".py" and (name.startswith("test_") or name.endswith("_test.py"))


def _changed_entities(state: ReviewState) -> dict[str, list[tuple[str, str, int]]]:
    """Extract (qualname, name, line) tuples per changed source file.

    Returns a dict keyed by file path. Skips test files (we don't map tests).
    """
    out: dict[str, list[tuple[str, str, int]]] = {}
    for f in state.get("changed_files", []):
        path = f.get("path", "")
        if not path.endswith(".py") or f.get("change_type") == "removed":
            continue
        if "test" in Path(path).name.lower() and (
            Path(path).name.startswith("test_") or Path(path).name.endswith("_test.py")
        ):
            continue
        try:
            content = f.get("content", "")
            entities: list[tuple[str, str, int]] = []
            for fn in find_functions(content):
                entities.append((fn.qualname, fn.name, fn.line_start))
            for cls in find_classes(content):
                entities.append((cls.name, cls.name, cls.line_start))
            if entities:
                out[path] = entities
        except SyntaxError:
            log.warning("skipping %s for test-mapping: syntax error", path)
            continue
    return out


def _collect_test_sources(repo_path: Path, test_dirs: tuple[str, ...]) -> list[tuple[Path, str]]:
    """Read every test_*.py / *_test.py under each ``test_dirs`` entry."""
    out: list[tuple[Path, str]] = []
    context = CodeContext(repo_path)
    for d in test_dirs:
        try:
            root = context.resolve(d)
        except (OSError, ValueError):
            continue
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            if not _is_test_file(p):
                continue
            try:
                out.append((p, context.text(p.relative_to(repo_path).as_posix())))
                if len(out) >= 200:
                    return out
            except (OSError, ValueError):
                continue
    return out


def _classify(kinds: set[str]) -> tuple[str, float]:
    if kinds & _HIGH_KINDS:
        # `from X import name` and called/used → high-confidence covered
        return "covered", 0.95
    if kinds & _MEDIUM_KINDS:
        return "covered", 0.75
    if kinds & _LOW_KINDS:
        return "partially_covered", 0.5
    return "uncovered", 0.9


def _build_coverage_rows(state: ReviewState) -> list[CoverageRow]:
    repo_path = Path(state["repo_path"])
    test_dirs = _read_test_dirs(repo_path)
    test_sources = _collect_test_sources(repo_path, test_dirs)
    entities_by_file = _changed_entities(state)

    rows: list[CoverageRow] = []
    for src_path, entities in entities_by_file.items():
        names = {n for _, n, _ in entities}
        kinds_per_name: dict[str, set[str]] = {n: set() for n in names}
        referenced_in: dict[str, set[str]] = {n: set() for n in names}

        for test_path, test_src in test_sources:
            try:
                refs = find_references(test_src, names)
            except SyntaxError:
                continue
            for r in refs:
                kinds_per_name.setdefault(r.name, set()).add(r.kind)
                referenced_in.setdefault(r.name, set()).add(str(test_path))

        for qualname, name, line in entities:
            status, conf = _classify(kinds_per_name.get(name, set()))
            rows.append(
                CoverageRow(
                    file_path=src_path,
                    qualname=qualname,
                    name=name,
                    line_start=line,
                    status=status,
                    confidence=conf,
                    referenced_in=tuple(sorted(referenced_in.get(name, set()))),
                )
            )
    return rows


def _coverage_summary(rows: list[CoverageRow]) -> str:
    if not rows:
        return "(no source entities found in changed files)"
    lines = []
    for r in rows:
        refs = f" — refs: {', '.join(r.referenced_in)}" if r.referenced_in else ""
        lines.append(
            f"- {r.file_path}:{r.line_start} {r.qualname} "
            f"[{r.status} @ {r.confidence:.2f}]{refs}"
        )
    return "\n".join(lines)


def _row_to_seed_finding(row: CoverageRow, idx: int) -> Finding:
    severity = Severity.MEDIUM if row.status == "uncovered" else Severity.LOW
    suggestion = (
        f"Add a unit test exercising {row.qualname}."
        if row.status == "uncovered"
        else (
            f"Strengthen the existing tests referencing {row.qualname} "
            "with direct call-site coverage."
        )
    )
    return Finding(
        id=f"TEST-MAP-{idx + 1:03d}",
        agent=TEST_MAPPING_LABEL,
        category=Category.TEST_COVERAGE,
        severity=severity,
        file_path=row.file_path,
        line_start=row.line_start,
        line_end=row.line_start,
        title=f"{row.status}: {row.qualname}",
        explanation=(
            f"Static AST analysis classified {row.qualname} as {row.status} "
            f"(confidence {row.confidence:.2f})."
        ),
        suggestion=suggestion,
        confidence=row.confidence,
        tool_source="ast-test-mapping",
    )


def _build_test_mapping_prompt(rows: list[CoverageRow]) -> str:
    needs_attention = [r for r in rows if r.status != "covered"]
    return (
        "## Coverage classification\n"
        f"{_coverage_summary(rows)}\n\n"
        "## Entries to address\n"
        f"{_coverage_summary(needs_attention) if needs_attention else '(none)'}\n\n"
        "Produce findings ONLY for uncovered or partially_covered entries. "
        "Use the FindingList schema."
    )


async def test_mapping_agent_node(state: ReviewState) -> dict[str, Any]:
    """LangGraph node entrypoint. Always returns a state patch — never raises."""
    timeout_s = config.agent_timeout("test_mapping", TEST_MAPPING_TIMEOUT_S)
    try:
        async with asyncio.timeout(timeout_s):
            return await _run(state)
    except TimeoutError:
        log.warning("test-mapping agent timed out after %.0fs", timeout_s)
        return {
            "agents_failed": [
                {
                    "agent": TEST_MAPPING_LABEL,
                    "error": f"timeout after {timeout_s:.0f}s",
                }
            ]
        }
    except Exception as e:  # noqa: BLE001
        log.exception("test-mapping agent crashed: %s", e)
        return {"agents_failed": [{"agent": TEST_MAPPING_LABEL, "error": str(e)}]}


async def _run(state: ReviewState) -> dict[str, Any]:
    rows = _build_coverage_rows(state)
    if not rows:
        return {"agents_succeeded": [TEST_MAPPING_LABEL]}

    needs_attention = [r for r in rows if r.status != "covered"]
    prompt = _build_test_mapping_prompt(rows)

    try:
        result = await analyze_with_schema(
            prompt, FindingList, system=TEST_MAPPING_SYSTEM_PROMPT
        )
        llm_findings = list(result.findings) if isinstance(result, FindingList) else []
    except Exception as e:  # noqa: BLE001
        log.warning("test-mapping LLM call failed; using AST seeds: %s", e)
        llm_findings = [_row_to_seed_finding(r, i) for i, r in enumerate(needs_attention)]

    if not llm_findings and needs_attention:
        llm_findings = [_row_to_seed_finding(r, i) for i, r in enumerate(needs_attention)]

    normalized = [
        f.model_copy(update={"agent": TEST_MAPPING_LABEL}) for f in llm_findings
    ]
    return {
        "findings": normalized,
        "agents_succeeded": [TEST_MAPPING_LABEL],
    }
