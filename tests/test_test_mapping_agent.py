"""Tests for the Test Mapping agent."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from reviewer.agents import test_mapping as tm_mod
from reviewer.agents.base import FindingList
from reviewer.agents.test_mapping import (
    DEFAULT_TEST_DIRS,
    TEST_MAPPING_LABEL,
    _build_coverage_rows,
    _changed_entities,
    _classify,
    _read_test_dirs,
)
from reviewer.agents.test_mapping import test_mapping_agent_node as _node
from reviewer.models.findings import Category, Finding, Severity

SOURCE_FILE = "app/auth.py"
SOURCE_CODE = '''def login(password, stored_password):
    if password == stored_password:
        return True
    return False


def helper_unused():
    return 1


class Session:
    def __init__(self, user_id):
        self.user_id = user_id
'''


def _build_repo(tmp_path: Path, *, with_test_for_login: bool = True,
                pyproject: str | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    if with_test_for_login:
        (repo / "tests" / "test_auth.py").write_text(
            "from app.auth import login\n"
            "def test_login():\n"
            "    assert login('a', 'a') is True\n",
            encoding="utf-8",
        )
    if pyproject is not None:
        (repo / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return repo


def _state(repo: Path) -> dict:
    return {
        "pr_url": "https://github.com/x/y/pull/1",
        "pr_diff": "diff",
        "changed_files": [
            {"path": SOURCE_FILE, "content": SOURCE_CODE, "change_type": "modified"},
        ],
        "repo_path": str(repo),
    }


def _llm_finding(file=SOURCE_FILE, line=7, sev=Severity.MEDIUM) -> Finding:
    return Finding(
        id="LLM-TM-1",
        agent="docs",  # wrong; node should normalize
        category=Category.TEST_COVERAGE,
        severity=sev,
        file_path=file,
        line_start=line,
        line_end=line,
        title="t",
        explanation="e",
        suggestion="s",
        confidence=0.7,
        tool_source="llm-analysis",
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kinds, expected_status, expected_conf_at_least",
    [
        ({"import_from", "call"}, "covered", 0.9),
        ({"import_from"}, "covered", 0.9),
        ({"call"}, "covered", 0.9),
        ({"import_module"}, "covered", 0.7),
        ({"name"}, "covered", 0.7),
        ({"attribute"}, "covered", 0.7),
        ({"string"}, "partially_covered", 0.4),
        (set(), "uncovered", 0.8),
    ],
)
def test_classify(kinds, expected_status, expected_conf_at_least):
    status, conf = _classify(kinds)
    assert status == expected_status
    assert conf >= expected_conf_at_least


def test_read_test_dirs_default(tmp_path):
    assert _read_test_dirs(tmp_path) == DEFAULT_TEST_DIRS


def test_read_test_dirs_uses_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.reviewer]\ntest_dirs = ["my_tests", "more_tests"]\n',
        encoding="utf-8",
    )
    assert _read_test_dirs(tmp_path) == ("my_tests", "more_tests")


def test_read_test_dirs_falls_back_on_malformed_toml(tmp_path):
    (tmp_path / "pyproject.toml").write_text("not = valid = toml", encoding="utf-8")
    assert _read_test_dirs(tmp_path) == DEFAULT_TEST_DIRS


def test_changed_entities_extracts_from_diff(tmp_path):
    state = _state(_build_repo(tmp_path))
    out = _changed_entities(state)
    assert SOURCE_FILE in out
    names = {n for _, n, _ in out[SOURCE_FILE]}
    assert {"login", "helper_unused", "Session"} <= names


def test_changed_entities_skips_test_files(tmp_path):
    state = {
        "changed_files": [
            {
                "path": "tests/test_x.py",
                "content": "def test_thing():\n    pass\n",
                "change_type": "modified",
            }
        ],
        "repo_path": str(tmp_path),
    }
    assert _changed_entities(state) == {}


def test_build_coverage_rows_classifies_login_as_covered(tmp_path):
    repo = _build_repo(tmp_path)
    rows = _build_coverage_rows(_state(repo))

    by_name = {r.name: r for r in rows}
    assert by_name["login"].status == "covered"
    assert by_name["login"].confidence >= 0.9
    # helper_unused has no test reference
    assert by_name["helper_unused"].status == "uncovered"
    # Session is not referenced in the test file → uncovered
    assert by_name["Session"].status == "uncovered"


def test_build_coverage_rows_no_tests_means_uncovered(tmp_path):
    repo = _build_repo(tmp_path, with_test_for_login=False)
    rows = _build_coverage_rows(_state(repo))
    assert all(r.status == "uncovered" for r in rows)


# --------------------------------------------------------------------------- #
# Node behavior                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_test_mapping_agent_no_entities_succeeds(monkeypatch, tmp_path):
    repo = _build_repo(tmp_path)
    state = {
        "pr_diff": "",
        "changed_files": [{"path": "README.md", "change_type": "modified"}],
        "repo_path": str(repo),
    }

    async def boom(*a, **kw):  # noqa: ARG001
        raise AssertionError("LLM should not run with no entities")

    monkeypatch.setattr(tm_mod, "analyze_with_schema", boom)
    out = await _node(state)
    assert out == {"agents_succeeded": [TEST_MAPPING_LABEL]}


@pytest.mark.asyncio
async def test_test_mapping_agent_happy_path(monkeypatch, tmp_path):
    repo = _build_repo(tmp_path)

    async def fake_analyze(prompt, schema, **kw):  # noqa: ARG001
        assert "Coverage classification" in prompt
        assert "uncovered" in prompt
        return FindingList(findings=[_llm_finding()])

    monkeypatch.setattr(tm_mod, "analyze_with_schema", fake_analyze)

    out = await _node(_state(repo))
    assert out["agents_succeeded"] == [TEST_MAPPING_LABEL]
    assert all(f.agent == TEST_MAPPING_LABEL for f in out["findings"])


@pytest.mark.asyncio
async def test_test_mapping_agent_llm_failure_uses_seeds(monkeypatch, tmp_path):
    repo = _build_repo(tmp_path)

    async def boom_llm(*a, **kw):  # noqa: ARG001
        raise RuntimeError("llm broke")

    monkeypatch.setattr(tm_mod, "analyze_with_schema", boom_llm)

    out = await _node(_state(repo))
    assert out["agents_succeeded"] == [TEST_MAPPING_LABEL]
    # helper_unused + Session are uncovered → seeds emitted for those
    assert len(out["findings"]) >= 1
    assert all(f.tool_source == "ast-test-mapping" for f in out["findings"])


@pytest.mark.asyncio
async def test_test_mapping_agent_timeout(monkeypatch, tmp_path):
    repo = _build_repo(tmp_path)

    async def slow_llm(*a, **kw):  # noqa: ARG001
        await asyncio.sleep(5)
        return FindingList()

    monkeypatch.setattr(tm_mod, "analyze_with_schema", slow_llm)
    monkeypatch.setattr(tm_mod, "TEST_MAPPING_TIMEOUT_S", 0.05)

    out = await _node(_state(repo))
    assert "timeout" in out["agents_failed"][0]["error"]
