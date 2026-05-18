"""Tests for the async Semgrep runner.

Default suite is fully offline — ``asyncio.create_subprocess_exec`` is
monkeypatched with a fake-process factory. A single live test runs only when
``REVIEWER_LIVE_SEMGREP=1`` is set and ``semgrep`` is on PATH.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from reviewer.tools.semgrep_runner import (
    SemgrepError,
    SemgrepFinding,
    SemgrepNotInstalledError,
    _parse_semgrep_json,
    _redact,
    run_semgrep,
)

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


# --------------------------------------------------------------------------- #
# Fake-process plumbing                                                       #
# --------------------------------------------------------------------------- #


class _FakeProc:
    """Stand-in for ``asyncio.subprocess.Process``."""

    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0,
                 sleep_seconds: float = 0.0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._sleep = sleep_seconds
        self.killed = False
        self.last_cmd: tuple[str, ...] = ()

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._sleep:
            await asyncio.sleep(self._sleep)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def _fake_exec_factory(proc: _FakeProc, *, raise_fnf: bool = False):
    captured: dict = {"cmd": None, "cwd": None}

    async def fake_exec(*cmd, cwd=None, stdout=None, stderr=None):  # noqa: ARG001
        if raise_fnf:
            raise FileNotFoundError(2, "no such file", cmd[0])
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        proc.last_cmd = cmd
        return proc

    return fake_exec, captured


# --------------------------------------------------------------------------- #
# JSON parsing                                                                #
# --------------------------------------------------------------------------- #


SAMPLE_PAYLOAD = json.dumps(
    {
        "results": [
            {
                "check_id": "python.lang.security.unsafe-eq",
                "path": "app/auth.py",
                "start": {"line": 23, "col": 5},
                "end": {"line": 23, "col": 40},
                "extra": {
                    "severity": "WARNING",
                    "message": "Password compared with == ...",
                    "lines": "if password == stored_password:",
                },
            },
            {
                "check_id": "python.lang.security.hardcoded-token",
                "path": "app/auth.py",
                "start": {"line": 12, "col": 1},
                "end": {"line": 12, "col": 50},
                "extra": {
                    "severity": "ERROR",
                    "message": "Hardcoded credentials detected",
                },
            },
        ],
        "errors": [],
    }
)


def test_parse_semgrep_json_extracts_fields():
    findings = _parse_semgrep_json(SAMPLE_PAYLOAD)
    assert len(findings) == 2
    f0, f1 = findings
    assert isinstance(f0, SemgrepFinding)
    assert f0.check_id == "python.lang.security.unsafe-eq"
    assert f0.path == "app/auth.py"
    assert f0.line_start == 23 and f0.line_end == 23
    assert f0.severity == "WARNING"
    assert "Password compared" in f0.message
    assert "stored_password" in f0.code_snippet
    assert f0.raw["check_id"] == f0.check_id  # raw preserved
    assert f1.severity == "ERROR"


def test_parse_semgrep_json_empty_payload():
    assert _parse_semgrep_json("") == []
    assert _parse_semgrep_json("   \n") == []
    assert _parse_semgrep_json('{"results": [], "errors": []}') == []


def test_parse_semgrep_json_malformed_raises():
    with pytest.raises(SemgrepError, match="Could not parse"):
        _parse_semgrep_json("{not json")


def test_parse_semgrep_json_malformed_entry_raises():
    bad = json.dumps({"results": [{"check_id": "x", "start": "not-a-dict"}]})
    with pytest.raises(SemgrepError, match="Malformed"):
        _parse_semgrep_json(bad)


def test_redact_strips_token_in_url():
    leaked = "fatal: could not access x-access-token:ghp_AAA123@github.com/x/y.git"
    assert "ghp_AAA" not in _redact(leaked)
    assert "x-access-token:***@" in _redact(leaked)


# --------------------------------------------------------------------------- #
# run_semgrep — async behavior                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_semgrep_empty_targets_short_circuits(monkeypatch):
    called = {"n": 0}

    async def boom(*a, **kw):  # noqa: ARG001
        called["n"] += 1
        raise AssertionError("must not invoke subprocess for empty targets")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    out = await run_semgrep(FIXTURE_REPO, [])
    assert out == []
    assert called["n"] == 0
    out2 = await run_semgrep(FIXTURE_REPO, None)
    assert out2 == []


@pytest.mark.asyncio
async def test_run_semgrep_parses_results_on_exit_1(monkeypatch):
    """Exit code 1 = findings exist; runner must treat that as success."""
    proc = _FakeProc(stdout=SAMPLE_PAYLOAD.encode(), returncode=1)
    fake_exec, captured = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    findings = await run_semgrep(FIXTURE_REPO, ["app/auth.py"])

    assert len(findings) == 2
    cmd = captured["cmd"]
    assert cmd[0] == "semgrep"
    assert "--json" in cmd
    assert "--config" in cmd
    assert "app/auth.py" in cmd
    assert captured["cwd"] == str(FIXTURE_REPO)


@pytest.mark.asyncio
async def test_run_semgrep_exit_0_no_findings(monkeypatch):
    proc = _FakeProc(stdout=b'{"results": [], "errors": []}', returncode=0)
    fake_exec, _ = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await run_semgrep(FIXTURE_REPO, ["app/auth.py"]) == []


@pytest.mark.asyncio
async def test_run_semgrep_exit_2_raises(monkeypatch):
    proc = _FakeProc(stdout=b"", stderr=b"some semgrep crash", returncode=2)
    fake_exec, _ = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(SemgrepError, match="exited 2"):
        await run_semgrep(FIXTURE_REPO, ["app/auth.py"])


@pytest.mark.asyncio
async def test_run_semgrep_redacts_token_in_error(monkeypatch):
    leaky = b"git: cannot reach x-access-token:ghp_SECRET@github.com/x/y"
    proc = _FakeProc(stderr=leaky, returncode=3)
    fake_exec, _ = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(SemgrepError) as ei:
        await run_semgrep(FIXTURE_REPO, ["app/auth.py"])
    assert "ghp_SECRET" not in str(ei.value)
    assert "x-access-token:***" in str(ei.value)


@pytest.mark.asyncio
async def test_run_semgrep_missing_binary_raises_not_installed(monkeypatch):
    fake_exec, _ = _fake_exec_factory(_FakeProc(), raise_fnf=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(SemgrepNotInstalledError, match="not found"):
        await run_semgrep(FIXTURE_REPO, ["app/auth.py"])


@pytest.mark.asyncio
async def test_run_semgrep_timeout_kills_proc(monkeypatch):
    proc = _FakeProc(sleep_seconds=5.0)
    fake_exec, _ = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(TimeoutError):
        await run_semgrep(FIXTURE_REPO, ["app/auth.py"], timeout=0.05)
    assert proc.killed is True


@pytest.mark.asyncio
async def test_run_semgrep_cancellation_kills_proc(monkeypatch):
    proc = _FakeProc(sleep_seconds=5.0)
    fake_exec, _ = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    task = asyncio.create_task(run_semgrep(FIXTURE_REPO, ["app/auth.py"]))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed is True


@pytest.mark.asyncio
async def test_run_semgrep_passes_extra_args(monkeypatch):
    proc = _FakeProc(stdout=b'{"results": [], "errors": []}', returncode=0)
    fake_exec, captured = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await run_semgrep(
        FIXTURE_REPO,
        ["app/auth.py"],
        config="p/security",
        extra_args=["--max-target-bytes", "1000000"],
    )
    cmd = captured["cmd"]
    assert "p/security" in cmd
    assert "--max-target-bytes" in cmd
    # targets come last
    assert cmd[-1] == "app/auth.py"


# --------------------------------------------------------------------------- #
# Optional live test                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.skipif(
    not (os.environ.get("REVIEWER_LIVE_SEMGREP") and shutil.which("semgrep")),
    reason="set REVIEWER_LIVE_SEMGREP=1 and install semgrep to enable",
)
async def test_run_semgrep_live_on_fixture():
    findings = await run_semgrep(
        FIXTURE_REPO, ["app/auth.py"], config="p/python", timeout=60
    )
    # Hardcoded token + unsafe == compare → at least one finding from p/python.
    assert isinstance(findings, list)
    assert all(isinstance(f, SemgrepFinding) for f in findings)
