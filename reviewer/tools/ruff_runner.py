"""Async Ruff runner.

Runs the ``ruff`` CLI as a subprocess via ``asyncio.create_subprocess_exec`` so
agent timeouts can ``proc.kill()`` the child cleanly. Parses Ruff's
``--output-format=json`` payload into ``RuffFinding`` records.

Ruff is a Python dependency (in ``[dev]``), but it ships its own native binary
on PATH (``.venv/bin/ruff``). We invoke it as a subprocess rather than through
the Python API so that:
- CPU-bound linting doesn't block the event loop, and
- timeouts can actually kill the child (vs. uncancellable ``to_thread``).

Severity translation from Ruff's rule codes to our ``Severity`` enum is the
Quality agent's job (Step 5); this module is a pure I/O wrapper.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RUFF_BIN = "ruff"

# Ruff exits 0 when no findings, 1 when findings are present, >=2 on real error.
_RUFF_OK_EXITS = (0, 1)


class RuffError(Exception):
    """Raised when Ruff itself errors (non-finding, non-cancellation)."""


class RuffNotInstalledError(RuffError):
    """Raised when the ``ruff`` binary cannot be located."""


@dataclass(frozen=True)
class RuffFinding:
    """A single Ruff diagnostic.

    Mirrors the subset of fields the Quality agent consumes; the raw entry is
    preserved on ``raw`` for richer LLM context.
    """

    code: str  # e.g. "F401"
    path: str
    line_start: int
    line_end: int
    column: int
    message: str
    fix_applicable: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


def _parse_ruff_json(payload: str) -> list[RuffFinding]:
    """Parse Ruff's JSON output (a top-level list of diagnostics)."""
    if not payload.strip():
        return []
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise RuffError(f"Could not parse Ruff JSON: {e}") from e

    if not isinstance(data, list):
        raise RuffError(f"Expected JSON list from ruff, got {type(data).__name__}")

    out: list[RuffFinding] = []
    for r in data:
        try:
            location = r.get("location") or {}
            end_location = r.get("end_location") or location
            out.append(
                RuffFinding(
                    code=str(r.get("code") or ""),
                    path=str(r.get("filename", "")),
                    line_start=int(location.get("row", 0)),
                    line_end=int(end_location.get("row", location.get("row", 0))),
                    column=int(location.get("column", 0)),
                    message=str(r.get("message", "")).strip(),
                    fix_applicable=bool(r.get("fix")),
                    raw=r,
                )
            )
        except (TypeError, ValueError, AttributeError) as e:
            raise RuffError(f"Malformed Ruff result entry: {r!r}: {e}") from e
    return out


def _resolve_executable(executable: str) -> str:
    """Locate ``ruff`` on PATH; fall through to the literal name otherwise.

    ``shutil.which`` respects the current ``PATH``; venvs activated implicitly
    via ``.venv/bin/python`` don't always export ``PATH`` correctly, so we
    fall back to the bare name and let ``create_subprocess_exec`` raise.
    """
    found = shutil.which(executable)
    return found or executable


async def run_ruff(
    repo_path: Path,
    target_files: list[str] | None = None,
    *,
    select: list[str] | None = None,
    extra_args: list[str] | None = None,
    executable: str = RUFF_BIN,
    timeout: float | None = None,
) -> list[RuffFinding]:
    """Run ``ruff check`` over ``target_files`` (relative to ``repo_path``).

    Parameters
    ----------
    repo_path: working directory; usually the cloned PR repo.
    target_files: paths relative to ``repo_path``. ``None``/empty short-circuits
        to ``[]``.
    select: optional list of Ruff rule prefixes (``["E", "F", "W"]``); when set,
        passed as ``--select E,F,W``.
    extra_args: appended after standard flags but before targets.
    executable: override the binary name (mostly for tests).
    timeout: seconds before the subprocess is killed. ``None`` = no timeout.

    Returns
    -------
    list[RuffFinding]: empty when Ruff ran cleanly with no findings.

    Raises
    ------
    RuffNotInstalledError: ``ruff`` binary not on PATH.
    RuffError: subprocess exited with a real error code (>=2) or JSON parse fail.
    TimeoutError: ``timeout`` elapsed before completion.
    asyncio.CancelledError: caller cancelled; subprocess is killed before re-raise.
    """
    if not target_files:
        return []

    cmd = [_resolve_executable(executable), "check", "--output-format=json", "--no-cache"]
    if select:
        cmd.extend(["--select", ",".join(select)])
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
        raise RuffNotInstalledError(
            f"ruff binary not found ({executable!r}). "
            "Install via `pip install ruff` (already in [dev] extras)."
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

    if proc.returncode not in _RUFF_OK_EXITS:
        raise RuffError(
            f"ruff exited {proc.returncode}: "
            f"{stderr.decode('utf-8', errors='replace').strip()}"
        )

    return _parse_ruff_json(stdout.decode("utf-8", errors="replace"))
