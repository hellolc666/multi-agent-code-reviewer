"""Tests for the async Ruff runner.

Default suite mocks ``asyncio.create_subprocess_exec``. A live test runs the
real ``ruff`` binary against the fixture repo when ``REVIEWER_LIVE_RUFF=1``
is set; ``ruff`` is in our ``[dev]`` deps so it should be available locally.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from reviewer.tools.ruff_runner import (
    RuffError,
    RuffFinding,
    RuffNotInstalledError,
    _parse_ruff_json,
    run_ruff,
)

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


class _FakeProc:
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
    [
        {
            "code": "F401",
            "filename": "app/auth.py",
            "message": "`os` imported but unused",
            "location": {"row": 14, "column": 1},
            "end_location": {"row": 14, "column": 9},
            "fix": {"applicability": "safe"},
        },
        {
            "code": "E501",
            "filename": "app/auth.py",
            "message": "Line too long (104 > 100)",
            "location": {"row": 22, "column": 101},
            "end_location": {"row": 22, "column": 105},
            "fix": None,
        },
    ]
)


def test_parse_ruff_json_extracts_fields():
    findings = _parse_ruff_json(SAMPLE_PAYLOAD)
    assert len(findings) == 2
    f0, f1 = findings
    assert isinstance(f0, RuffFinding)
    assert f0.code == "F401"
    assert f0.path == "app/auth.py"
    assert f0.line_start == 14 and f0.line_end == 14
    assert f0.column == 1
    assert "imported but unused" in f0.message
    assert f0.fix_applicable is True
    assert f0.raw["code"] == "F401"
    assert f1.fix_applicable is False


def test_parse_ruff_json_empty_payload():
    assert _parse_ruff_json("") == []
    assert _parse_ruff_json("   \n") == []
    assert _parse_ruff_json("[]") == []


def test_parse_ruff_json_malformed_raises():
    with pytest.raises(RuffError, match="Could not parse"):
        _parse_ruff_json("{not json")


def test_parse_ruff_json_non_list_raises():
    with pytest.raises(RuffError, match="Expected JSON list"):
        _parse_ruff_json('{"results": []}')


# --------------------------------------------------------------------------- #
# run_ruff — async behavior                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_ruff_empty_targets_short_circuits(monkeypatch):
    async def boom(*a, **kw):  # noqa: ARG001
        raise AssertionError("must not invoke subprocess for empty targets")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    assert await run_ruff(FIXTURE_REPO, []) == []
    assert await run_ruff(FIXTURE_REPO, None) == []


@pytest.mark.asyncio
async def test_run_ruff_parses_results_on_exit_1(monkeypatch):
    """Exit 1 = lint findings exist; runner must treat as success."""
    proc = _FakeProc(stdout=SAMPLE_PAYLOAD.encode(), returncode=1)
    fake_exec, captured = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    findings = await run_ruff(FIXTURE_REPO, ["app/auth.py"])
    assert len(findings) == 2
    cmd = captured["cmd"]
    # Resolved executable may be absolute or "ruff"; just check basename.
    assert Path(cmd[0]).name in {"ruff", "ruff.exe"}
    assert "check" in cmd
    assert "--output-format=json" in cmd
    assert cmd[-1] == "app/auth.py"
    assert captured["cwd"] == str(FIXTURE_REPO)


@pytest.mark.asyncio
async def test_run_ruff_exit_0_no_findings(monkeypatch):
    proc = _FakeProc(stdout=b"[]", returncode=0)
    fake_exec, _ = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    assert await run_ruff(FIXTURE_REPO, ["app/auth.py"]) == []


@pytest.mark.asyncio
async def test_run_ruff_exit_2_raises(monkeypatch):
    proc = _FakeProc(stderr=b"ruff: invalid argument", returncode=2)
    fake_exec, _ = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(RuffError, match="exited 2"):
        await run_ruff(FIXTURE_REPO, ["app/auth.py"])


@pytest.mark.asyncio
async def test_run_ruff_missing_binary_raises_not_installed(monkeypatch):
    fake_exec, _ = _fake_exec_factory(_FakeProc(), raise_fnf=True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(RuffNotInstalledError, match="not found"):
        await run_ruff(FIXTURE_REPO, ["app/auth.py"], executable="definitely-not-ruff")


@pytest.mark.asyncio
async def test_run_ruff_timeout_kills_proc(monkeypatch):
    proc = _FakeProc(sleep_seconds=5.0)
    fake_exec, _ = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(TimeoutError):
        await run_ruff(FIXTURE_REPO, ["app/auth.py"], timeout=0.05)
    assert proc.killed is True


@pytest.mark.asyncio
async def test_run_ruff_cancellation_kills_proc(monkeypatch):
    proc = _FakeProc(sleep_seconds=5.0)
    fake_exec, _ = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    task = asyncio.create_task(run_ruff(FIXTURE_REPO, ["app/auth.py"]))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed is True


@pytest.mark.asyncio
async def test_run_ruff_passes_select_and_extra_args(monkeypatch):
    proc = _FakeProc(stdout=b"[]", returncode=0)
    fake_exec, captured = _fake_exec_factory(proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await run_ruff(
        FIXTURE_REPO,
        ["app/auth.py"],
        select=["E", "F"],
        extra_args=["--preview"],
    )
    cmd = captured["cmd"]
    assert "--select" in cmd
    idx = cmd.index("--select")
    assert cmd[idx + 1] == "E,F"
    assert "--preview" in cmd


# --------------------------------------------------------------------------- #
# Optional live test (uses real ruff binary)                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.skipif(
    not (os.environ.get("REVIEWER_LIVE_RUFF") and shutil.which("ruff")),
    reason="set REVIEWER_LIVE_RUFF=1 (ruff is in [dev] extras)",
)
async def test_run_ruff_live_on_fixture():
    """Real ruff over the fixture repo: should flag the unused `import os`."""
    findings = await run_ruff(
        FIXTURE_REPO, ["app/auth.py"], select=["F", "E"], timeout=30
    )
    codes = {f.code for f in findings}
    assert "F401" in codes, f"expected unused-import F401, got {codes}"
