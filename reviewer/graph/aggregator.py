"""Aggregator node.

Final step of the LangGraph workflow:
    1. Deduplicate findings on ``(file_path, line_start, category)`` — keep
       highest severity. Tie-break preserves the first-seen entry's content
       (LLM-written explanations are usually richer than seed-derived ones).
    2. Sort by severity descending, then by file path ascending, then by
       ``line_start`` (None last) for deterministic output.
    3. Generate a 1–2 sentence executive summary via the LLM (with a
       deterministic fallback string if the LLM call or its Pydantic
       validation fails up to 2 retries).
    4. Build a ``ReviewReport`` and stash it on ``state["final_report"]`` as
       a plain dict.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from reviewer.agents.base import analyze_with_schema
from reviewer.graph.state import ReviewState
from reviewer.models.findings import SEVERITY_RANK, Finding
from reviewer.models.report import AgentFailure, ReviewReport

log = logging.getLogger(__name__)

DEFAULT_SUMMARY = "Review complete — see findings below."
SUMMARY_MAX_RETRIES = 2


class _SummaryResponse(BaseModel):
    """Schema the LLM populates for the executive summary."""

    summary: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="One-to-two sentence overview of the review.",
    )


SUMMARY_SYSTEM_PROMPT = (
    "You are a senior reviewer summarizing the output of a multi-agent code "
    "review. Given a list of findings (with severity, category, file, and "
    "title), write ONE OR TWO sentences capturing the overall state of the "
    "PR — surface the highest-severity issue first if any, otherwise note "
    "that the review found nothing blocking. Do NOT enumerate findings; the "
    "report lists them separately. Stay under 300 characters."
)


# --------------------------------------------------------------------------- #
# Dedup + sort                                                                #
# --------------------------------------------------------------------------- #


def _dedup_key(f: Finding) -> tuple[str, int | None, str]:
    return (f.file_path, f.line_start, f.category.value)


def deduplicate(findings: list[Finding]) -> list[Finding]:
    """Collapse duplicates on (file, line, category) keeping the highest severity.

    On ties, the *first-seen* finding wins — preserves the richer LLM-written
    explanation over a seed fallback.
    """
    best: dict[tuple, Finding] = {}
    for f in findings:
        key = _dedup_key(f)
        existing = best.get(key)
        if existing is None or SEVERITY_RANK[f.severity] > SEVERITY_RANK[existing.severity]:
            best[key] = f
    return list(best.values())


def _sort_key(f: Finding) -> tuple[int, str, int]:
    # severity desc → negate rank; then file asc; then line asc (None as huge int)
    return (
        -SEVERITY_RANK[f.severity],
        f.file_path,
        f.line_start if f.line_start is not None else 10**9,
    )


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=_sort_key)


# --------------------------------------------------------------------------- #
# Summary                                                                     #
# --------------------------------------------------------------------------- #


def _findings_for_summary(findings: list[Finding], limit: int = 30) -> str:
    if not findings:
        return "(no findings)"
    lines = []
    for f in findings[:limit]:
        loc = f"{f.file_path}:{f.line_start}" if f.line_start else f.file_path
        lines.append(f"- [{f.severity.value}] {f.category.value} @ {loc}: {f.title}")
    if len(findings) > limit:
        lines.append(f"... ({len(findings) - limit} more)")
    return "\n".join(lines)


def _build_summary_prompt(findings: list[Finding], agents_failed: list[dict]) -> str:
    failed = (
        "\n".join(f"- {rec.get('agent', '?')}: {rec.get('error', '?')}" for rec in agents_failed)
        if agents_failed
        else "(none)"
    )
    return (
        f"## Findings (sorted)\n{_findings_for_summary(findings)}\n\n"
        f"## Failed agents\n{failed}\n\n"
        "Respond with the _SummaryResponse schema."
    )


async def _generate_summary(findings: list[Finding], agents_failed: list[dict]) -> str:
    """Try the LLM up to ``SUMMARY_MAX_RETRIES + 1`` times; fall back on failure."""
    if not findings and not agents_failed:
        return "Review complete — no issues found across all agents."
    prompt = _build_summary_prompt(findings, agents_failed)
    last_error: Exception | None = None
    for attempt in range(SUMMARY_MAX_RETRIES + 1):
        try:
            result = await analyze_with_schema(
                prompt, _SummaryResponse, system=SUMMARY_SYSTEM_PROMPT
            )
            if isinstance(result, _SummaryResponse) and result.summary.strip():
                return result.summary.strip()
        except Exception as e:  # noqa: BLE001
            last_error = e
            log.warning("summary generation attempt %d failed: %s", attempt + 1, e)
    if last_error:
        log.warning("falling back to default summary after %d failures", SUMMARY_MAX_RETRIES + 1)
    return DEFAULT_SUMMARY


# --------------------------------------------------------------------------- #
# Aggregator node                                                             #
# --------------------------------------------------------------------------- #


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_failure_models(records: list[dict]) -> list[AgentFailure]:
    out: list[AgentFailure] = []
    for r in records or []:
        try:
            out.append(AgentFailure(agent=str(r.get("agent", "")), error=str(r.get("error", ""))))
        except Exception:  # noqa: BLE001
            continue
    return out


async def aggregate_findings_node(state: ReviewState) -> dict[str, Any]:
    """LangGraph node — assembles the final ``ReviewReport`` dict.

    Always returns a dict with ``final_report`` populated. Never raises.

    Special case: when the planner produced no applicable agents (e.g. a PR
    that touches no Python files in v1), ``state["agents_to_run"]`` is empty
    AND no agent has succeeded. We short-circuit the LLM summary and emit a
    deterministic message that explains why no review ran — much more useful
    than the generic fallback string.
    """
    raw = list(state.get("findings", []))
    deduped = deduplicate(raw)
    ordered = sort_findings(deduped)

    agents_failed = list(state.get("agents_failed", []))
    agents_succeeded = list(state.get("agents_succeeded", []))
    agents_to_run = list(state.get("agents_to_run", []))

    if not agents_to_run and not agents_succeeded and not agents_failed:
        summary = _no_agents_summary(state.get("changed_files", []))
    else:
        summary = await _generate_summary(ordered, agents_failed)

    metadata = state.get("pr_metadata") or {}
    started_at = metadata.get("started_at") or _utcnow_iso()
    completed_at = _utcnow_iso()
    duration = _duration_seconds(started_at, completed_at)

    report = ReviewReport(
        pr_url=state.get("pr_url", ""),
        pr_title=str(metadata.get("title", "")),
        pr_author=str(metadata.get("author", "")),
        review_started_at=started_at,
        review_completed_at=completed_at,
        duration_seconds=duration,
        files_changed=len(state.get("changed_files", [])),
        findings=ordered,
        summary=summary,
        agents_succeeded=list(dict.fromkeys(state.get("agents_succeeded", []))),
        agents_failed=_to_failure_models(agents_failed),
        trace_id=state.get("trace_id") or str(uuid.uuid4()),
        input_mode=state.get("review_input", {}).get("mode", "github"),
        head_sha=metadata.get("head_sha"),
        diff_sha256=state.get("review_input", {}).get("diff_sha256"),
        reviewed_files=[f["path"] for f in state.get("changed_files", [])],
        coverage=state.get("review_input", {}).get("coverage", {}),
        tool_evidence=state.get("tool_evidence", []),
        budget_events=state.get("budget_events", []),
        source_errors=state.get("source_errors", []),
        execution_mode=state.get("execution_mode", "live"),
        review_plan=state.get("review_plan", {}),
        planner_errors=state.get("planner_errors", []),
        status=(
            "partial"
            if agents_failed and agents_succeeded
            else "failed"
            if agents_failed
            else "completed"
            if agents_succeeded
            else "skipped"
        ),
    )
    return {"final_report": report.model_dump(mode="json")}


def _duration_seconds(started_at: str, completed_at: str) -> float:
    """Best-effort: parse ISO timestamps and diff. 0.0 on parse failure."""
    try:
        s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        e = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        delta = (e - s).total_seconds()
        return max(0.0, round(delta, 3))
    except (ValueError, TypeError):
        return 0.0


def _no_agents_summary(changed_files: list[dict]) -> str:
    """Build a human-friendly summary for the no-applicable-agents path.

    Lists the file extensions present in the diff so the user can see at a
    glance why nothing fired (v1 is Python-only).
    """
    exts: Counter = Counter()
    for f in changed_files or []:
        path = f.get("path", "")
        # ``.gitignore``-style files have no suffix; fall back to the basename.
        suffix = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else "(none)"
        exts[suffix] += 1
    if exts:
        breakdown = ", ".join(f"{n} .{ext}" for ext, n in exts.most_common())
    else:
        breakdown = "no files"
    return (
        "No applicable agents for this PR. v1 reviews Python repositories only "
        f"(this PR contains: {breakdown}). Run on a PR that touches .py files "
        "to get a full review."
    )
