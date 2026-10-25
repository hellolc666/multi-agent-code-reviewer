"""Generate examples/sample_review_output.json from the real aggregator.

Runs offline: stubs the four agents to emit canonical findings shapes, runs
the real ``aggregate_findings_node``, then serializes the resulting
``final_report`` dict.

This script is committed so the example stays in sync with the schema as the
code evolves; re-run it any time ``ReviewReport`` changes.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow running from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reviewer.graph import aggregator as agg_mod  # noqa: E402
from reviewer.graph.aggregator import aggregate_findings_node  # noqa: E402
from reviewer.models.findings import Category, Finding, Severity  # noqa: E402

SAMPLE_FINDINGS = [
    Finding(
        id="F-001",
        agent="security",
        category=Category.SECURITY,
        severity=Severity.HIGH,
        file_path="app/auth.py",
        line_start=23,
        line_end=23,
        title="Password compared with == instead of constant-time comparison",
        explanation=(
            "Comparing passwords with == leaks timing information to "
            "attackers who can iterate request response times to guess "
            "characters. Use secrets.compare_digest() for constant-time "
            "string comparison."
        ),
        suggestion=(
            "Replace `if password == stored_password:` with "
            "`if secrets.compare_digest(password, stored_password):`."
        ),
        confidence=0.92,
        tool_source="semgrep:python.lang.security.unsafe-eq",
    ),
    Finding(
        id="F-002",
        agent="security",
        category=Category.SECURITY,
        severity=Severity.MEDIUM,
        file_path="app/auth.py",
        line_start=12,
        line_end=12,
        title="Hardcoded API token detected",
        explanation=(
            "A hardcoded credential was found in source. Even if the "
            "value is rotated later, it persists in git history."
        ),
        suggestion=(
            "Move the token to an environment variable and load it via "
            "`os.environ['API_TOKEN']` at startup. Rotate the leaked value."
        ),
        confidence=0.88,
        tool_source="semgrep:python.lang.security.hardcoded-token",
    ),
    Finding(
        id="F-003",
        agent="quality",
        category=Category.QUALITY,
        severity=Severity.MEDIUM,
        file_path="app/auth.py",
        line_start=45,
        line_end=72,
        title="Function classify_user has cyclomatic complexity 11 (>10)",
        explanation=(
            "classify_user has 7 branches over role, age, country, and "
            "is_active. The conditional logic is hard to test exhaustively "
            "and likely to grow."
        ),
        suggestion=(
            "Extract a per-role handler dict mapping role to a tier-resolver "
            "function. Drops complexity to 3 and makes new roles trivial to add."
        ),
        confidence=0.85,
        tool_source="ast-complexity",
    ),
    Finding(
        id="F-004",
        agent="quality",
        category=Category.QUALITY,
        severity=Severity.LOW,
        file_path="app/auth.py",
        line_start=14,
        line_end=14,
        title="Unused import: os",
        explanation="`os` is imported but never used.",
        suggestion="Remove the import.",
        confidence=0.99,
        tool_source="ruff:F401",
    ),
    Finding(
        id="F-005",
        agent="test_mapping",
        category=Category.TEST_COVERAGE,
        severity=Severity.MEDIUM,
        file_path="app/auth.py",
        line_start=78,
        line_end=78,
        title="uncovered: Session.refresh",
        explanation=(
            "Static AST analysis classified Session.refresh as uncovered "
            "(no test file references the method)."
        ),
        suggestion=(
            "Add a unit test exercising Session.refresh — assert the "
            "returned user_id matches the input."
        ),
        confidence=0.9,
        tool_source="ast-test-mapping",
    ),
    Finding(
        id="F-006",
        agent="docs",
        category=Category.DOCUMENTATION,
        severity=Severity.LOW,
        file_path="app/auth.py",
        line_start=18,
        line_end=18,
        title="Missing docstring on function login",
        explanation=(
            "Public function login has no docstring. Documenting public "
            "APIs improves maintainability."
        ),
        suggestion=(
            "Add: 'Authenticate a user given the supplied password.'"
        ),
        confidence=0.9,
        tool_source="ast-docs",
    ),
    Finding(
        id="F-007",
        agent="docs",
        category=Category.DOCUMENTATION,
        severity=Severity.LOW,
        file_path="app/auth.py",
        line_start=70,
        line_end=70,
        title="Missing docstring on class Session",
        explanation="Public class Session has no docstring.",
        suggestion="Add: 'A user session with refresh-token semantics.'",
        confidence=0.9,
        tool_source="ast-docs",
    ),
]


async def main() -> None:
    # Stub the LLM-based summary so the example is deterministic.
    async def fake_summary(findings, agents_failed):  # noqa: ARG001
        return (
            "Found 1 high-severity timing-attack risk and 1 hardcoded token "
            "in app/auth.py, plus complexity, missing docstrings, and a "
            "missing test for Session.refresh. Fix the timing-comparison "
            "and rotate the token before merge."
        )

    agg_mod._generate_summary = fake_summary  # type: ignore[assignment]

    state = {
        "pr_url": "https://github.com/example/repo/pull/42",
        "pr_metadata": {
            "title": "Add user authentication endpoint",
            "author": "alice",
            "started_at": "2026-05-17T14:00:00Z",
        },
        "changed_files": [
            {"path": "app/auth.py"},
            {"path": "app/utils.py"},
            {"path": "app/__init__.py"},
            {"path": "tests/test_auth.py"},
        ],
        "findings": SAMPLE_FINDINGS,
        "agents_succeeded": ["security", "quality", "test_mapping", "docs"],
        "agents_failed": [],
        "trace_id": "1f3b9d2e-7a8c-4e9f-8b1a-2c3d4e5f6a7b",
    }

    out = await aggregate_findings_node(state)
    report = out["final_report"]

    # Overwrite the timestamps with fixed, realistic values so the committed
    # example doesn't drift every time someone re-runs the generator.
    report["review_started_at"] = "2026-05-17T14:00:00Z"
    report["review_completed_at"] = "2026-05-17T14:01:23Z"
    report["duration_seconds"] = 83.0
    report["trace_id"] = "1f3b9d2e-7a8c-4e9f-8b1a-2c3d4e5f6a7b"

    target = Path(__file__).resolve().parent.parent / "examples" / "sample_review_output.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {target}")


if __name__ == "__main__":
    asyncio.run(main())
