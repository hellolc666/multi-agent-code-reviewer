"""Loopback-only browser workbench for the existing local diff review pipeline."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from reviewer.runner import run_review_diff

log = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_DIFF_BYTES = 4 * 1024 * 1024
AGENTS = frozenset({"correctness", "security", "quality", "test_mapping", "docs"})


class LocalReviewRequest(BaseModel):
    diff_text: str = Field(min_length=1, max_length=MAX_DIFF_BYTES)
    repo_path: str = Field(min_length=1, max_length=2048)
    live: bool = False
    agents: list[str] = Field(default_factory=list, max_length=5)


app = FastAPI(title="Code Review Workbench", docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    page = Path(__file__).parent / "static" / "review.html"
    return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/api/example")
async def example() -> dict:
    return {
        "diff_text": (PROJECT_ROOT / "examples" / "demo-logic.diff").read_text(encoding="utf-8"),
        "repo_path": str(PROJECT_ROOT / "examples" / "demo-logic-repo"),
    }


@app.post("/api/review")
async def review(request: LocalReviewRequest) -> dict:
    if len(request.diff_text.encode("utf-8")) > MAX_DIFF_BYTES:
        raise HTTPException(413, "Diff exceeds the 4 MiB limit")
    if set(request.agents) - AGENTS:
        raise HTTPException(400, "Unknown reviewer selection")
    repo = Path(request.repo_path).expanduser()
    if not repo.is_dir():
        raise HTTPException(400, "Repository directory does not exist")

    # Reuse the same bounded parser, context reader, agents and Judge as the CLI.
    # The temporary diff is removed even when review fails.
    try:
        with tempfile.TemporaryDirectory(prefix="reviewer-ui-") as temp_dir:
            diff_path = Path(temp_dir) / "input.diff"
            diff_path.write_text(request.diff_text, encoding="utf-8")
            return await run_review_diff(
                diff_path, repo, live=request.live, only=request.agents or None
            )
    except (ValueError, UnicodeError, OSError) as exc:
        log.warning("local_ui_invalid_input error_type=%s", type(exc).__name__)
        raise HTTPException(400, "Invalid diff or repository content") from None
    except Exception as exc:
        log.error("local_ui_review_failed error_type=%s", type(exc).__name__)
        raise HTTPException(500, "Review failed; inspect server logs for the error type") from None
