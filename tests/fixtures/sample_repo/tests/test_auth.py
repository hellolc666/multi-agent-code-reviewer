"""Tests for app.auth — used by Test Mapping fixture."""

from app.auth import login


def test_login_smoke():
    assert login("a", "a") is True
    assert login("a", "b") is False
