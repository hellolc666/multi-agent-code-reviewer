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
from reviewer.runner import run_review

CLI_HELP = """\
Multi-agent code reviewer for GitHub pull requests.

Dispatches four specialist AI agents in parallel — security, quality,
test mapping, documentation — each pairing a deterministic tool
(Semgrep, Ruff, Python AST) with an LLM for explanations and fix
suggestions. Returns a structured JSON ReviewReport.

Run ``reviewer review --help`` for the full review-command options.

EXAMPLES

  reviewer review https://github.com/owner/repo/pull/42
  reviewer review <PR_URL> --output report.json --post-comments
  reviewer review <PR_URL> --only security,quality
  reviewer --env-file ~/.config/reviewer.env review <PR_URL>

ENVIRONMENT

  ANTHROPIC_API_KEY   Anthropic API key (default LLM provider)
  OPENAI_API_KEY      OpenAI API key (when LLM_PROVIDER=openai)
  LLM_PROVIDER        anthropic (default) | openai
  LLM_MODEL           Override the default model
  GITHUB_TOKEN        Required for private repos and --post-comments
  LANGCHAIN_TRACING_V2, LANGCHAIN_API_KEY, LANGCHAIN_PROJECT
                      Optional LangSmith tracing config

DOCS
  https://github.com/OmkumarSolanki/multi-agent-code-reviewer
"""

REVIEW_HELP = """\
Review a GitHub Pull Request and emit a structured JSON report.

The pipeline:
  1. Fetch PR diff + changed files; shallow-clone the head branch.
  2. Plan which specialists to run (deterministic — based on file types).
  3. Dispatch security / quality / test-mapping / docs agents in parallel.
  4. Each agent runs its tool (Semgrep / Ruff / AST), then an LLM call.
  5. Aggregator dedups, sorts by severity, writes a 1–2 sentence summary.

EXAMPLES

  reviewer review https://github.com/owner/repo/pull/42
  reviewer review <PR_URL> --output report.json
  reviewer review <PR_URL> --post-comments
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
    if not verbose:
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
            "Valid: security, quality, test_mapping, docs."
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
