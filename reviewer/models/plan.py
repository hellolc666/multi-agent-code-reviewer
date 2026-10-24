from typing import Literal

from pydantic import BaseModel, Field

from reviewer.models.findings import AgentLabel


class ReviewPlan(BaseModel):
    risk: Literal["low", "medium", "high"] = "low"
    change_summary: str = ""
    affected_files: list[str] = Field(default_factory=list)
    selected_agents: list[AgentLabel] = Field(default_factory=list, max_length=5)
    focus_points: list[str] = Field(default_factory=list)
    routing_reasons: dict[str, str] = Field(default_factory=dict)
    budget: dict = Field(default_factory=dict)
    mode: Literal["rules", "hybrid"] = "rules"
