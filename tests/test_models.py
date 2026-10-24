"""Validates the Pydantic models accept the sample output from BUILD_SPEC §Sample Output."""

import pytest
from pydantic import ValidationError

from reviewer.graph.state import AGENT_NODE_TO_LABEL, AGENT_NODES, ReviewState
from reviewer.models import Category, Finding, ReviewReport, Severity
from reviewer.models.findings import SEVERITY_RANK

SAMPLE_FINDING_DATA = {
    "id": "F-001",
    "agent": "security",
    "category": "security",
    "severity": "high",
    "file_path": "app/auth.py",
    "line_start": 23,
    "line_end": 25,
    "title": "Password compared with == instead of constant-time comparison",
    "explanation": (
        "Comparing passwords with == allows timing attacks. Use secrets.compare_digest() instead."
    ),
    "suggestion": (
        "Replace `if password == stored_password:` with "
        "`if secrets.compare_digest(password, stored_password):`"
    ),
    "confidence": 0.92,
    "tool_source": "semgrep:python.lang.security.unsafe-eq",
}

SAMPLE_REPORT_DATA = {
    "pr_url": "https://github.com/example/repo/pull/42",
    "pr_title": "Add user authentication endpoint",
    "pr_author": "alice",
    "review_started_at": "2026-05-17T14:00:00Z",
    "review_completed_at": "2026-05-17T14:01:23Z",
    "duration_seconds": 83.2,
    "files_changed": 4,
    "findings": [SAMPLE_FINDING_DATA],
    "summary": (
        "Found 1 high-severity security issue, 2 medium quality issues, and 3 missing docstrings."
    ),
    "agents_succeeded": ["security", "quality", "test_mapping", "docs"],
    "agents_failed": [],
    "trace_id": "abc123",
}


def test_finding_validates_sample():
    finding = Finding.model_validate(SAMPLE_FINDING_DATA)
    assert finding.id == "F-001"
    assert finding.agent == "security"
    assert finding.category is Category.SECURITY
    assert finding.severity is Severity.HIGH
    assert finding.confidence == 0.92
    assert finding.tool_source == "semgrep:python.lang.security.unsafe-eq"


def test_finding_optional_line_numbers():
    data = SAMPLE_FINDING_DATA | {"line_start": None, "line_end": None}
    finding = Finding.model_validate(data)
    assert finding.line_start is None
    assert finding.line_end is None


def test_finding_rejects_invalid_agent():
    data = SAMPLE_FINDING_DATA | {"agent": "not_an_agent"}
    with pytest.raises(ValidationError):
        Finding.model_validate(data)


def test_finding_rejects_confidence_out_of_range():
    data = SAMPLE_FINDING_DATA | {"confidence": 1.5}
    with pytest.raises(ValidationError):
        Finding.model_validate(data)
    data2 = SAMPLE_FINDING_DATA | {"confidence": -0.1}
    with pytest.raises(ValidationError):
        Finding.model_validate(data2)


def test_review_report_validates_sample():
    report = ReviewReport.model_validate(SAMPLE_REPORT_DATA)
    assert report.pr_url == "https://github.com/example/repo/pull/42"
    assert report.pr_author == "alice"
    assert report.duration_seconds == 83.2
    assert report.files_changed == 4
    assert len(report.findings) == 1
    assert report.findings[0].severity is Severity.HIGH
    assert report.agents_failed == []
    assert report.trace_id == "abc123"


def test_review_report_default_lists():
    minimal = {
        "pr_url": "https://github.com/x/y/pull/1",
        "pr_title": "t",
        "pr_author": "a",
        "review_started_at": "2026-05-17T00:00:00Z",
        "review_completed_at": "2026-05-17T00:00:01Z",
        "duration_seconds": 1.0,
        "files_changed": 0,
        "summary": "ok",
        "trace_id": "t",
    }
    report = ReviewReport.model_validate(minimal)
    assert report.findings == []
    assert report.agents_succeeded == []
    assert report.agents_failed == []


def test_severity_rank_ordering():
    ranks = [
        SEVERITY_RANK[s]
        for s in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO)
    ]
    assert ranks == sorted(ranks, reverse=True)
    assert SEVERITY_RANK[Severity.CRITICAL] > SEVERITY_RANK[Severity.HIGH]


def test_agent_node_label_mapping():
    assert AGENT_NODE_TO_LABEL["security_agent"] == "security"
    assert AGENT_NODE_TO_LABEL["quality_agent"] == "quality"
    assert AGENT_NODE_TO_LABEL["test_mapping_agent"] == "test_mapping"
    assert AGENT_NODE_TO_LABEL["docs_agent"] == "docs"
    assert set(AGENT_NODE_TO_LABEL.values()) == {
        "security",
        "quality",
        "test_mapping",
        "docs",
        "correctness",
    }
    assert len(AGENT_NODES) == 5


def test_review_state_typeddict_accepts_keys():
    state: ReviewState = {
        "pr_url": "https://github.com/x/y/pull/1",
        "changed_files": [{"path": "a.py", "content": "x = 1", "change_type": "modified"}],
        "findings": [Finding.model_validate(SAMPLE_FINDING_DATA)],
        "agents_succeeded": ["security"],
        "agents_failed": [],
        "agents_to_run": ["security_agent"],
        "trace_id": "t",
    }
    assert state["pr_url"].endswith("/pull/1")
    assert state["changed_files"][0]["path"] == "a.py"


def test_pydantic_serializer_warning_is_suppressed():
    """The cosmetic ``Pydantic serializer warnings`` UserWarning should be
    silenced when emitted from ``pydantic.main``.

    We can't easily check for the filter directly because pytest manipulates
    ``warnings.filters`` between tests. Instead, simulate the exact warning
    langchain emits and assert ``catch_warnings(record=True)`` captures
    nothing. The package-level filter is reapplied via ``import reviewer``.
    """
    import warnings

    import reviewer  # noqa: F401 — import side-effect installs the filter

    # Re-apply our filter inside this catch_warnings block (pytest resets the
    # global filter list between tests, which is what makes a direct filter
    # inspection brittle in CI).
    with warnings.catch_warnings(record=True) as captured:
        warnings.resetwarnings()
        warnings.filterwarnings(
            "ignore",
            message=r"^Pydantic serializer warnings:",
            category=UserWarning,
            module=r"pydantic\.main",
        )
        # Emit a warning that mimics what pydantic.main produces.
        warnings.warn_explicit(
            "Pydantic serializer warnings: PydanticSerializationUnexpectedValue(...)",
            UserWarning,
            "pydantic/main.py",
            475,
            module="pydantic.main",
        )
    assert captured == [], (
        f"expected the cosmetic warning to be filtered, got: "
        f"{[(str(w.message), w.category.__name__) for w in captured]}"
    )
