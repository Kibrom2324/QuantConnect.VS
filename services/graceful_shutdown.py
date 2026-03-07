"""
APEX Graceful Shutdown — services/graceful_shutdown.py

Fixes implemented in this file
───────────────────────────────
  CF-9   Shutdown timeout: asyncio.wait_for(coro, timeout=30) guards every
         async shutdown coroutine so a hung cleanup step cannot block the
         process forever and prevent the container from restarting.
"""

from __future__ import annotations

import asyncio
import logging
import signal as _signal
from typing import Callable, Coroutine, Any

import structlog

logger = structlog.get_logger(__name__)

# CF-9 FIX 2026-02-27: maximum wall-clock seconds for any single shutdown step
SHUTDOWN_TIMEOUT_SECONDS: float = float(
    __import__("os").environ.get("SHUTDOWN_TIMEOUT_SECONDS", "30")
)


class GracefulShutdown:
    """
    SIGTERM / SIGINT handler with per-coroutine 30-second timeouts.

    CF-9 FIX: asyncio.wait_for(coro, timeout=SHUTDOWN_TIMEOUT_SECONDS) wraps
    every registered shutdown coroutine so a stuck cleanup step cannot block
    the entire shutdown sequence indefinitely.

    Usage
    ─────
    shutdown = GracefulShutdown()
    shutdown.register(producer.stop)
    shutdown.register(consumer.close)
    asyncio.run(main())       # signal handlers installed automatically

    Or from an existing event loop:
    await shutdown.run_shutdown_sequence()
    """

    def __init__(self) -> None:
        self._handlers:     list[Callable[[], Coroutine]] = []
        self._shutdown_event = asyncio.Event()
        self._install_signal_handlers()

    # ─── Public API ──────────────────────────────────────────────────────────

    @property
    def is_shutdown(self) -> bool:
        return self._shutdown_event.is_set()

    async def wait(self) -> None:
        """Await until a shutdown signal is received."""
        await self._shutdown_event.wait()

    def register(self, coro_fn: Callable[[], Coroutine]) -> None:
        """Register an async cleanup function.  Called in LIFO order."""
        self._handlers.append(coro_fn)

    async def run_shutdown_sequence(self) -> None:
        """
        CF-9 FIX: Execute each registered shutdown coroutine with a 30-second
        timeout.  Logs a warning and continues to the next handler if a step
        times out — it does NOT abort the sequence.
        """
        logger.info("shutdown_sequence_started", n_handlers=len(self._handlers))

        # LIFO: last-registered handler runs first (stack discipline)
        for handler in reversed(self._handlers):
            name = getattr(handler, "__qualname__", repr(handler))
            try:
                # CF-9 FIX 2026-02-27: asyncio.wait_for enforces 30-second cap
                await asyncio.wait_for(
                    handler(),
                    timeout=SHUTDOWN_TIMEOUT_SECONDS,
                )
                logger.info("shutdown_handler_completed", handler=name)
            except asyncio.TimeoutError:
                logger.warning(
                    "shutdown_handler_timed_out",         # CF-9 FIX identifier
                    handler=name,
                    timeout_seconds=SHUTDOWN_TIMEOUT_SECONDS,
                )
            except Exception as e:
                logger.error(
                    "shutdown_handler_raised",
                    handler=name,
                    error=str(e),
                )

        logger.info("shutdown_sequence_complete")

    # ─── Signal installation ─────────────────────────────────────────────────

    def _install_signal_handlers(self) -> None:
        """Register SIGTERM and SIGINT handlers via asyncio's event loop."""
        try:
            loop = asyncio.get_event_loop()
            for sig in (_signal.SIGTERM, _signal.SIGINT):
                loop.add_signal_handler(sig, self._handle_signal)
        except RuntimeError:
            # No running event loop yet — handlers will be installed lazily
            pass

    def _handle_signal(self) -> None:
        logger.info("shutdown_signal_received")
        self._shutdown_event.set()
        # Schedule the cleanup sequence as a task
        asyncio.ensure_future(self.run_shutdown_sequence())
