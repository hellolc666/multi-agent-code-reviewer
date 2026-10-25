"""Local browser workbench contract; no network or live model calls."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from reviewer import local_ui
from scripts import run_local_ui


def _client():
    return TestClient(local_ui.app, base_url="http://localhost")


def test_workbench_and_example_are_available():
    with _client() as client:
        page = client.get("/")
        example = client.get("/api/example")
    assert page.status_code == example.status_code == 200
    assert "代码审查工作台" in page.text
    assert "开始离线审查" in page.text
    assert "demo-logic-repo" in example.json()["repo_path"]
    assert "diff --git" in example.json()["diff_text"]


def test_workbench_reuses_diff_runner_and_cleans_temp_file(tmp_path, monkeypatch):
    observed = {}

    async def fake_review(diff_path, repo_path, *, live, only):
        observed.update(
            diff_text=Path(diff_path).read_text(encoding="utf-8"),
            diff_path=Path(diff_path),
            repo_path=repo_path,
            live=live,
            only=only,
        )
        return {"status": "completed", "findings": []}

    monkeypatch.setattr(local_ui, "run_review_diff", fake_review)
    with _client() as client:
        result = client.post(
            "/api/review",
            json={
                "diff_text": "diff --git a/a.py b/a.py\n",
                "repo_path": str(tmp_path),
                "agents": ["correctness"],
            },
        )
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert observed["diff_text"].startswith("diff --git")
    assert observed["repo_path"] == tmp_path
    assert observed["live"] is False
    assert observed["only"] == ["correctness"]
    assert not observed["diff_path"].exists()


def test_live_requires_explicit_request(tmp_path, monkeypatch):
    selected = []

    async def fake_review(_diff_path, _repo_path, *, live, only):
        selected.append((live, only))
        return {"status": "completed"}

    monkeypatch.setattr(local_ui, "run_review_diff", fake_review)
    with _client() as client:
        result = client.post(
            "/api/review",
            json={"diff_text": "patch", "repo_path": str(tmp_path), "live": True},
        )
    assert result.status_code == 200
    assert selected == [(True, None)]


def test_invalid_workbench_requests_are_sanitized(tmp_path, monkeypatch):
    async def invalid(*_args, **_kwargs):
        raise ValueError("PRIVATE-SOURCE-CONTENT")

    monkeypatch.setattr(local_ui, "run_review_diff", invalid)
    with _client() as client:
        invalid_agent = client.post(
            "/api/review",
            json={"diff_text": "patch", "repo_path": str(tmp_path), "agents": ["secret"]},
        )
        missing_repo = client.post(
            "/api/review",
            json={"diff_text": "patch", "repo_path": str(tmp_path / "missing")},
        )
        invalid_diff = client.post(
            "/api/review", json={"diff_text": "patch", "repo_path": str(tmp_path)}
        )
        too_large = client.post(
            "/api/review",
            json={"diff_text": "中" * (local_ui.MAX_DIFF_BYTES // 2), "repo_path": str(tmp_path)},
        )
    assert invalid_agent.status_code == missing_repo.status_code == invalid_diff.status_code == 400
    assert too_large.status_code == 413
    assert "PRIVATE-SOURCE-CONTENT" not in invalid_diff.text


def test_launcher_loads_project_env_and_binds_loopback(monkeypatch):
    observed = {}

    def fake_dotenv(path, *, override):
        observed["dotenv"] = (path, override)

    def fake_run(app, *, host, port):
        observed["server"] = (app, host, port)

    monkeypatch.setattr(run_local_ui, "load_dotenv", fake_dotenv)
    monkeypatch.setattr(run_local_ui.uvicorn, "run", fake_run)
    run_local_ui.main()
    assert observed["dotenv"] == (local_ui.PROJECT_ROOT / ".env", False)
    assert observed["server"] == ("reviewer.local_ui:app", "127.0.0.1", 8765)
