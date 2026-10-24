"""FastAPI HTTP entrypoint.

::

    uvicorn reviewer.api:app --host 0.0.0.0 --port 8000

    POST /review   — run the review pipeline; returns the ReviewReport JSON.
    GET  /health   — liveness probe.

Native async — reuses ``reviewer.runner.run_review`` so behavior is identical
between this and the CLI.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from reviewer.runner import run_review

log = logging.getLogger(__name__)


class ReviewRequest(BaseModel):
    """Request body for ``POST /review``."""

    pr_url: str = Field(..., description="GitHub PR URL.")
    live: bool = False
    options: dict | None = None
    agents: list[str] | None = Field(
        default=None,
        description=("Optional whitelist of agents (short labels). If set, overrides the planner."),
    )
    post_comments: bool = Field(
        default=False, description="Post a Markdown summary comment to the PR."
    )
    github_token: str | None = Field(
        default=None,
        description=(
            "Optional override of the server's $GITHUB_TOKEN. "
            "Useful for multi-tenant deployments. Treated as opaque."
        ),
    )


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"


app = FastAPI(
    title="Multi-Agent Code Reviewer",
    description="Reviews GitHub PRs by dispatching specialized AI agents in parallel.",
    version="0.1.0",
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.post("/review")
async def review(request: ReviewRequest) -> dict[str, Any]:
    if request.post_comments:
        raise HTTPException(
            status_code=403,
            detail="API publishing disabled; review JSON then use CLI publish explicitly",
        )
    token = request.github_token or os.environ.get("GITHUB_TOKEN") or None
    try:
        report = await run_review(
            request.pr_url,
            github_token=token,
            only=request.agents,
            post_comments=request.post_comments,
            **({"live": True} if request.live else {}),
            **({"options": request.options} if request.options is not None else {}),
        )
    except ValueError as e:
        # PR-URL parse failures, etc.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.exception("review failed for %s", request.pr_url)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)) from e

    if not report:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="review pipeline returned an empty report",
        )
    return report
