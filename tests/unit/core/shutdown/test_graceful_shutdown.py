from __future__ import annotations

import asyncio

from app.core.shutdown import (
    GracefulShutdownDeps,
    ShutdownState,
    register_graceful_shutdown,
)


class TestShutdownState:
    def test_initial_state_and_transitions(self) -> None:
        state = ShutdownState()
        assert state.is_shutting_down is False
        assert state.isShuttingDown is False

        state.mark_shutting_down()
        assert state.is_shutting_down is True
        assert state.isShuttingDown is True

        state.reset()
        assert state.is_shutting_down is False


class TestRegisterGracefulShutdown:
    async def test_marks_not_ready_drains_aborts_deps_closes_then_exits_0(
        self,
    ) -> None:
        drain_ms = 100
        force_ms = 50
        state = ShutdownState()
        order: list[str] = []
        signal_handlers: dict[str, object] = {}
        exited: asyncio.Future[int] = asyncio.get_running_loop().create_future()

        orig_begin = state.begin_shutdown

        def _spy_begin() -> None:
            order.append("beginShutdown")
            orig_begin()

        state.begin_shutdown = _spy_begin  # type: ignore[method-assign]

        async def _sleep(ms: int) -> None:
            order.append(f"sleep:{ms}")

        async def _before_close() -> None:
            order.append("beforeClose")

        state.register_before_close(lambda: order.append("stateHook"))

        class _FakeApp:
            async def close(self) -> None:
                order.append("close")

        def _on_exit(code: int) -> None:
            order.append(f"exit:{code}")
            if not exited.done():
                exited.set_result(code)

        handle = register_graceful_shutdown(
            _FakeApp(),
            state,
            GracefulShutdownDeps(
                drain_ms=drain_ms,
                force_ms=force_ms,
                sleep=_sleep,
                before_close=_before_close,
                on_exit=_on_exit,
                on_signal=lambda sig, cb: signal_handlers.__setitem__(sig, cb),
            ),
        )

        assert "SIGTERM" in signal_handlers
        assert "SIGINT" in signal_handlers
        await handle("SIGTERM")

        assert await exited == 0
        assert state.is_shutting_down is True
        assert order == [
            "beginShutdown",
            f"sleep:{drain_ms}",
            "beforeClose",
            "stateHook",
            "close",
            "exit:0",
        ]

    async def test_ignores_duplicate_signals_while_already_shutting_down(
        self,
    ) -> None:
        state = ShutdownState()
        draining = asyncio.Event()
        sleep_calls = 0
        close_calls = 0
        exit_codes: list[int] = []

        async def _sleep(_ms: int) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            await draining.wait()

        class _FakeApp:
            async def close(self) -> None:
                nonlocal close_calls
                close_calls += 1

        handle = register_graceful_shutdown(
            _FakeApp(),
            state,
            GracefulShutdownDeps(
                drain_ms=100,
                force_ms=50,
                sleep=_sleep,
                on_exit=lambda code: exit_codes.append(code),
                on_signal=lambda _sig, _cb: None,
            ),
        )

        t1: asyncio.Task[None] = asyncio.create_task(handle("SIGTERM"))
        await asyncio.sleep(0.01)
        await handle("SIGINT")
        await handle("SIGTERM")

        assert sleep_calls == 1
        draining.set()
        await t1
        assert close_calls == 1
        assert exit_codes == [0]

    async def test_force_exits_with_code_1_when_close_hangs_past_drain_plus_force(
        self,
    ) -> None:
        state = ShutdownState()
        exited: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        hang_event = asyncio.Event()

        class _HangingApp:
            async def close(self) -> None:
                await hang_event.wait()

        handle = register_graceful_shutdown(
            _HangingApp(),
            state,
            GracefulShutdownDeps(
                drain_ms=20,
                force_ms=20,
                sleep=lambda _ms: asyncio.sleep(0.005),
                on_exit=lambda code: (
                    exited.set_result(code) if not exited.done() else None
                ),
                on_signal=lambda _sig, _cb: None,
            ),
        )

        task: asyncio.Task[None] = asyncio.create_task(handle("SIGTERM"))
        code = await asyncio.wait_for(exited, timeout=1.0)
        assert code == 1
        hang_event.set()
        await task

    async def test_exits_1_when_before_close_or_close_rejects(self) -> None:
        state1 = ShutdownState()
        close_called = False
        exit_codes_1: list[int] = []

        async def _failing_before_close() -> None:
            raise RuntimeError("destroy failed")

        class _App1:
            async def close(self) -> None:
                nonlocal close_called
                close_called = True

        handle1 = register_graceful_shutdown(
            _App1(),
            state1,
            GracefulShutdownDeps(
                drain_ms=10,
                force_ms=50,
                sleep=lambda _ms: asyncio.sleep(0),
                before_close=_failing_before_close,
                on_exit=lambda code: exit_codes_1.append(code),
                on_signal=lambda _sig, _cb: None,
            ),
        )
        await handle1("SIGTERM")
        assert exit_codes_1 == [1]
        assert close_called is False

        state2 = ShutdownState()
        exit_codes_2: list[int] = []

        class _FailingCloseApp:
            async def close(self) -> None:
                raise RuntimeError("close failed")

        handle2 = register_graceful_shutdown(
            _FailingCloseApp(),
            state2,
            GracefulShutdownDeps(
                drain_ms=10,
                force_ms=50,
                sleep=lambda _ms: asyncio.sleep(0),
                on_exit=lambda code: exit_codes_2.append(code),
                on_signal=lambda _sig, _cb: None,
            ),
        )
        await handle2("SIGINT")
        assert exit_codes_2 == [1]
