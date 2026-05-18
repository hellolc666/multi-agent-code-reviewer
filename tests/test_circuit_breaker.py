"""Tests for the ``CircuitBreaker``.

Time is mocked by monkeypatching ``CircuitBreaker._now`` so tests don't sleep.
"""

from __future__ import annotations

import asyncio

import pytest

from reviewer.reliability import circuit_breaker as cb_mod
from reviewer.reliability.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


def _fake_clock(start: float = 0.0):
    """Return a clock fn + a setter to advance the clock."""
    state = {"t": start}

    async def now(self):  # bound method shim
        return state["t"]

    def advance(seconds: float):
        state["t"] += seconds

    return now, advance


def _install_clock(monkeypatch, start: float = 0.0):
    now_fn, advance = _fake_clock(start)
    monkeypatch.setattr(cb_mod.CircuitBreaker, "_now", now_fn)
    return advance


@pytest.mark.asyncio
async def test_breaker_passes_through_success(monkeypatch):
    _install_clock(monkeypatch)
    breaker = CircuitBreaker(name="t", failure_threshold=3)

    async def ok():
        return 42

    assert await breaker.call(ok) == 42
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_breaker_trips_after_threshold(monkeypatch):
    _install_clock(monkeypatch)
    breaker = CircuitBreaker(name="t", failure_threshold=3, window_seconds=60)

    async def boom():
        raise RuntimeError("nope")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await breaker.call(boom)
    assert breaker.state == CircuitState.OPEN

    # Subsequent calls fast-fail without invoking the function.
    called = {"n": 0}

    async def should_not_run():
        called["n"] += 1
        return None

    with pytest.raises(CircuitOpenError):
        await breaker.call(should_not_run)
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_breaker_window_drops_old_failures(monkeypatch):
    advance = _install_clock(monkeypatch)
    breaker = CircuitBreaker(failure_threshold=3, window_seconds=10)

    async def boom():
        raise RuntimeError("err")

    # 2 failures within window
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await breaker.call(boom)
    assert breaker.state == CircuitState.CLOSED

    # Move outside window — the prior failures should age out.
    advance(20)
    with pytest.raises(RuntimeError):
        await breaker.call(boom)
    # Only 1 failure within the current window → still CLOSED.
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_breaker_transitions_to_half_open_then_closed_on_success(monkeypatch):
    advance = _install_clock(monkeypatch)
    breaker = CircuitBreaker(failure_threshold=2, window_seconds=60, open_seconds=30)

    async def boom():
        raise RuntimeError("err")

    async def ok():
        return "good"

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await breaker.call(boom)
    assert breaker.state == CircuitState.OPEN

    # Within OPEN window — still fast-fail.
    advance(15)
    with pytest.raises(CircuitOpenError):
        await breaker.call(ok)

    # After OPEN window → admit a probe; success → CLOSED.
    advance(20)
    assert await breaker.call(ok) == "good"
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_breaker_half_open_failure_reopens(monkeypatch):
    advance = _install_clock(monkeypatch)
    breaker = CircuitBreaker(failure_threshold=2, window_seconds=60, open_seconds=30)

    async def boom():
        raise RuntimeError("err")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await breaker.call(boom)
    assert breaker.state == CircuitState.OPEN

    advance(35)  # past OPEN window
    with pytest.raises(RuntimeError):
        await breaker.call(boom)  # half-open probe fails
    assert breaker.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_breaker_does_not_record_cancellation_as_failure(monkeypatch):
    _install_clock(monkeypatch)
    breaker = CircuitBreaker(failure_threshold=2)

    async def cancel_me():
        raise asyncio.CancelledError("cancelled")

    for _ in range(5):
        with pytest.raises(asyncio.CancelledError):
            await breaker.call(cancel_me)
    # CancelledError shouldn't trip the breaker.
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_breaker_only_counts_listed_exceptions(monkeypatch):
    _install_clock(monkeypatch)
    breaker = CircuitBreaker(
        failure_threshold=2, failure_exceptions=(ValueError,)
    )

    async def type_err():
        raise TypeError("not failure-worthy")

    for _ in range(5):
        with pytest.raises(TypeError):
            await breaker.call(type_err)
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_breaker_decorator_form(monkeypatch):
    _install_clock(monkeypatch)
    breaker = CircuitBreaker(failure_threshold=2)

    @breaker
    async def fn(x):
        if x < 0:
            raise RuntimeError("neg")
        return x * 2

    assert await fn(5) == 10
    with pytest.raises(RuntimeError):
        await fn(-1)
    with pytest.raises(RuntimeError):
        await fn(-1)
    with pytest.raises(CircuitOpenError):
        await fn(5)
