"""Async Semgrep runner.

Runs the ``semgrep`` CLI as a subprocess via ``asyncio.create_subprocess_exec`` so
that cancellation (e.g. agent timeout) can ``proc.kill()`` the child cleanly.
Parses Semgrep's JSON output into a list of ``SemgrepFinding`` records.

The Semgrep CLI is **not** a Python dependency of this project — it's installed
out of band (``brew install semgrep`` / ``pipx install semgrep``) and pinned in
the production Dockerfile (Step 12). If the binary is missing, ``run_semgrep``
raises ``SemgrepNotInstalledError`` so the calling agent can record the failure
gracefully via ``agents_failed`` rather than crashing the graph.

Severity translation from Semgrep's ``ERROR | WARNING | INFO`` strings to our
``Severity`` enum is intentionally **not** done here — runners are pure I/O
wrappers. The Security agent (Step 4) maps Semgrep severities into ``Finding``
objects.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reviewer.offline import offline_mode
from reviewer.reliability.harness import observed_tool
from reviewer.tools.context import CodeContext

DEFAULT_CONFIG = "p/python"
SEMGREP_BIN = "semgrep"

# Semgrep exits 0 (no findings), 1 (findings), or >=2 (real error).
_SEMGREP_OK_EXITS = (0, 1)


class SemgrepError(Exception):
    """Raised when Semgrep itself errors (non-finding, non-cancellation)."""


class SemgrepNotInstalledError(SemgrepError):
    """Raised when the ``semgrep`` binary is not on PATH."""


@dataclass(frozen=True)
class SemgrepFinding:
    """A single result from a Semgrep run.

    Mirrors the subset of fields the Security agent consumes; the raw Semgrep
    JSON entry is preserved on ``raw`` for debugging and richer LLM context.
    """

    check_id: str
    path: str
    line_start: int
    line_end: int
    severity: str  # "ERROR" | "WARNING" | "INFO" — Semgrep's own scale
    message: str
    code_snippet: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


def _parse_semgrep_json(payload: str) -> list[SemgrepFinding]:
    """Parse Semgrep ``--json`` stdout into ``SemgrepFinding`` records.

    Semgrep's JSON shape:
        {"results": [{"check_id", "path",
                      "start": {"line": int, "col": int},
                      "end":   {"line": int, "col": int},
                      "extra": {"severity", "message", "lines"}}, ...],
         "errors": [...]}
    """
    if not payload.strip():
        return []
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise SemgrepError(f"Could not parse Semgrep JSON: {e}") from e

    results = data.get("results") or []
    out: list[SemgrepFinding] = []
    for r in results:
        try:
            extra = r.get("extra") or {}
            start = r.get("start") or {}
            end = r.get("end") or {}
            out.append(
                SemgrepFinding(
                    check_id=str(r.get("check_id", "")),
                    path=str(r.get("path", "")),
                    line_start=int(start.get("line", 0)),
                    line_end=int(end.get("line", start.get("line", 0))),
                    severity=str(extra.get("severity", "INFO")).upper(),
                    message=str(extra.get("message", "")).strip(),
                    code_snippet=str(extra.get("lines", "")).strip(),
                    raw=r,
                )
            )
        except (TypeError, ValueError, AttributeError) as e:
            raise SemgrepError(f"Malformed Semgrep result entry: {r!r}: {e}") from e
    return out


def _redact(stderr: str) -> str:
    """Strip secrets that may have leaked into Semgrep's stderr (auth tokens, etc)."""
    return re.sub(r"(x-access-token:)[^@\s]+", r"\1***", stderr)


@observed_tool("semgrep")
async def run_semgrep(
    repo_path: Path,
    target_files: list[str] | None = None,
    *,
    config: str = DEFAULT_CONFIG,
    extra_args: list[str] | None = None,
    executable: str = SEMGREP_BIN,
    timeout: float | None = None,
) -> list[SemgrepFinding]:
    """Run Semgrep over ``target_files`` (relative to ``repo_path``).

    Parameters
    ----------
    repo_path: working directory; usually the cloned PR repo.
    target_files: paths relative to ``repo_path``. ``None`` or ``[]`` short-circuits
        to ``[]`` — Semgrep with no targets is wasted I/O.
    config: Semgrep ruleset (registry slug or local path). Defaults to ``p/python``.
    extra_args: appended after standard flags but before targets. Useful for tests.
    executable: Override the binary name (mostly for tests).
    timeout: Optional float seconds; on expiry, kills the subprocess and raises
        ``TimeoutError``. Agent nodes already wrap calls in ``asyncio.timeout``;
        this is a convenience for direct callers / unit tests.

    Returns
    -------
    list[SemgrepFinding]: empty list when Semgrep ran cleanly with no findings.

    Raises
    ------
    SemgrepNotInstalledError: ``semgrep`` not on PATH.
    SemgrepError: subprocess exited with a real error code (>=2) or JSON parse fail.
    TimeoutError: ``timeout`` elapsed before completion.
    asyncio.CancelledError: caller cancelled; subprocess is killed before re-raise.
    """
    if not target_files:
        return []
    if offline_mode.get():
        return []
    context = CodeContext(repo_path)
    for target in target_files:
        context.resolve(target)

    cmd = [executable, "--config", config, "--json", "--quiet", "--metrics=off"]
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend(target_files)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise SemgrepNotInstalledError(
            f"semgrep binary not found ({executable!r}). "
            "Install via `brew install semgrep` or `pipx install semgrep`."
        ) from e

    try:
        if timeout is not None:
            async with asyncio.timeout(timeout):
                stdout, stderr = await proc.communicate()
        else:
            stdout, stderr = await proc.communicate()
    except (asyncio.CancelledError, TimeoutError):
        proc.kill()
        try:
            await proc.wait()
        except Exception:
            pass
        raise

    if proc.returncode not in _SEMGREP_OK_EXITS:
        raise SemgrepError(
            f"semgrep exited {proc.returncode}: "
            f"{_redact(stderr.decode('utf-8', errors='replace')).strip()}"
        )

    return _parse_semgrep_json(stdout.decode("utf-8", errors="replace"))
