"""Typer CLI entrypoint.

::

    reviewer review <PR_URL> [--output report.json] [--post-comments]
                              [--only security,quality] [--verbose]

The CLI is a thin synchronous shim around ``reviewer.runner.run_review``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv

from reviewer.runner import run_review

app = typer.Typer(
    add_completion=False,
    help="Multi-agent code reviewer for GitHub pull requests (Python repos, v1).",
)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Quiet noisy library loggers in non-verbose mode.
    if not verbose:
        for name in ("httpx", "httpcore", "anthropic", "langgraph", "langchain"):
            logging.getLogger(name).setLevel(logging.WARNING)


@app.callback()
def _root(
    env_file: Path | None = typer.Option(  # noqa: B008
        None, "--env-file", help="Path to a .env file to load before running.",
        exists=False, dir_okay=False,
    ),
):
    """Load environment variables from ``.env`` (if present)."""
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()  # default: load CWD's .env if present


@app.command()
def review(
    pr_url: str = typer.Argument(..., help="GitHub PR URL"),
    output: Path | None = typer.Option(  # noqa: B008
        None, "--output", "-o", help="Write the JSON report here. Stdout if omitted.",
    ),
    post_comments: bool = typer.Option(
        False, "--post-comments", help="Post a single Markdown summary comment to the PR.",
    ),
    only: str | None = typer.Option(
        None, "--only", help="Run ONLY these agents (comma-separated). Overrides planner. "
        "Valid: security,quality,test_mapping,docs",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
    github_token: str | None = typer.Option(
        None, "--github-token", help="GitHub token. Defaults to $GITHUB_TOKEN.",
        envvar="GITHUB_TOKEN",
    ),
):
    """Review a GitHub PR and emit a structured JSON report."""
    _setup_logging(verbose)

    only_list = [s.strip() for s in only.split(",")] if only else None
    token = github_token or os.environ.get("GITHUB_TOKEN") or None

    try:
        report = asyncio.run(
            run_review(
                pr_url,
                github_token=token,
                only=only_list,
                post_comments=post_comments,
            )
        )
    except KeyboardInterrupt:
        typer.echo("Interrupted.", err=True)
        raise typer.Exit(code=130) from None
    except Exception as e:  # noqa: BLE001
        typer.echo(f"error: {e}", err=True)
        if verbose:
            logging.getLogger(__name__).exception("review failed")
        raise typer.Exit(code=1) from e

    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)
    else:
        typer.echo(payload)


if __name__ == "__main__":
    app()
