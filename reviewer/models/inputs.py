"""Provider-independent review inputs and exact RIGHT-side diff evidence."""

from typing import Literal

from pydantic import BaseModel, Field


class DiffLine(BaseModel):
    kind: Literal["add", "remove", "context"]
    text: str
    new_line: int | None = None
    old_line: int | None = None


class Hunk(BaseModel):
    header: str
    text: str
    lines: list[DiffLine] = Field(default_factory=list)


class DiffFile(BaseModel):
    path: str
    old_path: str | None = None
    change_type: str = "modified"
    binary: bool = False
    hunks: list[Hunk] = Field(default_factory=list)


class ReviewInput(BaseModel):
    mode: Literal["github", "diff"]
    diff_sha256: str
    head_sha: str | None = None
    files: list[DiffFile] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)
