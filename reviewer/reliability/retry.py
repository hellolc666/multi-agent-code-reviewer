"""Async retry decorator with exponential backoff.

Use on GitHub API calls and LLM calls (NOT on Semgrep — see CLAUDE.md). The
decorator preserves the wrapped function's signature and is safe across
``asyncio.timeout`` boundaries: a ``CancelledError`` always propagates without
being retried.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


def async_retry(
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    jitter: float = 0.25,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Retry an async callable up to ``max_attempts`` times.

    Backoff is exponential (``base_delay * 2 ** attempt``) capped at
    ``max_delay``, with up to ``jitter`` fraction of randomness so retries
    don't synchronize across callers.

    ``CancelledError`` is *never* retried — propagates immediately so
    ``asyncio.timeout`` and task cancellation work correctly.

    Parameters
    ----------
    retry_on: tuple of exception types that count as transient. Anything
        outside this set propagates after the first occurrence.
    sleep: injection point for tests. If ``None`` (default), ``asyncio.sleep``
        is looked up at call time so monkeypatching it works on production
        decorations too.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(fn)
        async def wrapper(*args, **kwargs) -> T:
            last_error: BaseException | None = None
            for attempt in range(max_attempts):
                try:
                    return await fn(*args, **kwargs)
                except asyncio.CancelledError:
                    raise
                except retry_on as e:
                    last_error = e
                    if attempt == max_attempts - 1:
                        log.warning(
                            "%s failed after %d attempts: %s",
                            fn.__name__, max_attempts, e,
                        )
                        raise
                    delay = min(max_delay, base_delay * (2 ** attempt))
                    if jitter:
                        delay *= 1 + random.uniform(-jitter, jitter)
                    delay = max(0.0, delay)
                    log.info(
                        "%s attempt %d/%d failed (%s); retrying in %.2fs",
                        fn.__name__, attempt + 1, max_attempts, e, delay,
                    )
                    sleep_fn = sleep if sleep is not None else asyncio.sleep
                    await sleep_fn(delay)
            # Unreachable in normal flow (the loop either returns or raises),
            # but keep mypy/static checkers happy.
            raise last_error  # type: ignore[misc]

        return wrapper

    return decorator
