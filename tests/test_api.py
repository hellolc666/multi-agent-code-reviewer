"""Tests for the FastAPI HTTP API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from reviewer import api as api_mod
from reviewer.api import app


def test_health_endpoint():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


@pytest.fixture
def patched_run_review(monkeypatch):
    captured: dict = {}

    async def fake(pr_url, *, github_token=None, only=None, post_comments=False):
        captured["pr_url"] = pr_url
        captured["github_token"] = github_token
        captured["only"] = only
        captured["post_comments"] = post_comments
        return {
            "pr_url": pr_url,
            "summary": "stub",
            "files_changed": 0,
            "findings": [],
            "agents_succeeded": ["security"],
            "agents_failed": [],
            "trace_id": "trace-1",
        }

    monkeypatch.setattr(api_mod, "run_review", fake)
    return captured


def test_review_minimal_request(patched_run_review):
    client = TestClient(app)
    resp = client.post("/review", json={"pr_url": "https://github.com/x/y/pull/1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["pr_url"] == "https://github.com/x/y/pull/1"
    assert patched_run_review["only"] is None
    assert patched_run_review["post_comments"] is False


def test_review_passes_through_options(patched_run_review):
    client = TestClient(app)
    resp = client.post(
        "/review",
        json={
            "pr_url": "https://github.com/x/y/pull/1",
            "agents": ["security", "quality"],
            "post_comments": False,
            "github_token": "tok-from-body",
        },
    )
    assert resp.status_code == 200
    assert patched_run_review["only"] == ["security", "quality"]
    assert patched_run_review["post_comments"] is False
    assert patched_run_review["github_token"] == "tok-from-body"


def test_review_uses_env_token_when_body_omits(patched_run_review, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    client = TestClient(app)
    resp = client.post("/review", json={"pr_url": "https://github.com/x/y/pull/1"})
    assert resp.status_code == 200
    assert patched_run_review["github_token"] == "from-env"


def test_review_400_on_value_error(monkeypatch):
    async def fake(*a, **kw):  # noqa: ARG001
        raise ValueError("bad PR URL")

    monkeypatch.setattr(api_mod, "run_review", fake)
    client = TestClient(app)
    resp = client.post("/review", json={"pr_url": "not-a-url"})
    assert resp.status_code == 400
    assert "bad PR URL" in resp.json()["detail"]


def test_review_500_on_unexpected_exception(monkeypatch):
    async def fake(*a, **kw):  # noqa: ARG001
        raise RuntimeError("upstream broke")

    monkeypatch.setattr(api_mod, "run_review", fake)
    client = TestClient(app)
    resp = client.post(
        "/review", json={"pr_url": "https://github.com/x/y/pull/1"}
    )
    assert resp.status_code == 500
    assert "upstream broke" in resp.json()["detail"]


def test_review_500_on_empty_report(monkeypatch):
    async def fake(*a, **kw):  # noqa: ARG001
        return {}

    monkeypatch.setattr(api_mod, "run_review", fake)
    client = TestClient(app)
    resp = client.post(
        "/review", json={"pr_url": "https://github.com/x/y/pull/1"}
    )
    assert resp.status_code == 500


def test_review_422_on_missing_pr_url():
    client = TestClient(app)
    resp = client.post("/review", json={})
    assert resp.status_code == 422  # FastAPI/Pydantic validation
