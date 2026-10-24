"""Async GitHub PR fetcher.

Parses a PR URL, fetches metadata + raw diff + changed-files list via the REST API,
and shallow-clones the PR head into a temporary directory. Yields a ``PRBundle``
through an async context manager; the tempdir is removed on exit.

Network layer: ``httpx.AsyncClient`` for raw diff + file list; ``PyGithub`` (wrapped
via ``asyncio.to_thread``) for typed PR metadata. Source layer: ``git clone --depth=1``
via ``asyncio.create_subprocess_exec`` so timeouts can ``proc.kill()`` cleanly.

Reliability decorators (retry, circuit breaker) are intentionally NOT applied here —
they land at Step 8 and wrap the same call sites.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from github import Github
from github.PullRequest import PullRequest

from reviewer.reliability.circuit_breaker import CircuitBreaker
from reviewer.reliability.retry import async_retry
from reviewer.tools.context import CodeContext

GITHUB_API = "https://api.github.com"
PR_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)/?$"
)


# Module-level breaker for the GitHub endpoint. Same shape as the LLM breaker.
# Counts only HTTP 5xx + transport errors as failures (4xx is a caller bug, not
# a service outage; we don't want a typo in a PR URL to trip the breaker).
class _GitHubTransientError(Exception):
    """Internal: marks a GitHub failure as breaker-counting."""


_GITHUB_BREAKER = CircuitBreaker(
    name="github_api",
    failure_threshold=5,
    window_seconds=60.0,
    open_seconds=30.0,
    failure_exceptions=(_GitHubTransientError,),
)


def _is_transient_http_error(exc: Exception) -> bool:
    """5xx, connection errors, timeouts → retry. 4xx → propagate."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(
        exc,
        (httpx.TransportError, httpx.TimeoutException, _GitHubTransientError),
    )


class GitHubClientError(Exception):
    """Raised for parse failures, API errors, or clone failures."""


@dataclass
class PRFile:
    path: str
    change_type: str
    patch: str | None = None
    additions: int = 0
    deletions: int = 0


@dataclass
class PRMetadata:
    owner: str
    repo: str
    number: int
    title: str
    author: str
    head_sha: str
    head_ref: str
    base_ref: str
    head_repo_clone_url: str


@dataclass
class PRBundle:
    pr_url: str
    metadata: PRMetadata
    pr_diff: str
    changed_files: list[PRFile]
    repo_path: Path
    test_files: list[PRFile] = field(default_factory=list)

    def to_state_changed_files(self, repo_root: Path | None = None) -> list[dict]:
        """Render ``changed_files`` as plain dicts for ``ReviewState``.

        Reads file contents from the cloned ``repo_path`` so agents have full source.
        Removed files are omitted; their content no longer exists on disk.
        """
        root = repo_root or self.repo_path
        out: list[dict] = []
        for f in self.changed_files:
            if f.change_type == "removed":
                continue
            try:
                content = CodeContext(root).text(f.path)
            except (OSError, ValueError):
                content = ""
            out.append({"path": f.path, "content": content, "change_type": f.change_type})
        return out


def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    m = PR_URL_RE.match(pr_url.strip())
    if not m:
        raise GitHubClientError(f"Not a GitHub PR URL: {pr_url!r}")
    return m["owner"], m["repo"], int(m["number"])


def _is_test_path(path: str, test_dirs: tuple[str, ...] = ("tests", "test")) -> bool:
    parts = Path(path).parts
    if not parts:
        return False
    if parts[0] in test_dirs:
        return True
    name = parts[-1]
    return name.startswith("test_") or name.endswith("_test.py")


async def _fetch_raw_diff(
    client: httpx.AsyncClient, owner: str, repo: str, number: int
) -> str:
    return await _GITHUB_BREAKER.call(
        _fetch_raw_diff_inner, client, owner, repo, number
    )


@async_retry(
    max_attempts=3, base_delay=1.0, max_delay=10.0,
    retry_on=(httpx.TransportError, httpx.TimeoutException, _GitHubTransientError),
)
async def _fetch_raw_diff_inner(
    client: httpx.AsyncClient, owner: str, repo: str, number: int
) -> str:
    try:
        resp = await client.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        if _is_transient_http_error(e):
            raise _GitHubTransientError(str(e)) from e
        raise
    return resp.text


async def _fetch_files(
    client: httpx.AsyncClient, owner: str, repo: str, number: int
) -> list[PRFile]:
    return await _GITHUB_BREAKER.call(
        _fetch_files_inner, client, owner, repo, number
    )


