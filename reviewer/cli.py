"""Typer CLI entrypoint for the multi-agent code reviewer.

Usage::

    reviewer review <PR_URL> [OPTIONS]

The CLI is a thin synchronous shim around ``reviewer.runner.run_review``.
Run ``reviewer --help`` for top-level options, or ``reviewer review --help``
for the full review-command options.
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

from reviewer import __version__
from reviewer.runner import run_review, run_review_diff

CLI_HELP = """\
Multi-agent code reviewer for GitHub pull requests and local diffs.

Routes up to five specialist agents — security, correctness, quality,
test mapping and documentation — then independently verifies candidates.
Returns an auditable JSON ReviewReport. Default mode is offline mock.

Run ``reviewer review --help`` for the full review-command options.

EXAMPLES

  reviewer review https://github.com/owner/repo/pull/42
  reviewer review-diff --diff review.diff --repo-path . --output report.json
  reviewer review <PR_URL> --live --output report.json
  reviewer publish --report report.json --post-comments
  reviewer review <PR_URL> --only security,quality
  reviewer --env-file ~/.config/reviewer.env review <PR_URL>

ENVIRONMENT

  ANTHROPIC_API_KEY   Anthropic API key (default LLM provider)
  OPENAI_API_KEY      OpenAI API key (when LLM_PROVIDER=openai)
  LLM_PROVIDER        anthropic (default) | openai | qwen
  DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, DASHSCOPE_MODEL
                      Required with qwen
  LLM_MODEL           Override the default model
  GITHUB_TOKEN        Required for private repos and --post-comments
  REVIEWER_PLANNER_MODE    rules (default) | hybrid

DOCS
  https://github.com/OmkumarSolanki/multi-agent-code-reviewer
"""

REVIEW_HELP = """\
Review a GitHub Pull Request and emit a structured JSON report.

The pipeline:
  1. Fetch PR diff + changed files; shallow-clone the head branch.
  2. Build a risk plan and dispatch selected specialists with Send.
  3. Gather bounded evidence and propose candidate findings.
  4. Judge independently checks anchors, evidence and causal claims.
  5. Aggregate confirmed findings and retain raw/uncertain/rejected.

EXAMPLES

  reviewer review https://github.com/owner/repo/pull/42
  reviewer review <PR_URL> --output report.json
  reviewer review <PR_URL> --live --output report.json
  reviewer publish --report report.json --post-comments
  reviewer review <PR_URL> --only security,quality
  reviewer review <PR_URL> --verbose
"""


app = typer.Typer(
    add_completion=False,
    help=CLI_HELP,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"reviewer {__version__}")
        raise typer.Exit()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # SDK debug logs can contain request bodies or credentials even in verbose mode.
    for name in ("httpx", "httpcore", "anthropic", "openai", "langgraph", "langchain"):
        logging.getLogger(name).setLevel(logging.WARNING)


@app.callback()
def _root(
    env_file: Path | None = typer.Option(  # noqa: B008
        None,
        "--env-file",
        metavar="PATH",
        help="Path to a .env file to load before running. Defaults to ./.env",
        exists=False,
        dir_okay=False,
        show_default=False,
    ),
    version: bool | None = typer.Option(  # noqa: B008
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the reviewer version and exit.",
    ),
):
    """Multi-agent code reviewer — load env vars, then dispatch a subcommand."""
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()


@app.command(help=REVIEW_HELP, no_args_is_help=True)
def review(
    pr_url: str = typer.Argument(
        ...,
        metavar="PR_URL",
        help="GitHub pull-request URL (e.g. https://github.com/owner/repo/pull/42).",
    ),
    live: bool = typer.Option(False, "--live", help="Allow paid model/static-tool requests."),
    output: Path | None = typer.Option(  # noqa: B008
        None,
        "--output",
        "-o",
        metavar="PATH",
        help="Write the JSON report to this path. Defaults to stdout.",
        show_default=False,
    ),
    post_comments: bool = typer.Option(
        False,
        "--post-comments",
        help=(
            "Post a single Markdown summary comment to the PR. "
            "Requires GITHUB_TOKEN with write access."
        ),
    ),
    only: str | None = typer.Option(
        None,
        "--only",
        metavar="AGENTS",
        help=(
            "Run ONLY these agents (comma-separated). Overrides the planner. "
            "Valid: security, correctness, quality, test_mapping, docs."
        ),
        show_default=False,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Verbose logging (DEBUG level on stderr).",
    ),
    github_token: str | None = typer.Option(  # noqa: B008
        None,
        "--github-token",
        metavar="TOKEN",
        help="GitHub token. Defaults to the GITHUB_TOKEN environment variable.",
        envvar="GITHUB_TOKEN",
        show_default=False,
        show_envvar=False,
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
                **({"live": True} if live else {}),
            )
        )
    except KeyboardInterrupt:
        typer.echo("Interrupted.", err=True)
        raise typer.Exit(code=130) from None
    except Exception as e:  # noqa: BLE001
        typer.echo(f"error: {type(e).__name__}", err=True)
        raise typer.Exit(code=1) from e

    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
        typer.echo(f"wrote {output}", err=True)
    else:
        typer.echo(payload)


@app.command("publish")
def publish(
    report: Path = typer.Option(..., "--report", exists=True, dir_okay=False),  # noqa: B008
    post_comments: bool = typer.Option(False, "--post-comments"),
    allow_update: bool = typer.Option(False, "--allow-update"),
):
    """Preview reviewed JSON; explicit --post-comments authorizes GitHub writing."""
    from reviewer.models.report import ReviewReport
    from reviewer.publication import publish_report

    try:
        value = ReviewReport.model_validate_json(report.read_text(encoding="utf-8"))
        result = publish_report(
            value.model_dump(mode="json"),
            os.environ.get("GITHUB_TOKEN"),
            post_comments=post_comments,
            allow_update=allow_update,
        )
    except Exception as exc:
        typer.echo(f"publication failed: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from None
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@app.command("review-diff")
def review_diff(
    diff: Path = typer.Option(..., "--diff", exists=True, dir_okay=False),  # noqa: B008
    repo_path: Path = typer.Option(..., "--repo-path", exists=True, file_okay=False),  # noqa: B008
    output: Path | None = typer.Option(None, "--output", "-o"),  # noqa: B008
    live: bool = typer.Option(False, "--live", help="Allow paid API requests."),
    only: str | None = typer.Option(None, "--only"),
):
    """Review a local git unified diff; offline mock by default, no GitHub token."""
    try:
        result = asyncio.run(
            run_review_diff(
                diff,
                repo_path,
                only=only.split(",") if only else None,
                live=live,
            )
        )
    except (ValueError, OSError) as exc:
        typer.echo(f"error: {type(exc).__name__}: invalid diff/repository", err=True)
        raise typer.Exit(1) from None
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    else:
        typer.echo(payload)


if __name__ == "__main__":
    app()
