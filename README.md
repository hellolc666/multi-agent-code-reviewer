# Multi-Agent Code Reviewer

[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An AI-assisted code review tool for GitHub pull requests. Four specialist
LangGraph agents (security, quality, test mapping, documentation) run in
parallel against your PR and produce a structured `ReviewReport` with
deduplicated, severity-ranked findings. Pattern: **deterministic tool first,
LLM second** — Semgrep / Ruff / `ast` produce findings; the LLM explains them,
suggests fixes, and adds what the tools missed.

> **Live evidence**: see [DEMO.md](DEMO.md) for six worked examples on a real
> public repo, including the JSON reports and the actual PR comments the
> reviewer posted.

## What it does

```
┌──────────┐   ┌──────────────┐   ┌─────────────────────────┐   ┌───────────┐
│ fetch_pr │──▶│ plan_review  │──▶│  security  │  quality   │──▶│ aggregate │
└──────────┘   └──────────────┘   │  docs      │ test_mapping│   └───────────┘
                                  │   (parallel via Send)    │         │
                                  └─────────────────────────┘         ▼
                                                                ReviewReport
                                                                  (JSON)
```

- **Security agent** — runs Semgrep on the changed files, then asks the LLM to
  explain findings and surface what Semgrep missed (timing attacks, SSRF,
  unsafe deserialization, hardcoded secrets, weak crypto).
- **Quality agent** — runs Ruff, then asks the LLM about complexity,
  maintainability, and design smells the linter can't see.
- **Test mapping agent** — walks the AST of changed Python files, identifies
  public entities, and asks the LLM whether existing tests cover them.
- **Docs agent** — finds public functions / classes / methods missing
  docstrings and suggests one-line summaries.

The aggregator dedups findings on `(file, line, category)`, keeps the highest
severity, sorts by severity → file → line, and asks the LLM for an executive
summary.

## Quick start

```bash
# 1. Install
git clone https://github.com/OmkumarSolanki/multi-agent-code-reviewer.git
cd multi-agent-code-reviewer
uv venv && source .venv/bin/activate     # or: python -m venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"               # or: pip install -e ".[dev]"
brew install semgrep                      # CLI dep, not a Python package

# 2. Configure
cp .env.example .env
# Edit .env: at minimum set ANTHROPIC_API_KEY (or OPENAI_API_KEY) and GITHUB_TOKEN.

# 3. Review a PR
reviewer review https://github.com/owner/repo/pull/42 --output report.json

# 4. Optional: post the summary back to the PR as a Markdown comment
reviewer review https://github.com/owner/repo/pull/42 --post-comments
```

## Features

| Surface | What it does |
|---|---|
| `reviewer review <PR_URL>` | CLI entry point, prints structured JSON to stdout. |
| `--output report.json` | Write the JSON report to a file. |
| `--post-comments` | Post a Markdown summary as a single PR comment, with explicit bot-attribution disclaimer. |
| `--only security,quality` | Override the planner; run a subset of agents. |
| `--verbose` | DEBUG-level stderr logging. |
| `--env-file path/to/.env` | Load a non-default `.env`. |
| `uvicorn reviewer.api:app` | HTTP API at `/review`, `/health`, `/docs`. Same pipeline. |
| `LLM_PROVIDER=anthropic\|openai` | Switch backends. Default `anthropic`. |
| `LLM_MODEL=...` | Override model. Defaults: `claude-sonnet-4-6` / `gpt-5.5`. |
| `REVIEWER_<AGENT>_TIMEOUT_S=...` | Per-agent wall-clock budget. Defaults: 120/90/60/90 (security/quality/test_mapping/docs). |
| `LANGCHAIN_TRACING_V2=true` | Enable LangSmith tracing; each review tagged with `pr_url:`, `author:`, `repo:`. |

## Reliability

- **Retry with exponential backoff** on every LLM and GitHub call (3 attempts,
  1–10s) — transient 5xx / 429 / `httpx.TransportError` retried; 4xx
  propagates immediately.
- **Circuit breaker** per upstream (5 fails / 60s → OPEN 30s → HALF_OPEN) so a
  bad provider day doesn't grind through your token budget.
- **Per-agent timeouts** with proper async cancellation (`asyncio.timeout`,
  subprocess `proc.kill()` on Semgrep / Ruff).
- **Partial-failure tolerance** — an agent crashing is captured in
  `agents_failed`, the others keep running, and the aggregator still produces
  a structured report.
- **Graceful no-applicable-agents path** — non-Python PRs (or PRs with only
  docs/config changes) get a structured report with a clear "v1 is Python
  only" summary and the file-extension breakdown, instead of empty output.

## Configuration

| Env var | Required | Description |
|---|---|---|
| `LLM_PROVIDER` | No | `anthropic` (default) or `openai`. |
| `ANTHROPIC_API_KEY` | When provider=anthropic | Anthropic API key. |
| `OPENAI_API_KEY` | When provider=openai | OpenAI API key. |
| `LLM_MODEL` | No | Override the default model. |
| `GITHUB_TOKEN` | For private repos / `--post-comments` | Personal access token with `repo` scope. |
| `LANGCHAIN_TRACING_V2` | No | `true` to enable LangSmith. |
| `LANGCHAIN_API_KEY` | If tracing on | LangSmith API key. |
| `LANGCHAIN_PROJECT` | No | Default `multi-agent-code-reviewer`. |
| `REVIEWER_SECURITY_TIMEOUT_S` | No | Per-agent timeouts in seconds. |
| `REVIEWER_QUALITY_TIMEOUT_S` | No | (Same.) |
| `REVIEWER_TEST_MAPPING_TIMEOUT_S` | No | (Same.) |
| `REVIEWER_DOCS_TIMEOUT_S` | No | (Same.) |

The Test Mapping agent reads test directories from the **target repo's**
`pyproject.toml`:
```toml
[tool.reviewer]
test_dirs = ["tests", "integration_tests"]   # default: ["tests"]
```

## Demo

Six PRs on a tiny `notes-cli` repo, each engineered to surface one specific
agent (or graceful path):

| PR | Demonstrates |
|---|---|
| [#8](https://github.com/OmkumarSolanki/notes-cli/pull/8) | **Security** — pickle, SSRF, command injection, path traversal |
| [#9](https://github.com/OmkumarSolanki/notes-cli/pull/9) | **Quality** — complexity + unused imports + broad except |
| [#10](https://github.com/OmkumarSolanki/notes-cli/pull/10) | **Docs** — missing docstrings on every public entity |
| [#11](https://github.com/OmkumarSolanki/notes-cli/pull/11) | **Test mapping** — substantial public surface, no tests |
| [#12](https://github.com/OmkumarSolanki/notes-cli/pull/12) | **Graceful path** — non-Python PR (just `.md` changes) |
| [#13](https://github.com/OmkumarSolanki/notes-cli/pull/13) | **Covered path** — code shipped with its tests |

Full walkthroughs (per-PR JSON, posted comments, what the reviewer caught and
what it missed) in [DEMO.md](DEMO.md).

## Tech stack

- **Python 3.11**, async-first.
- **LangGraph** for orchestration (parallel fan-out via the `Send` API).
- **LangChain Anthropic / OpenAI** for the LLM with `with_structured_output`
  binding to Pydantic models.
- **Semgrep + Ruff** as deterministic first-pass tools.
- **Python `ast`** for the test-mapping and docs agents.
- **Pydantic v2** for typed findings + reports.
- **PyGithub + httpx** for GitHub PR fetching.
- **FastAPI + uvicorn** for the HTTP API.
- **Typer** for the CLI.
- **LangSmith** (optional) for tracing.
- **pytest + ruff** in dev deps; **278+ tests, <2s** suite runtime.

## Limitations (v1)

- **Python only.** Non-Python PRs return a structured "no applicable agents"
  report. Multi-language is on the roadmap.
- **Single summary comment.** Inline-per-line PR comments are v2 scope.
- **No auto-fix.** Findings include `suggestion` text only — no patch
  generation.
- **No caching.** Each review re-runs the full pipeline.
- **No webhook integration.** You invoke it manually via CLI / API.

## License

MIT — see [LICENSE](LICENSE).
