"""Async circuit breaker.

Per-endpoint state machine guarded by ``asyncio.Lock``:

    CLOSED  → calls flow through; failures count.
    OPEN    → calls fail fast with ``CircuitOpenError`` until ``open_seconds``
              has elapsed.
    HALF_OPEN → next call is admitted as a "probe"; success → CLOSED, failure → OPEN.

Apply to LLM and GitHub API endpoints. Pair with ``async_retry`` *inside* the
breaker (retries shouldn't compound across an open circuit).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised by a tripped breaker before invoking the wrapped callable."""


@dataclass
class CircuitBreaker:
    """Async-safe circuit breaker.

    Parameters
    ----------
    name: identifier surfaced in logs and ``CircuitOpenError``.
    failure_threshold: trips the circuit after this many failures within ``window_seconds``.
    window_seconds: rolling window for the failure count.
    open_seconds: how long to stay OPEN before allowing a HALF_OPEN probe.
    failure_exceptions: which exceptions count as failures
        (everything else propagates without affecting state).
    """

    name: str = "default"
    failure_threshold: int = 5
    window_seconds: float = 60.0
    open_seconds: float = 30.0
    failure_exceptions: tuple[type[BaseException], ...] = (Exception,)

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failures: list[float] = field(default_factory=list, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def state(self) -> CircuitState:
        return self._state

    async def _now(self) -> float:
        return time.monotonic()

    async def _maybe_half_open(self) -> None:
        if self._state == CircuitState.OPEN and (
            await self._now() - self._opened_at >= self.open_seconds
        ):
            log.info("breaker %s: OPEN -> HALF_OPEN", self.name)
            self._state = CircuitState.HALF_OPEN

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                log.info("breaker %s: HALF_OPEN probe succeeded -> CLOSED", self.name)
            self._state = CircuitState.CLOSED
            self._failures.clear()

    async def _record_failure(self) -> None:
        async with self._lock:
            now = await self._now()
            cutoff = now - self.window_seconds
            self._failures = [t for t in self._failures if t >= cutoff]
            self._failures.append(now)
            if self._state == CircuitState.HALF_OPEN:
                log.info("breaker %s: HALF_OPEN probe failed -> OPEN", self.name)
                self._state = CircuitState.OPEN
                self._opened_at = now
            elif (
                self._state == CircuitState.CLOSED
                and len(self._failures) >= self.failure_threshold
            ):
                log.warning(
                    "breaker %s: tripped (%d failures in %.0fs) CLOSED -> OPEN",
                    self.name, len(self._failures), self.window_seconds,
                )
                self._state = CircuitState.OPEN
                self._opened_at = now

    async def call(self, fn: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
        """Invoke ``fn`` through the breaker. Raises ``CircuitOpenError`` if tripped."""
        async with self._lock:
            await self._maybe_half_open()
            if self._state == CircuitState.OPEN:
                raise CircuitOpenError(
                    f"circuit '{self.name}' is open; refusing call"
                )
        try:
            result = await fn(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except self.failure_exceptions as e:
            await self._record_failure()
            raise e
        await self._record_success()
        return result

    def __call__(
        self, fn: Callable[..., Awaitable[T]]
    ) -> Callable[..., Awaitable[T]]:
        """Use as a decorator: ``@breaker``."""

        async def wrapper(*args, **kwargs) -> T:
            return await self.call(fn, *args, **kwargs)

        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        return wrapper
