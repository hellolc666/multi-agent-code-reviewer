from typing import Literal

from pydantic import BaseModel, Field, model_validator

from reviewer.models.findings import Finding

VerificationStatus = Literal["confirmed", "rejected", "uncertain"]


class JudgeDecision(BaseModel):
    status: VerificationStatus = Field(description="Exactly one of confirmed, rejected, uncertain.")
    reason: str = Field(
        min_length=3,
        max_length=1500,
        description="Brief evidence-based reason for the decision.",
    )
    reproducible_trigger: str | None = Field(
        default=None,
        description="Concrete condition that reproduces a confirmed defect; null otherwise.",
    )
    evidence_support: bool = Field(
        default=False,
        description="True only if supplied code evidence supports the confirmed defect.",
    )
    style_only: bool = Field(
        default=False,
        description="True if the candidate concerns style alone; such findings are rejected.",
    )

    @model_validator(mode="after")
    def require_confirmation_evidence(self):
        if self.status == "confirmed" and (
            not self.evidence_support or not self.reproducible_trigger
        ):
            raise ValueError("confirmation requires evidence and a reproducible trigger")
        return self


class VerifiedFinding(BaseModel):
    finding: Finding
    status: VerificationStatus
    reason: str
    candidate_ids: list[str] = Field(default_factory=list)
    source_agents: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    can_comment: bool = False
    verification_incomplete: bool = False
