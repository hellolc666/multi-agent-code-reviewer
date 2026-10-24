from operator import add
from typing import Annotated, TypedDict

from reviewer.models.findings import Finding

AGENT_NODE_TO_LABEL: dict[str, str] = {
    "security_agent": "security",
    "quality_agent": "quality",
    "test_mapping_agent": "test_mapping",
    "docs_agent": "docs",
    "correctness_agent": "correctness",
}

AGENT_NODES: tuple[str, ...] = tuple(AGENT_NODE_TO_LABEL.keys())


class ChangedFile(TypedDict):
    path: str
    content: str
    change_type: str


class AgentFailureRecord(TypedDict):
    agent: str
    error: str


class ReviewState(TypedDict, total=False):
    review_input: dict
    context_tools_enabled: bool
    tool_limits: dict
    tool_evidence: Annotated[list[dict], add]
    budget_events: Annotated[list[dict], add]
    source_errors: list[dict]
    execution_mode: str
    review_options: dict
    review_plan: dict
    planner_errors: list[dict]
    raw_findings: list[Finding]
    verified_findings: list[dict]
    verification_incomplete: bool
    judge_enabled: bool
    verification_conflicts: list[dict]
    pr_url: str
    pr_metadata: dict
    pr_diff: str
    changed_files: list[ChangedFile]
    test_files: list[ChangedFile]
    repo_path: str
    agents_to_run: list[str]
    findings: Annotated[list[Finding], add]
    agents_succeeded: Annotated[list[str], add]
    agents_failed: Annotated[list[AgentFailureRecord], add]
    final_report: dict | None
    trace_id: str
