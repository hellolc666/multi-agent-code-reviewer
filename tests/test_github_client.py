"""Tests for the async GitHub PR fetcher.

Default suite is fully offline:
- HTTP layer mocked via ``httpx.MockTransport``.
- PyGithub metadata + ``git clone`` patched via monkeypatch.

A single live test runs only when ``REVIEWER_LIVE_PR_URL`` env var is set; CI skips it.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from reviewer.tools import github_client as gc
from reviewer.tools.github_client import (
    GitHubClientError,
    PRBundle,
    PRMetadata,
    _is_test_path,
    fetch_pr,
    parse_pr_url,
)

OWNER, REPO, NUMBER = "example", "repo", 42
PR_URL = f"https://github.com/{OWNER}/{REPO}/pull/{NUMBER}"

SAMPLE_DIFF = """\
diff --git a/app/auth.py b/app/auth.py
index 0000001..0000002 100644
--- a/app/auth.py
+++ b/app/auth.py
@@ -20,3 +20,5 @@
+def login(password, stored_password):
+    if password == stored_password:
+        return True
"""

SAMPLE_FILES_RESPONSE = [
    {
        "filename": "app/auth.py",
        "status": "modified",
        "additions": 3,
        "deletions": 0,
        "patch": "@@ -20,3 +20,5 @@\n+def login(password, stored_password):",
    },
    {
        "filename": "tests/test_auth.py",
        "status": "added",
        "additions": 5,
        "deletions": 0,
        "patch": "@@ ...",
    },
    {
        "filename": "docs/old.md",
        "status": "removed",
        "additions": 0,
        "deletions": 12,
        "patch": None,
    },
]


def _mock_transport_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    accept = request.headers.get("Accept", "")
    if path == f"/repos/{OWNER}/{REPO}/pulls/{NUMBER}" and "diff" in accept:
        return httpx.Response(200, text=SAMPLE_DIFF)
    if path == f"/repos/{OWNER}/{REPO}/pulls/{NUMBER}/files":
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=SAMPLE_FILES_RESPONSE if page == 1 else [])
    return httpx.Response(404, json={"message": "not mocked", "path": path})


@pytest.fixture
def mock_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(_mock_transport_handler))


@pytest.fixture
def patched_metadata_and_clone(monkeypatch):
    """Patch PyGithub metadata fetch + ``git clone`` to populate a fake repo tree."""

    def fake_metadata(token, owner, repo, number):
        return PRMetadata(
            owner=owner,
            repo=repo,
            number=number,
            title="Add user authentication endpoint",
            author="alice",
            head_sha="deadbeefcafef00d",
            head_ref="feature/login",
            base_ref="main",
            head_repo_clone_url=f"https://github.com/{owner}/{repo}.git",
        )

    async def fake_clone(clone_url, ref, token, dest, sha=None):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "app").mkdir()
        (dest / "app" / "auth.py").write_text(
            "def login(password, stored_password):\n"
            "    if password == stored_password:\n"
            "        return True\n",
            encoding="utf-8",
        )
        (dest / "tests").mkdir()
        (dest / "tests" / "test_auth.py").write_text(
            "def test_login_smoke():\n    assert True\n", encoding="utf-8"
        )

    monkeypatch.setattr(gc, "_fetch_metadata_sync", fake_metadata)
    monkeypatch.setattr(gc, "_shallow_clone", fake_clone)


# -------------------- pure unit -------------------- #


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/octocat/Hello-World/pull/1", ("octocat", "Hello-World", 1)),
        ("http://github.com/a/b/pull/999", ("a", "b", 999)),
        ("https://github.com/a/b/pull/12/", ("a", "b", 12)),
    ],
)
def test_parse_pr_url_valid(url, expected):
    assert parse_pr_url(url) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "https://github.com/a/b/issues/12",
        "https://gitlab.com/a/b/pull/1",
        "not a url",
        "https://github.com/a/b/pull/abc",
        "",
    ],
)
def test_parse_pr_url_rejects(bad):
    with pytest.raises(GitHubClientError):
        parse_pr_url(bad)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("tests/test_x.py", True),
        ("test/foo.py", True),
        ("app/test_helpers.py", True),
        ("app/auth_test.py", True),
        ("app/auth.py", False),
        ("docs/readme.md", False),
        ("src/test_module/file.py", False),
    ],
)
def test_is_test_path(path, expected):
    assert _is_test_path(path) is expected


# -------------------- async integration (mocked) -------------------- #


@pytest.mark.asyncio
async def test_fetch_pr_yields_bundle(mock_http_client, patched_metadata_and_clone):
    async with fetch_pr(PR_URL, github_token=None, http_client=mock_http_client) as bundle:
        assert isinstance(bundle, PRBundle)
        assert bundle.pr_url == PR_URL
        assert bundle.metadata.owner == OWNER
        assert bundle.metadata.title == "Add user authentication endpoint"
        assert bundle.metadata.author == "alice"
        assert bundle.metadata.head_sha == "deadbeefcafef00d"

        assert bundle.pr_diff.startswith("diff --git a/app/auth.py")
        assert "password == stored_password" in bundle.pr_diff

        assert len(bundle.changed_files) == 3
        paths = {f.path for f in bundle.changed_files}
        assert paths == {"app/auth.py", "tests/test_auth.py", "docs/old.md"}
        statuses = {f.path: f.change_type for f in bundle.changed_files}
        assert statuses["docs/old.md"] == "removed"
        assert statuses["tests/test_auth.py"] == "added"

        assert [f.path for f in bundle.test_files] == ["tests/test_auth.py"]

        assert bundle.repo_path.exists()
        assert (bundle.repo_path / "app" / "auth.py").exists()

        captured_repo_path = bundle.repo_path

    assert not captured_repo_path.exists(), "tempdir should be cleaned up after context exit"


@pytest.mark.asyncio
async def test_to_state_changed_files_omits_removed_and_reads_source(
    mock_http_client, patched_metadata_and_clone
):
    async with fetch_pr(PR_URL, github_token=None, http_client=mock_http_client) as bundle:
        state_files = bundle.to_state_changed_files()
        paths = [f["path"] for f in state_files]
        assert "docs/old.md" not in paths
        assert "app/auth.py" in paths
        auth = next(f for f in state_files if f["path"] == "app/auth.py")
        assert "def login" in auth["content"]
        assert auth["change_type"] == "modified"


@pytest.mark.asyncio
async def test_fetch_pr_propagates_http_error(patched_metadata_and_clone):
    def err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(err_handler))
    with pytest.raises(httpx.HTTPStatusError):
        async with fetch_pr(PR_URL, github_token=None, http_client=client):
            pass
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_pr_retries_on_5xx(patched_metadata_and_clone, monkeypatch):
    """Transient 5xx must trigger retry; success on the second attempt."""
    calls = {"diff": 0, "files": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        accept = request.headers.get("Accept", "")
        if path == f"/repos/{OWNER}/{REPO}/pulls/{NUMBER}" and "diff" in accept:
            calls["diff"] += 1
            if calls["diff"] == 1:
                return httpx.Response(503, text="overloaded")
            return httpx.Response(200, text=SAMPLE_DIFF)
        if path == f"/repos/{OWNER}/{REPO}/pulls/{NUMBER}/files":
            calls["files"] += 1
            return httpx.Response(200, json=SAMPLE_FILES_RESPONSE)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    # Speed up retries: monkeypatch asyncio.sleep globally. The retry decorator
    # looks up asyncio.sleep at call time when no explicit sleep is provided.
    import asyncio as _asyncio

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(_asyncio, "sleep", fast_sleep)

    async with fetch_pr(PR_URL, github_token=None, http_client=client) as bundle:
        assert bundle.pr_diff.startswith("diff --git")

    assert calls["diff"] >= 2  # retried after 503

    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_pr_does_not_retry_4xx(patched_metadata_and_clone):
    """4xx is propagated immediately — no retry, no breaker trip."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"message": "Not Found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        async with fetch_pr(PR_URL, github_token=None, http_client=client):
            pass
    # Two endpoints fire concurrently via gather (1 call each, no retries).
    # If retry kicked in we'd see ≥4. So calls per endpoint must be ≤1.
    assert calls["n"] <= 2
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_pr_cleans_tempdir_on_clone_failure(mock_http_client, monkeypatch):
    """Tempdir must be removed even if clone fails after metadata + files succeed."""

    def fake_metadata(token, owner, repo, number):
        return PRMetadata(
            owner, repo, number, "t", "a", "sha", "ref", "main",
            f"https://github.com/{owner}/{repo}.git",
        )

    async def fake_clone_fail(*args, **kwargs):
        raise GitHubClientError("git exploded")

    monkeypatch.setattr(gc, "_fetch_metadata_sync", fake_metadata)
    monkeypatch.setattr(gc, "_shallow_clone", fake_clone_fail)

    captured: list[Path] = []

    real_tempdir = gc.tempfile.TemporaryDirectory

    def spying_tempdir(*a, **kw):
        td = real_tempdir(*a, **kw)
        captured.append(Path(td.name))
        return td

    monkeypatch.setattr(gc.tempfile, "TemporaryDirectory", spying_tempdir)

    with pytest.raises(GitHubClientError, match="git exploded"):
        async with fetch_pr(PR_URL, github_token=None, http_client=mock_http_client):
            pass

    assert captured, "expected TemporaryDirectory to have been created"
    for p in captured:
        assert not p.exists(), f"tempdir {p} leaked after clone failure"


# -------------------- optional live -------------------- #


LIVE_URL = os.environ.get("REVIEWER_LIVE_PR_URL")


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.skipif(not LIVE_URL, reason="set REVIEWER_LIVE_PR_URL to enable")
async def test_fetch_pr_live():
    token = os.environ.get("GITHUB_TOKEN")
    async with fetch_pr(LIVE_URL, github_token=token) as bundle:
        assert bundle.changed_files, "live PR should report changed files"
        assert bundle.pr_diff
        assert bundle.repo_path.exists()
        assert any(bundle.repo_path.iterdir())
