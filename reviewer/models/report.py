from pydantic import BaseModel, Field

from reviewer.models.findings import Finding


class AgentFailure(BaseModel):
    agent: str
    error: str
    stage: str | None = None


class ReviewReport(BaseModel):
    pr_url: str
    pr_title: str
    pr_author: str
    review_started_at: str
    review_completed_at: str
    duration_seconds: float
    files_changed: int
    findings: list[Finding] = Field(default_factory=list)
    summary: str
    agents_succeeded: list[str] = Field(default_factory=list)
    agents_failed: list[AgentFailure] = Field(default_factory=list)
    trace_id: str
    input_mode: str = "github"
    head_sha: str | None = None
    diff_sha256: str | None = None
    reviewed_files: list[str] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)
    tool_evidence: list[dict] = Field(default_factory=list)
    budget_events: list[dict] = Field(default_factory=list)
    source_errors: list[dict] = Field(default_factory=list)
    execution_mode: str = "live"
    review_plan: dict = Field(default_factory=dict)
    planner_errors: list[dict] = Field(default_factory=list)
    status: str = "completed"
    raw_findings: list[Finding] = Field(default_factory=list)
    verified_findings: list[dict] = Field(default_factory=list)
    verification_incomplete: bool = False
    judge_enabled: bool = True
    verification_conflicts: list[dict] = Field(default_factory=list)
    run_id: str | None = None
    review_version: str = "2.0.0"
    prompt_version: str = "review-v2.0"
    budget: dict = Field(default_factory=dict)
    usage: dict = Field(default_factory=dict)
    trace: list[dict] = Field(default_factory=list)
    unreviewed_dimensions: list[str] = Field(default_factory=list)
    publication: dict = Field(default_factory=lambda: {"status": "dry_run"})
