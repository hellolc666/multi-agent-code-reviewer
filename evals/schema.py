"""Frozen corpus contract; gold data is consumed by the evaluator only."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reviewer.tools.context import safe_relative
from reviewer.tools.diff_input import make_input


class GoldLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    file_path: str
    line: int = Field(ge=1)
    title: str
    defect: str
    trigger: str
    severity: Literal["high", "medium", "low"]
    evidence: str
    category: Literal["logic", "security", "regression"]


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    split: Literal["dev", "test"]
    source: str
    annotation_status: Literal["curated_pending_human_signoff", "human_confirmed"]
    reviewed_by: str
    review_note: str
    repo_files: dict[str, str]
    diff: str
    gold_labels: list[GoldLabel]

    @model_validator(mode="after")
    def validate_locations(self):
        value, _ = make_input(self.diff, mode="diff")
        added = {
            (f.path, line.new_line)
            for f in value.files
            for hunk in f.hunks
            for line in hunk.lines
            if line.kind == "add"
        }
        for path in self.repo_files:
            safe_relative(path)
        if len({g.id for g in self.gold_labels}) != len(self.gold_labels):
            raise ValueError("duplicate gold id")
        for gold in self.gold_labels:
            lines = self.repo_files[gold.file_path].splitlines()
            if (gold.file_path, gold.line) not in added or lines[gold.line - 1] != gold.evidence:
                raise ValueError("gold must anchor to exact added line")
        return self

    def reviewer_input(self):
        """Explicit allowlist. Never serialize self into a model prompt."""
        return {"repo_files": dict(self.repo_files), "diff": self.diff}
