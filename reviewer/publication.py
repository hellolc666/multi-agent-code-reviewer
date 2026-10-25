"""Human-controlled preview/publish, pinned head and remote idempotency marker."""

from __future__ import annotations

import hashlib
import re


class PublicationError(ValueError):
    pass


def prepare_publication(report):
    from reviewer.runner import _format_comment_body

    if (
        report.get("input_mode") != "github"
        or report.get("execution_mode") != "live"
        or report.get("status") != "completed"
        or not report.get("judge_enabled", False)
        or report.get("verification_incomplete")
        or report.get("agents_failed")
    ):
        raise PublicationError("Only a completed live GitHub review with Judge can be published")
    sha = report.get("head_sha", "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise PublicationError("Missing immutable 40-character PR head SHA")
    if any(f.get("verification_status") != "confirmed" for f in report.get("findings", [])):
        raise PublicationError("Unverified findings cannot be published")
    key = "|".join([report["pr_url"], sha, report.get("review_version", "2.0.0")])
    marker = "<!-- multi-agent-pr-review:" + hashlib.sha256(key.encode()).hexdigest() + " -->"
    body = marker + "\n" + _format_comment_body(report)
    if len(body) > 60000:
        raise PublicationError("Preview exceeds GitHub comment size limit; reduce the report first")
    return {"body": body, "marker": marker, "head_sha": sha}


def post_comment(
    pr_url,
    github_token,
    body,
    *,
    expected_head_sha,
    marker,
    allow_update=False,
    github_factory=None,
):
    from github import Auth, Github

    from reviewer.tools.github_client import parse_pr_url

    owner, repo, number = parse_pr_url(pr_url)
    gh = github_factory(github_token) if github_factory else Github(auth=Auth.Token(github_token))
    try:
        pr = gh.get_repo(f"{owner}/{repo}").get_pull(number)
        if pr.head.sha != expected_head_sha:
            raise PublicationError("PR head changed; rerun the review before publishing")
        # Scan all comments; marker persists across application restarts.
        existing = next((c for c in pr.get_issue_comments() if marker in (c.body or "")), None)
        if existing is not None:
            if not allow_update:
                return {"status": "already_published", "url": existing.html_url}
            pr.update()
            if pr.head.sha != expected_head_sha:
                raise PublicationError("PR head changed before comment update")
            existing.edit(body)
            return {"status": "updated", "url": existing.html_url}
        # Refresh immediately before the write. GitHub issue comments have no
        # atomic head precondition; the residual race is documented.
        pr.update()
        if pr.head.sha != expected_head_sha:
            raise PublicationError("PR head changed before publish")
        comment = pr.create_issue_comment(body)
        return {"status": "published", "url": comment.html_url}
    finally:
        gh.close()


def publish_report(report, token, *, post_comments=False, allow_update=False, poster=None):
    preview = prepare_publication(report)
    if not post_comments:
        return {"status": "preview", **preview}
    if not token:
        raise PublicationError("GITHUB_TOKEN is required for publishing")
    return (poster or post_comment)(
        report["pr_url"],
        token,
        preview["body"],
        expected_head_sha=preview["head_sha"],
        marker=preview["marker"],
        allow_update=allow_update,
    )
