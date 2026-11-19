from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

type BeforeCloseHook = Callable[[], Awaitable[None] | None]


class ShutdownState:
    """Tracks application shutdown state and registered before-close hooks."""

    def __init__(self) -> None:
        self._is_shutting_down: bool = False
        self._before_close_hooks: list[BeforeCloseHook] = []

    @property
    def is_shutting_down(self) -> bool:
        return self._is_shutting_down

    @property
    def isShuttingDown(self) -> bool:
        return self._is_shutting_down

    def begin_shutdown(self) -> None:
        self._is_shutting_down = True

    def beginShutdown(self) -> None:
        self.begin_shutdown()

    def mark_shutting_down(self) -> None:
        self.begin_shutdown()

    def reset(self) -> None:
        self._is_shutting_down = False
        self._before_close_hooks.clear()

    def register_before_close(self, hook: BeforeCloseHook) -> None:
        self._before_close_hooks.append(hook)

    @property
    def before_close_hooks(self) -> tuple[BeforeCloseHook, ...]:
        return tuple(self._before_close_hooks)

    async def run_before_close_hooks(self) -> None:
        for hook in list(self._before_close_hooks):
            result = hook()
            if inspect.isawaitable(result):
                await result


_default_shutdown_state = ShutdownState()


def get_shutdown_state() -> ShutdownState:
    """Return the process-wide singleton ShutdownState instance."""
    return _default_shutdown_state


__all__ = ["BeforeCloseHook", "ShutdownState", "get_shutdown_state"]
