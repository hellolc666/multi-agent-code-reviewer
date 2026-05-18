# Demo — multi-agent-code-reviewer in action

This document walks through six pull requests on a small public Python repo
(`notes-cli`) that were each engineered to surface one specific behaviour of
the reviewer. For each PR you'll find: what's in the diff, what the reviewer
actually produced (real numbers from a real run, not invented output), and
what the run tells you about how the system behaves.

The captured JSON reports live under [`examples/notes_cli/`](examples/notes_cli)
so you can inspect them directly without re-running anything.

---

## The demo target

[**`OmkumarSolanki/notes-cli`**](https://github.com/OmkumarSolanki/notes-cli)
is a deliberately tiny Python CLI for managing local notes. Clean baseline
on `main` (Note dataclass, JSON-on-disk store, three Typer commands, two test
files). The six demo PRs each layer one new module — or one set of non-Python
changes — on top of `main`, so the diff each agent sees is small and
well-scoped.

Project layout on `main`:

```
notes-cli/
├── pyproject.toml
├── README.md
├── notes/
│   ├── __init__.py
│   ├── model.py         # Note dataclass
│   ├── store.py         # NoteStore: load/save/add/list/get
│   └── cli.py           # typer commands: add, list, show
└── tests/
    ├── test_model.py
    └── test_store.py
```

## The six PRs at a glance

| # | Branch | Demonstrates | Total findings | Duration | Agents that ran |
|---|---|---|---:|---:|---|
| [8](https://github.com/OmkumarSolanki/notes-cli/pull/8)  | `demo/security`     | Security agent on a fresh attack surface (pickle, SSRF, command injection, path traversal) | 14 | 43s | all 4 |
| [9](https://github.com/OmkumarSolanki/notes-cli/pull/9)  | `demo/quality`      | `--only security,quality` flag (planner override) | 5 | 41s | security + quality |
| [10](https://github.com/OmkumarSolanki/notes-cli/pull/10) | `demo/docs`        | `--env-file` flag + docs-agent emphasis | 19 | 32s | all 4 |
| [11](https://github.com/OmkumarSolanki/notes-cli/pull/11) | `demo/test-mapping` | `--verbose` DEBUG logs + test-mapping focus | 9 | 33s | all 4 |
| [12](https://github.com/OmkumarSolanki/notes-cli/pull/12) | `demo/no-python`    | Graceful "no applicable agents" path on a `.md`-only PR | 0 | 2s | none (by design) |
| [13](https://github.com/OmkumarSolanki/notes-cli/pull/13) | `demo/covered`      | `REVIEWER_DOCS_TIMEOUT_S` env var + cross-agent dedup | 17 | 62s | all 4 |

Every run posted a single summary comment back to the PR with a clear
bot-attribution disclaimer (see "Posted comments" below). Total time across
all six runs: under four minutes.

---

## PR #8 — security demo

**Branch:** `demo/security` &nbsp; **PR:** [#8](https://github.com/OmkumarSolanki/notes-cli/pull/8)

### What's in the diff

Adds `notes/import_.py` — a note-import module that deliberately stacks five
classic Python security smells: `pickle.loads` on a user-controlled file,
`urllib.request.urlopen` with no scheme/host allowlist (SSRF), `subprocess.run`
with `shell=True` and an f-string command, `os.path.expanduser` on unvalidated
paths (path traversal), and a hardcoded `/tmp/notes-import` working directory.
Misdirecting comments like `# Trusted internal format used by our sync helper.`
are present to test whether the LLM follows the comment or reads the code.

### What the reviewer found

- **Total findings:** 14 across `security` (5), `quality` (5), `test_mapping` (4).
- **Severity breakdown:** 2 HIGH · 10 MEDIUM · 2 LOW.
- **All four agents succeeded.** No timeouts, no failures.
- **Duration:** 43 seconds.

Top findings (full report: [`examples/notes_cli/pr8_security.json`](examples/notes_cli/pr8_security.json)):

| Severity | Agent | Title | Confidence | Source |
|---|---|---|---:|---|
| **HIGH** | security | Unsafe deserialization with `pickle.loads` | 0.98 | `llm-analysis` |
| **HIGH** | security | Command injection via subprocess `shell=True` | 0.99 | Semgrep `subprocess-shell-true` |
| MEDIUM | test_mapping | Uncovered function `import_from_file` | 0.90 | `coverage-classification` |

LLM-generated executive summary verbatim:

> The PR has blocking security issues in notes/import_.py: unsafe pickle
> deserialization and shell-based archive extraction/command execution enable
> code execution. Additional SSRF, unsafe extraction, quality, and coverage
> gaps need follow-up.

### What the run tells you

Both Semgrep and the LLM contributed real findings. Semgrep nailed the
`shell=True` rule with 0.99 confidence (its built-in rule fires reliably);
the LLM caught the `pickle.loads` issue on its own at 0.98 confidence and
wrote an explicit "code-execution-capable by design" explanation. The
misdirecting comments did not influence the verdict — the LLM read the actual
code path and surfaced the risk anyway. This is the "deterministic tool first,
LLM second" pattern doing exactly what it's supposed to: mechanically reliable
findings from the tool, plus higher-context findings the tool's rules don't
have.

---

## PR #9 — quality demo (with `--only` flag)

**Branch:** `demo/quality` &nbsp; **PR:** [#9](https://github.com/OmkumarSolanki/notes-cli/pull/9)

### What's in the diff

Adds `notes/search.py` — search helpers for the note store. Quality-targeted
smells: two unused imports preserved with `noqa` comments ("might need this
later"), a single deeply-nested `search()` function with high cyclomatic
complexity, repetitive broad `try/except Exception` blocks, and an unused
`json` import that's only consumed by a contrived dedup key.

### What the reviewer found

- **Total findings:** 5, all from the `quality` agent.
- **Severity breakdown:** 1 MEDIUM · 4 LOW.
- **`--only security,quality` flag worked:** only `security` and `quality`
  ran (succeeded list reflects this); docs and test_mapping correctly skipped.
- **Duration:** 41 seconds.

Top findings (full report: [`examples/notes_cli/pr9_quality.json`](examples/notes_cli/pr9_quality.json)):

| Severity | Agent | Title | Confidence | Source |
|---|---|---|---:|---|
| **MEDIUM** | quality | Age filter silently rejects valid naive ISO timestamps | 0.78 | `llm-analysis` |
| LOW | quality | Unused imports are suppressed for hypothetical future use | 0.96 | `llm-analysis` |
| LOW | quality | Search function is overly complex and duplicates parsing logic | 0.93 | `llm-analysis` |

LLM-generated executive summary verbatim:

> The main concern is a medium-severity quality issue where the age filter
> can silently reject valid naive ISO timestamps. Other findings are
> low-severity maintainability and robustness issues in search parsing,
> exception handling, imports, and deduplication.

### What the run tells you

The `--only` planner override did exactly what it said on the tin —
`agents_succeeded` is `["security", "quality"]`, no docs or test_mapping
findings, no docs or test_mapping entries in `agents_failed`. The most
interesting finding wasn't the unused imports (which Ruff would flag anyway
at 0.96); it was the LLM noticing that the `age:` filter compares a *naive*
parsed timestamp to an *aware* `now()`, which would crash at runtime — that
kind of subtle correctness issue is exactly what the quality agent is for.

---

## PR #10 — docs demo (with `--env-file` flag)

**Branch:** `demo/docs` &nbsp; **PR:** [#10](https://github.com/OmkumarSolanki/notes-cli/pull/10)

### What's in the diff

Adds `notes/export.py` — JSON / CSV / Markdown exporters. The code itself is
clean (no security or complexity issues) but ships with **zero docstrings**:
no module docstring, no `Exporter` class docstring, no method docstrings on
the five `Exporter` methods, and no docstrings on the two free functions.

### What the reviewer found

- **Total findings:** 19 across `test_mapping` (8), `docs` (7), `security` (2),
  `quality` (2).
- **Severity breakdown:** 10 MEDIUM · 9 LOW.
- **All four agents succeeded.** `--env-file .env` correctly loaded the local
  `.env` (the flag has to come *before* the `review` subcommand because it's
  attached to the root callback).
- **Duration:** 32 seconds.

Top findings (full report: [`examples/notes_cli/pr10_docs.json`](examples/notes_cli/pr10_docs.json)):

| Severity | Agent | Title | Confidence | Source |
|---|---|---|---:|---|
| MEDIUM | test_mapping | Missing test coverage for `Exporter` class | 0.90 | `coverage-classification` |
| MEDIUM | test_mapping | Missing test coverage for `Exporter.__init__` | 0.90 | `coverage-classification` |
| MEDIUM | test_mapping | Missing test coverage for `Exporter.to_json` | 0.90 | `coverage-classification` |

LLM-generated executive summary verbatim:

> The PR has medium-severity security and quality issues in export handling,
> notably CSV formula injection and potential JSON timestamp serialization
> failures. Test coverage for the new export module is also broadly missing.

### What the run tells you

Two interesting things came out of this run. First, the docs agent landed
seven LOW findings — one for each missing docstring on the public surface —
exactly as designed. Second, the LLM volunteered a finding nobody asked for:
**CSV formula injection** — the `to_csv` method writes user-controlled note
text into a CSV without escaping leading `=`, `+`, `-`, `@`. That's a real
security issue the security agent caught despite the PR being framed as a
docs demo. Two LOW docstring findings + a serious security finding from the
same diff is exactly the kind of cross-cutting feedback you can't get from
single-purpose linters.

---

## PR #11 — test-mapping demo (with `--verbose`)

**Branch:** `demo/test-mapping` &nbsp; **PR:** [#11](https://github.com/OmkumarSolanki/notes-cli/pull/11)

### What's in the diff

Adds `notes/sharing.py` — note sharing via base64-encoded JSON tokens. New
public surface (`ShareToken` dataclass, `issue_token`, `parse_token`,
`redeem`) ships with **no new test file**. `--verbose` was added to this run
so the DEBUG logs show the full per-agent decision flow on stderr.

### What the reviewer found

- **Total findings:** 9 across `test_mapping` (5), `security` (2),
  `quality` (2).
- **Severity breakdown:** 1 HIGH · 7 MEDIUM · 1 LOW.
- **All four agents succeeded.** Verbose logging surfaced the planner's
  decision and each agent's progress.
- **Duration:** 33 seconds.

Top findings (full report: [`examples/notes_cli/pr11_test_mapping.json`](examples/notes_cli/pr11_test_mapping.json)):

| Severity | Agent | Title | Confidence | Source |
|---|---|---|---:|---|
| **HIGH** | security | Share tokens are tamperable because they are not signed | 0.98 | `llm-analysis` |
| MEDIUM | quality | Docstring claims signed tokens but implementation is only encoded | 0.90 | `llm-analysis` |
| MEDIUM | test_mapping | Uncovered `ShareToken` class | 0.90 | (test-mapping) |

LLM-generated executive summary verbatim:

> The PR has a blocking security issue: share tokens are only encoded, not
> signed, so they can be tampered with. There are also robustness,
> documentation, and test coverage gaps in the sharing token flow.

### What the run tells you

The test-mapping agent worked as designed (every public function in the new
module flagged uncovered), but the highlight of this run came from elsewhere:
the security agent caught a **HIGH-severity tamperable-token issue** that the
PR author would only spot once an attacker abused it in production, and the
quality agent noticed the **module docstring lies** (says "signed" but the
implementation is only base64-encoded). That cross-agent reading of the same
file — security on the implementation, quality on the docstring/code mismatch
— is the dedup logic doing useful work: same file, same line range, two
different categories, both surface separately because the dedup key is
`(file, line, category)` not just `(file, line)`.

---

## PR #12 — graceful no-applicable-agents path

**Branch:** `demo/no-python` &nbsp; **PR:** [#12](https://github.com/OmkumarSolanki/notes-cli/pull/12)

### What's in the diff

A pure-documentation PR — one-line README copy edit plus a new `CONTRIBUTING.md`.
**Zero `.py` files touched.** This is the regression test for a real bug we
fixed earlier in the build: the planner used to return an empty agent list
on non-Python PRs, which let the graph dead-end and the runner emit literal
`{}` to stdout. The fix was to fall through to `aggregate` even when no
agents apply, so the graph always produces a structured `ReviewReport`.

### What the reviewer found

- **Total findings:** 0 (by design).
- **Agents succeeded / failed:** both empty — no agent was applicable.
- **Duration:** 2 seconds (no LLM calls, no Semgrep, no Ruff).

LLM-generated executive summary verbatim (this is deterministic, *not*
LLM-generated, on this path):

> No applicable agents for this PR. v1 reviews Python repositories only
> (this PR contains: 2 .md). Run on a PR that touches .py files to get a
> full review.

### What the run tells you

This is the most boring-looking output and the most important one to
preserve. A non-Python PR shouldn't produce noise findings (any single .py
agent would either crash or emit garbage on a Markdown diff), but it also
shouldn't produce nothing — empty stdout would be indistinguishable from a
runner crash. The fix routes empty `agents_to_run` directly to `aggregate`,
which detects the empty state and writes a deterministic, helpful summary
listing the file extensions seen. Two seconds end-to-end because there are
no LLM round-trips. Full report: [`examples/notes_cli/pr12_no_python.json`](examples/notes_cli/pr12_no_python.json).

---

## PR #13 — covered path + env-var timeout

**Branch:** `demo/covered` &nbsp; **PR:** [#13](https://github.com/OmkumarSolanki/notes-cli/pull/13)

### What's in the diff

Wires `notes/search.py` into the CLI as a new `notes search` subcommand and
ships **with** a focused test file (`tests/test_search.py`, 9 tests covering
`parse_age`, `matches`, `search`, and the CLI command via Typer's
`CliRunner`). The shipping-with-tests aspect is what the test-mapping agent
should detect — its findings should be quieter than on PR #11. Run with
`REVIEWER_DOCS_TIMEOUT_S=180` to demonstrate the per-agent timeout override.

### What the reviewer found

- **Total findings:** 17 across `docs` (9), `test_mapping` (5),
  `quality` (3).
- **Severity breakdown:** 7 MEDIUM · 10 LOW.
- **All four agents succeeded** (the bumped docs timeout meant the docs agent
  could finish even with the larger `cli.py` + `search.py` surface area).
- **Duration:** 62 seconds — longest of the six runs because it's the largest
  diff (3 files vs 1) and the docs agent had the most public entities to
  examine.

Top findings (full report: [`examples/notes_cli/pr13_covered.json`](examples/notes_cli/pr13_covered.json)):

| Severity | Agent | Title | Confidence | Source |
|---|---|---|---:|---|
| MEDIUM | test_mapping | Uncovered CLI `list` command | 0.90 | (test-mapping) |
| MEDIUM | test_mapping | Uncovered CLI `show` command | 0.90 | (test-mapping) |
| MEDIUM | test_mapping | Uncovered token matching helper `_token_matches` | 0.90 | (test-mapping) |

LLM-generated executive summary verbatim:

> No high-severity blockers were found, but several medium issues remain
> around search correctness: tag matching uses partial substrings and age
> filtering can crash on naive timestamps. Test coverage gaps also affect
> CLI commands and search helpers.

### What the run tells you

Two things to notice. First, the test-mapping agent got noticeably quieter
on the new search code itself — `search()`, `matches()`, and `parse_age` are
all referenced by the new `tests/test_search.py`, so the agent flagged
*untouched-by-this-PR* CLI commands instead of the new search module. That's
the agent doing its job: a covered path produces fewer "uncovered" findings,
not zero, because it can still see other public entities in the same file.
Second, the docs timeout override worked transparently — without
`REVIEWER_DOCS_TIMEOUT_S=180` the docs agent might have brushed up against
the default 90s budget given the larger surface area, but with the override
it had room. The `--verbose` log confirmed the resolved timeout in the
`asyncio.timeout` boundary.

---

## Posted comments

Every run except PR #12 (which has no findings to comment on) posted a
single Markdown summary comment back to the PR. The first line of every
comment is unmistakable bot attribution:

```markdown
## ⚠️ This is an automated comment — NOT written by @OmkumarSolanki

🤖 Generated by [multi-agent-code-reviewer](...), an AI-assisted code review tool.
The account shown as the commenter only owns the API token used to post it
— they did not write any of the analysis below. ...
```

You can see the comments live on each PR thread:
[#8](https://github.com/OmkumarSolanki/notes-cli/pull/8),
[#9](https://github.com/OmkumarSolanki/notes-cli/pull/9),
[#10](https://github.com/OmkumarSolanki/notes-cli/pull/10),
[#11](https://github.com/OmkumarSolanki/notes-cli/pull/11),
[#13](https://github.com/OmkumarSolanki/notes-cli/pull/13).

The footer of every comment includes the trace ID from the JSON report so
you can correlate a comment to a LangSmith trace if tracing is enabled.

---

## Reproduce this demo yourself

The exact six commands that produced the JSON files in this directory:

```bash
# Setup (once per terminal)
cd ~/Documents/Projects/multi-agent-code-reviewer
source .venv/bin/activate
mkdir -p examples/notes_cli
export GITHUB_TOKEN="$(gh auth token)"   # or paste a classic PAT

# 1. Security demo + post comment
reviewer review https://github.com/OmkumarSolanki/notes-cli/pull/8 \
  --post-comments \
  --output examples/notes_cli/pr8_security.json

# 2. Quality demo with --only flag
reviewer review https://github.com/OmkumarSolanki/notes-cli/pull/9 \
  --only security,quality \
  --post-comments \
  --output examples/notes_cli/pr9_quality.json

# 3. Docs demo with --env-file (note: --env-file MUST come before `review`)
reviewer --env-file .env review https://github.com/OmkumarSolanki/notes-cli/pull/10 \
  --post-comments \
  --output examples/notes_cli/pr10_docs.json

# 4. Test-mapping demo with --verbose
reviewer review https://github.com/OmkumarSolanki/notes-cli/pull/11 \
  --verbose \
  --post-comments \
  --output examples/notes_cli/pr11_test_mapping.json

# 5. Graceful no-applicable-agents path
reviewer review https://github.com/OmkumarSolanki/notes-cli/pull/12 \
  --post-comments \
  --output examples/notes_cli/pr12_no_python.json

# 6. Covered path with per-agent timeout override
REVIEWER_DOCS_TIMEOUT_S=180 reviewer review https://github.com/OmkumarSolanki/notes-cli/pull/13 \
  --post-comments \
  --output examples/notes_cli/pr13_covered.json
```

Quick comparison helper for the resulting JSON files:

```bash
for f in examples/notes_cli/pr*.json; do
  echo "=== $(basename "$f") ==="
  jq '{duration: .duration_seconds, findings: (.findings | length),
       by_agent: (.findings | group_by(.agent) | map({(.[0].agent): length}) | add // {}),
       agents_failed}' "$f"
done
```

---

## What success looks like — what failure looks like

When everything's working, you should see:

- **All four `agents_succeeded`** (or just the `--only` set) and an empty
  `agents_failed` list on a normal Python PR.
- A LLM-generated `summary` that's specific to the diff (not the fallback
  string `Review complete — see findings below.`).
- Per-finding `tool_source` values that mix Semgrep/Ruff rule IDs with
  `llm-analysis` and `coverage-classification` — that mix is the
  "deterministic tool + LLM" pattern paying off.
- Sub-2-minute duration for diffs of one or two files.
- A LangSmith trace tagged with `pr_url:`, `author:`, `repo:` if tracing is
  enabled (set `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_API_KEY=…`).

When something's degraded, the failure modes are explicit:

- **Per-agent timeout** → `agents_failed` entry like
  `{"agent": "security", "error": "timeout after 120s"}`. Other agents still
  finish; aggregator still runs.
- **LLM provider down** → circuit breaker trips after 5 fails / 60s and
  fails fast for 30s. The aggregator falls back to its deterministic summary
  if the executive summary call fails.
- **Non-Python PR** → no agents run, but you still get a structured report
  with `summary` starting `"No applicable agents for this PR..."` and a
  file-extension breakdown.
- **Bad `LLM_PROVIDER`** → fails fast at client construction with a clear
  `ValueError`, not a confusing import error inside an agent.
- **`--post-comments` on a token without `issues:write`** → 403 logged once,
  the JSON report is still produced, the comment just isn't posted. (If
  `gh`'s OAuth token returns 403, switch to a classic PAT with `repo`
  scope — see the troubleshooting section in `USER_GUIDE.md` if you have
  local access.)

The pattern is the same in all of these: degraded behaviour produces
structured, actionable feedback, never silent or empty output.
