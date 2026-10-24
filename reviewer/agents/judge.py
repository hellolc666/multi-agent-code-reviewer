"""Independent evidence verification. Never infer confirmation from an outage."""

from __future__ import annotations

import ast
import json
import os
import re
from collections import defaultdict

from reviewer.agents.base import analyze_with_schema
from reviewer.agents.evidence import UNTRUSTED_SYSTEM, untrusted
from reviewer.models.findings import SEVERITY_RANK, Finding
from reviewer.models.inputs import ReviewInput
from reviewer.models.verification import JudgeDecision, VerifiedFinding
from reviewer.tools.context import CodeContext, safe_relative

SYSTEM = UNTRUSTED_SYSTEM + (
    " You are an independent skeptical Judge, not the proposing reviewer. Try to disprove "
    "the candidate against actual changed code, surrounding code and static/test evidence. "
    "Confirm only a concrete change-related defect with a reproducible trigger, causal "
    "explanation and supporting code. Reject incorrect/low-value style-only claims. "
    "Use uncertain when context is missing, confidence is low or evidence is inconclusive. "
    "Do not infer dynamic test results; no tests were executed. Contradictory candidates "
    "are not evidence by themselves. Set evidence_support and reproducible_trigger for confirmed."
)


def _groups(findings):
    groups = {}
    for index, raw in enumerate(findings):
        finding = Finding.model_validate(raw)
        title = re.sub(r"\W+", " ", finding.title.lower()).strip()
        key = (finding.file_path, finding.line_start, title)
        identity = f"{finding.agent}/{finding.id}/{index}"
        if key not in groups:
            groups[key] = (finding, [identity], [finding.agent])
        else:
            previous, ids, agents = groups[key]
            ids.append(identity)
            if finding.agent not in agents:
                agents.append(finding.agent)
            if (SEVERITY_RANK[finding.severity], finding.confidence) > (
                SEVERITY_RANK[previous.severity],
                previous.confidence,
            ):
                previous = finding
            groups[key] = previous, ids, agents
    return list(groups.values())


def _anchor(finding, value, context):
    """Return (early status/reason, checks, inline eligibility, code evidence)."""
    checks = []
    try:
        path = safe_relative(finding.file_path)
    except ValueError:
        return ("rejected", "invalid_path"), checks, False, {}
    file = next((f for f in value.files if f.path == path), None)
    if file is None:
        return ("rejected", "file_not_in_diff"), checks, False, {}
    checks.append("path_in_diff")
    start, end = finding.line_start, finding.line_end or finding.line_start
    if start is None:
        return ("uncertain", "missing_line_anchor"), checks, False, {}
    if start < 1 or end < start or end - start > 20:
        return ("rejected", "invalid_line_range"), checks, False, {}
    right = {
        line.new_line: line
        for hunk in file.hunks
        for line in hunk.lines
        if line.new_line is not None
    }
    added = {number for number, line in right.items() if line.kind == "add"}
    if start not in right or any(number not in right for number in range(start, end + 1)):
        return ("rejected", "line_outside_changed_hunks"), checks, False, {}
    inline = start in added
    if not inline and not finding.trigger_condition:
        return ("uncertain", "context_anchor_requires_impact_explanation"), checks, False, {}
    checks.append("right_side_added_line" if inline else "affected_hunk_context")
    try:
        source = context.text(path)
        lines = source.splitlines()
        if any(
            number > len(lines) or lines[number - 1] != right[number].text
            for number in range(start, end + 1)
        ):
            return ("uncertain", "checkout_does_not_match_diff"), checks, False, {}
        snippet = finding.evidence_snippet
        if not snippet:
            return ("uncertain", "missing_exact_evidence_snippet"), checks, inline, {}
        if snippet.strip() not in "\n".join(lines[start - 1 : end]).strip():
            return ("rejected", "evidence_snippet_not_at_anchor"), checks, False, {}
        checks.append("snippet_exists_at_anchor")
        evidence = {
            "code": context.read_file(path, max(1, start - 10), end + 10),
            "hunks": context.get_changed_hunks(path),
            "tests": [],
        }
        try:
            functions = [
                n
                for n in ast.walk(ast.parse(source))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.lineno <= start <= (n.end_lineno or n.lineno)
            ]
            if functions:
                refs = context.search_references(functions[-1].name)["matches"]
                for ref in refs:
                    if "test" in ref["path"] and len(evidence["tests"]) < 3:
                        evidence["tests"].append(
                            context.read_file(
                                ref["path"],
                                max(1, ref["line"] - 2),
                                ref["line"] + 3,
                            )
                        )
        except (SyntaxError, RecursionError):
            pass
        if finding.confidence < 0.6:
            return ("uncertain", "low_candidate_confidence"), checks, inline, evidence
        return None, checks, inline, evidence
    except (ValueError, OSError):
        return ("uncertain", "source_evidence_unavailable"), checks, False, {}


async def judge_findings_node(state):
    raw = list(state.get("findings", []))
    enabled = state.get("review_options", {}).get(
        "judge_enabled",
        os.environ.get("REVIEWER_JUDGE_ENABLED", "true").lower() == "true",
    )
    value = ReviewInput.model_validate(state["review_input"]) if state.get("review_input") else None
    context = CodeContext(state["repo_path"], value.files) if value else None
    records = []
    for finding, ids, agents in _groups(raw):
        checks, inline = [], False
        incomplete = False
        if not enabled:
            status, reason = "uncertain", "judge_disabled"
        elif value is None:
            status, reason, incomplete = "uncertain", "input_evidence_unavailable", True
        else:
            early, checks, inline, evidence = _anchor(finding, value, context)
            if early:
                status, reason = early
            else:
                try:
                    related = [
                        {"agent": f.agent, "title": f.title, "explanation": f.explanation}
                        for f, _, _ in _groups(raw)
                        if f.file_path == finding.file_path and f.line_start == finding.line_start
                    ]
                    # This explicit projection cannot carry evaluator/gold fields.
                    payload = {
                        "candidate": finding.model_dump(mode="json"),
                        "evidence": evidence,
                        "related_candidates": related[:8],
                        "static_tool_evidence": [
                            r for r in state.get("tool_evidence", []) if r.get("agent") in agents
                        ][:6],
                    }
                    decision = await analyze_with_schema(
                        untrusted(json.dumps(payload)), JudgeDecision, system=SYSTEM
                    )
                    status = "rejected" if decision.style_only else decision.status
                    reason = decision.reason
                except Exception as exc:
                    status, reason, incomplete = (
                        "uncertain",
                        "judge_error:" + type(exc).__name__,
                        True,
                    )
        records.append(
            VerifiedFinding(
                finding=finding.model_copy(update={"verification_status": status}),
                status=status,
                reason=reason,
                candidate_ids=ids,
                source_agents=agents,
                checks=checks,
                can_comment=inline and status == "confirmed",
                verification_incomplete=incomplete,
            ).model_dump(mode="json")
        )
    by_anchor = defaultdict(list)
    for record in records:
        f = record["finding"]
        by_anchor[(f["file_path"], f["line_start"])].append(record)
    conflicts = [
        {
            "path": key[0],
            "line": key[1],
            "candidate_ids": [i for r in group for i in r["candidate_ids"]],
        }
        for key, group in by_anchor.items()
        if len({r["status"] for r in group}) > 1
    ]
    return {
        "raw_findings": raw,
        "verified_findings": records,
        "verification_incomplete": any(r["verification_incomplete"] for r in records),
        "judge_enabled": enabled,
        "verification_conflicts": conflicts,
    }