@async_retry(
    max_attempts=3, base_delay=1.0, max_delay=10.0,
    retry_on=(httpx.TransportError, httpx.TimeoutException, _GitHubTransientError),
)
async def _fetch_files_inner(
    client: httpx.AsyncClient, owner: str, repo: str, number: int
) -> list[PRFile]:
    files: list[PRFile] = []
    page = 1
    while True:
        try:
            resp = await client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
                headers={"Accept": "application/vnd.github+json"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if _is_transient_http_error(e):
                raise _GitHubTransientError(str(e)) from e
            raise
        batch = resp.json()
        if not batch:
            break
        for f in batch:
            files.append(
                PRFile(
                    path=f["filename"],
                    change_type=f.get("status", "modified"),
                    patch=f.get("patch"),
                    additions=int(f.get("additions", 0)),
                    deletions=int(f.get("deletions", 0)),
                )
            )
        if len(batch) < 100:
            break
        page += 1
    return files


def _fetch_metadata_sync(token: str | None, owner: str, repo: str, number: int) -> PRMetadata:
    gh = Github(token) if token else Github()
    pr: PullRequest = gh.get_repo(f"{owner}/{repo}").get_pull(number)
    head = pr.head
    return PRMetadata(
        owner=owner,
        repo=repo,
        number=number,
        title=pr.title or "",
        author=pr.user.login if pr.user else "",
        head_sha=head.sha,
        head_ref=head.ref,
        base_ref=pr.base.ref,
        head_repo_clone_url=head.repo.clone_url if head.repo else pr.base.repo.clone_url,
    )


async def _shallow_clone(
    clone_url: str, ref: str, token: str | None, dest: Path, sha: str | None = None
) -> None:
    """Shallow-clone ``ref`` into ``dest``. Auth via token-in-URL for private repos.

    ``sha`` is unused by default ``--depth=1`` but kept for future pinning via fetch.
    """
    if token and clone_url.startswith("https://"):
        url = clone_url.replace("https://", f"https://x-access-token:{token}@", 1)
    else:
        url = clone_url
    cmd = ["git", "clone", "--depth=1", "--branch", ref, url, str(dest)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await proc.communicate()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        safe_url = re.sub(r"x-access-token:[^@]+@", "x-access-token:***@", url)
        raise GitHubClientError(
            f"git clone failed for {safe_url} (ref={ref}): "
            f"{stderr.decode('utf-8', errors='replace').strip()}"
        )


@asynccontextmanager
async def fetch_pr(
    pr_url: str,
    github_token: str | None = None,
    *,
    test_dirs: tuple[str, ...] = ("tests", "test"),
    http_client: httpx.AsyncClient | None = None,
) -> AsyncIterator[PRBundle]:
    """Fetch a PR and yield a ``PRBundle`` with cleanup-guaranteed tempdir.

    Parameters
    ----------
    pr_url: ``https://github.com/<owner>/<repo>/pull/<n>``
    github_token: Optional. If set, raises rate limits and unlocks private repos.
    test_dirs: Directories considered "tests" for the ``test_files`` split.
    http_client: Optional injected ``httpx.AsyncClient`` for testing.
    """
    owner, repo, number = parse_pr_url(pr_url)
    headers = {"User-Agent": "multi-agent-code-reviewer", "Accept": "application/vnd.github+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(headers=headers, timeout=30.0)
    if not owns_client:
        client.headers.update(headers)

    try:
        diff_task = _fetch_raw_diff(client, owner, repo, number)
        files_task = _fetch_files(client, owner, repo, number)
        meta_task = asyncio.to_thread(_fetch_metadata_sync, github_token, owner, repo, number)
        pr_diff, changed_files, metadata = await asyncio.gather(
            diff_task, files_task, meta_task
        )
    finally:
        if owns_client:
            await client.aclose()

    test_files = [f for f in changed_files if _is_test_path(f.path, test_dirs)]

    with tempfile.TemporaryDirectory(prefix="reviewer-") as tmp:
        repo_path = Path(tmp) / "src"
        await _shallow_clone(
            metadata.head_repo_clone_url,
            metadata.head_ref,
            github_token,
            repo_path,
            sha=metadata.head_sha,
        )
        yield PRBundle(
            pr_url=pr_url,
            metadata=metadata,
            pr_diff=pr_diff,
            changed_files=changed_files,
            repo_path=repo_path,
            test_files=test_files,
        )
