from operator import add
from typing import Annotated, TypedDict

from reviewer.models.findings import Finding

AGENT_NODE_TO_LABEL: dict[str, str] = {
    "security_agent": "security",
    "quality_agent": "quality",
    "test_mapping_agent": "test_mapping",
    "docs_agent": "docs",
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
