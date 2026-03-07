"""
3-state circuit breaker for wrapping external API calls.
States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing)

Ported from AWET-main — Step 7 / 2026-02-27
"""
import asyncio
import time
from enum import Enum
from typing import Callable, Any


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(RuntimeError):
    """Raised when a call is blocked because the circuit is OPEN."""


class CircuitBreaker:
    """
    Async circuit breaker.

    Usage::

        cb = CircuitBreaker("alpaca-api")
        result = await cb.call(my_async_fn, arg1, kwarg=val)

    After ``failure_threshold`` consecutive failures the circuit opens and
    all calls raise :class:`CircuitBreakerOpenError` immediately.  After
    ``recovery_timeout`` seconds a single probe call is allowed
    (HALF_OPEN); if it succeeds the circuit closes, otherwise it reopens.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._half_open_calls: int = 0
        self._lock = asyncio.Lock()

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute *func* if the circuit allows; raise otherwise."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.time() - self._last_failure_time
                if elapsed > self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit '{self.name}' is OPEN — call blocked "
                        f"({self.recovery_timeout - elapsed:.0f}s until retry)"
                    )

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitBreakerOpenError(
                        f"Circuit '{self.name}' HALF_OPEN — "
                        "max probe calls reached, waiting for success"
                    )
                self._half_open_calls += 1

        try:
            result = await func(*args, **kwargs)
            async with self._lock:
                self._on_success()
            return result
        except Exception:
            async with self._lock:
                self._on_failure()
            raise

    def _on_success(self) -> None:
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def _on_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_closed(self) -> bool:
        return self._state == CircuitState.CLOSED

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name!r}, state={self._state.value}, "
            f"failures={self._failure_count}/{self.failure_threshold})"
        )
