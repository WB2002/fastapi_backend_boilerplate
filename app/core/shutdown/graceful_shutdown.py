from __future__ import annotations

import asyncio
import inspect
import os
import signal
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.logger.logger_config import get_logger
from app.core.shutdown.shutdown_state import BeforeCloseHook, ShutdownState


class ClosableApp(Protocol):
    def close(self) -> Awaitable[None] | None: ...


type SleepFn = Callable[[int], Awaitable[None]]
type ExitFn = Callable[[int], None]
type SignalListener = Callable[[], Awaitable[None] | None]
type OnSignalFn = Callable[[str, SignalListener], None]

_background_tasks: set[asyncio.Task[None]] = set()


@dataclass(slots=True)
class GracefulShutdownDeps:
    drain_ms: int = 10_000
    force_ms: int = 5_000
    sleep: SleepFn | None = None
    on_exit: ExitFn | None = None
    on_signal: OnSignalFn | None = None
    before_close: BeforeCloseHook | None = None


async def _default_sleep(ms: int) -> None:
    await asyncio.sleep(max(0.0, ms / 1000.0))


def _default_exit(code: int) -> None:
    os._exit(code)


def _schedule_task(coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _default_on_signal(sig_name: str, listener: SignalListener) -> None:
    sig_enum = getattr(signal, sig_name, None)
    if sig_enum is None:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(
            sig_enum,
            lambda: _schedule_task(_invoke_listener(listener)),
        )
        return
    except RuntimeError:
        pass

    def _handler(_signum: int, _frame: Any) -> None:
        try:
            _schedule_task(_invoke_listener(listener))
        except RuntimeError:
            asyncio.run(_invoke_listener(listener))

    signal.signal(sig_enum, _handler)


async def _invoke_listener(listener: SignalListener) -> None:
    res = listener()
    if inspect.isawaitable(res):
        await res


async def _call_close(app: ClosableApp | Callable[[], Any] | Any) -> None:
    close_fn = getattr(app, "close", None)
    if callable(close_fn):
        res = close_fn()
        if inspect.isawaitable(res):
            await res
    elif callable(app):
        res = app()
        if inspect.isawaitable(res):
            await res


def _resolve_deps(
    deps: GracefulShutdownDeps | dict[str, Any] | None,
) -> tuple[int, int, SleepFn, ExitFn, OnSignalFn, BeforeCloseHook | None]:
    if isinstance(deps, GracefulShutdownDeps):
        return (
            deps.drain_ms,
            deps.force_ms,
            deps.sleep or _default_sleep,
            deps.on_exit or _default_exit,
            deps.on_signal or _default_on_signal,
            deps.before_close,
        )
    if isinstance(deps, dict):
        raw_drain = deps.get("drain_ms", deps.get("drainMs", 10_000))
        raw_force = deps.get("force_ms", deps.get("forceMs", 5_000))
        return (
            int(raw_drain if raw_drain is not None else 10_000),
            int(raw_force if raw_force is not None else 5_000),
            deps.get("sleep") or _default_sleep,
            deps.get("on_exit") or deps.get("onExit") or _default_exit,
            deps.get("on_signal") or deps.get("onSignal") or _default_on_signal,
            deps.get("before_close") or deps.get("beforeClose"),
        )
    return (10_000, 5_000, _default_sleep, _default_exit, _default_on_signal, None)


def register_graceful_shutdown(
    app: ClosableApp | Callable[[], Any] | Any,
    shutdown_state: ShutdownState,
    deps: GracefulShutdownDeps | dict[str, Any] | None = None,
) -> Callable[[str], Coroutine[Any, Any, None]]:
    """Register two-phase SIGTERM/SIGINT handlers for graceful shutdown."""
    logger = get_logger("GracefulShutdown")
    drain_ms, force_ms, sleep_fn, on_exit, on_signal, before_close = _resolve_deps(deps)
    is_shutting_down = False

    async def handle(sig_name: str) -> None:
        nonlocal is_shutting_down
        if is_shutting_down:
            logger.warn(f"Ignoring duplicate {sig_name} during shutdown")
            return
        is_shutting_down = True
        logger.log(
            f"Received {sig_name}; marking not ready, "
            f"draining {drain_ms}ms, then closing"
        )
        shutdown_state.begin_shutdown()

        total_force_sec = max(0.0, (drain_ms + force_ms) / 1000.0)
        loop = asyncio.get_running_loop()

        def _on_force_timeout() -> None:
            logger.error(f"Forced exit after {drain_ms + force_ms}ms (close hung)")
            on_exit(1)

        force_handle = loop.call_later(total_force_sec, _on_force_timeout)

        try:
            await sleep_fn(drain_ms)
            if before_close is not None:
                bc_res = before_close()
                if inspect.isawaitable(bc_res):
                    await bc_res
            await shutdown_state.run_before_close_hooks()
            await _call_close(app)
            force_handle.cancel()
            on_exit(0)
        except Exception as err:
            force_handle.cancel()
            logger.error(f"Shutdown failed: {err}")
            on_exit(1)

    def _make_signal_callback(sig_name: str) -> SignalListener:
        def _cb() -> Awaitable[None]:
            return _schedule_task(handle(sig_name))

        return _cb

    on_signal("SIGTERM", _make_signal_callback("SIGTERM"))
    on_signal("SIGINT", _make_signal_callback("SIGINT"))
    return handle


__all__ = [
    "ClosableApp",
    "GracefulShutdownDeps",
    "register_graceful_shutdown",
]
