"""Tests for ``async_retry``."""

from __future__ import annotations

import asyncio

import pytest

from reviewer.reliability.retry import async_retry


@pytest.mark.asyncio
async def test_retry_succeeds_first_try():
    calls = {"n": 0}

    async def noop_sleep(_):  # pragma: no cover — should not be reached
        raise AssertionError("sleep must not be called on first-try success")

    @async_retry(max_attempts=3, sleep=noop_sleep)
    async def fn():
        calls["n"] += 1
        return "ok"

    assert await fn() == "ok"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_retry_recovers_on_transient_failure():
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    @async_retry(max_attempts=3, base_delay=1.0, jitter=0, sleep=fake_sleep)
    async def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("flaky")
        return 42

    assert await fn() == 42
    assert calls["n"] == 3
    # Two retries → two sleep calls; backoff: 1s, 2s
    assert sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_retry_exhausts_then_raises():
    async def fake_sleep(_):
        return None

    @async_retry(max_attempts=2, base_delay=0.01, jitter=0, sleep=fake_sleep)
    async def fn():
        raise RuntimeError("always")

    with pytest.raises(RuntimeError, match="always"):
        await fn()


@pytest.mark.asyncio
async def test_retry_caps_at_max_delay():
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    @async_retry(
        max_attempts=5, base_delay=10.0, max_delay=15.0, jitter=0, sleep=fake_sleep
    )
    async def fn():
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError):
        await fn()
    # Backoff would be 10, 20, 40, 80 → capped at 15.
    assert all(s <= 15.0 for s in sleeps)


@pytest.mark.asyncio
async def test_retry_does_not_retry_cancelled_error():
    calls = {"n": 0}

    @async_retry(max_attempts=5, base_delay=0.01, sleep=lambda _: asyncio.sleep(0))
    async def fn():
        calls["n"] += 1
        raise asyncio.CancelledError("stop")

    with pytest.raises(asyncio.CancelledError):
        await fn()
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_retry_only_retries_listed_exceptions():
    calls = {"n": 0}

    async def fake_sleep(_):
        return None

    @async_retry(
        max_attempts=5,
        base_delay=0.01,
        retry_on=(ValueError,),
        jitter=0,
        sleep=fake_sleep,
    )
    async def fn():
        calls["n"] += 1
        raise TypeError("not retryable")

    with pytest.raises(TypeError):
        await fn()
    assert calls["n"] == 1  # propagated immediately


@pytest.mark.asyncio
async def test_retry_preserves_function_name():
    @async_retry(max_attempts=1)
    async def my_func():
        return None

    assert my_func.__name__ == "my_func"


def test_retry_invalid_max_attempts():
    with pytest.raises(ValueError):
        async_retry(max_attempts=0)
