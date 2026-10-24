from __future__ import annotations

import hashlib
import json

import pytest
from typer.testing import CliRunner

from reviewer.agents import evidence as evidence_mod
from reviewer.agents.evidence import EvidenceTurn, collect_evidence
from reviewer.cli import app
from reviewer.runner import run_review_diff
from reviewer.tools.context import CodeContext, safe_relative
from reviewer.tools.diff_input import make_input, parse_diff

PATCH = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 1
+    return helper()
"""


@pytest.fixture
def checkout(tmp_path):
    (tmp_path / "app.py").write_text("def answer():\n    return helper()\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    return tmp_path


def test_right_side_line_numbers_and_whole_hunk_budget():
    value, text = make_input(PATCH, mode="diff", max_chars=500)
    lines = value.files[0].hunks[0].lines
    assert [(line.kind, line.new_line) for line in lines] == [
        ("context", 1),
        ("remove", None),
        ("add", 2),
    ]
    assert "@@ -1,2 +1,2 @@" in text
    omitted, text = make_input(PATCH, mode="diff", max_chars=40)
    assert text == ""
    assert omitted.coverage["omitted"][0]["reason"] == "diff_character_budget"


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/etc/passwd",
        "C:\\secret",
        "a/../../b",
        "//server/file",
        ".git/config",
        ".env",
        "a:stream",
        "-x",
    ],
)
def test_path_escapes_denied(path):
    with pytest.raises(ValueError):
        safe_relative(path)


def test_tools_are_read_only_bounded_and_cross_file(checkout):
    context = CodeContext(checkout, parse_diff(PATCH), max_output=100)
    assert context.search_symbol("helper")["matches"][0]["path"] == "other.py"
    assert context.search_references("helper")["matches"][0]["path"] == "app.py"
    assert context.read_file("other.py", 1, 2)["lines"][1]["line"] == 2
    assert not context.call("shell", {"cmd": "anything"})["ok"]
    assert not context.call("read_file", {"path": "../outside"})["ok"]
    assert not context.call("read_file", {"path": "missing.py"})["ok"]
    assert not context.call("read_file", {"path": "app.py", "start_line": 0})["ok"]
    assert not context.call("search_symbol", {"symbol": ".*"})["ok"]
    assert not context.call("get_changed_hunks", {"file_path": "other.py"})["ok"]


def test_long_non_utf8_and_binary_files(checkout):
    context = CodeContext(checkout, max_output=80, max_file=100)
    (checkout / "long.py").write_text("x" * 101, encoding="utf-8")
    (checkout / "bad.py").write_bytes(b"\xff")
    (checkout / "binary.py").write_bytes(b"a\x00b")
    for name in ("long.py", "bad.py", "binary.py"):
        assert not context.call("read_file", {"path": name})["ok"]


def test_symlink_outside_repository_is_denied(checkout, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.py")
    outside.write_text("secret", encoding="utf-8")
    link = checkout / "link.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlink creation privilege unavailable")
    assert not CodeContext(checkout).call("read_file", {"path": "link.py"})["ok"]


def test_rename_delete_binary_and_invalid_hunks():
    files = parse_diff("""diff --git a/old.py b/new.py
similarity index 100%
rename from old.py
rename to new.py
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-old
diff --git a/icon.png b/icon.png
Binary files a/icon.png and b/icon.png differ
""")
    assert [f.change_type for f in files] == ["renamed", "removed", "modified"]
    assert files[2].binary
    assert files[1].hunks[0].lines[0].new_line is None
    with pytest.raises(ValueError, match="hunk_count_mismatch"):
        parse_diff(PATCH.replace("+1,2", "+1,9"))
    with pytest.raises(ValueError):
        parse_diff(PATCH.replace("a/app.py b/app.py", "a/../x b/../x"))


async def test_evidence_tool_calls_and_repetition_are_bounded(checkout, monkeypatch):
    async def ask(_prompt, _schema, **_kwargs):
        return EvidenceTurn(requests=[{"name": "search_symbol", "arguments": {"symbol": "helper"}}])

    monkeypatch.setattr(evidence_mod, "analyze_with_schema", ask)
    value, diff = make_input(PATCH, mode="diff")
    records, events = await collect_evidence(
        {
            "repo_path": str(checkout),
            "pr_diff": diff,
            "review_input": value.model_dump(),
            "context_tools_enabled": True,
        },
        "security",
    )
    assert len(records) == 1
    assert records[0]["result"]["matches"][0]["path"] == "other.py"
    assert events[0]["status"] == "budget_exhausted"


async def test_local_diff_shared_runner_is_offline_by_default(checkout):
    patch = checkout / "review.patch"
    patch.write_bytes(PATCH.encode("utf-8"))
    report = await run_review_diff(patch, checkout)
    assert report["input_mode"] == "diff"
    assert report["diff_sha256"] == hashlib.sha256(patch.read_bytes()).hexdigest()
    assert report["execution_mode"] == "mock"
    assert report["reviewed_files"] == ["app.py"]
    assert report["coverage"]["included_hunks"][0]["path"] == "app.py"


def test_review_diff_cli_without_github_token(checkout, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    patch = checkout / "review.patch"
    patch.write_text(PATCH, encoding="utf-8")
    output = checkout / "report.json"
    result = CliRunner().invoke(
        app,
        [
            "review-diff",
            "--diff",
            str(patch),
            "--repo-path",
            str(checkout),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text(encoding="utf-8"))["execution_mode"] == "mock"
