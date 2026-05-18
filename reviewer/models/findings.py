from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

AgentLabel = Literal["security", "quality", "test_mapping", "docs"]


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


class Category(StrEnum):
    SECURITY = "security"
    QUALITY = "quality"
    TEST_COVERAGE = "test_coverage"
    DOCUMENTATION = "documentation"


class Finding(BaseModel):
    id: str = Field(..., description="unique id within report")
    agent: AgentLabel
    category: Category
    severity: Severity
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    title: str
    explanation: str
    suggestion: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    tool_source: str | None = Field(
        None, description="e.g. 'semgrep-rule-xyz' or 'llm-analysis'"
    )
