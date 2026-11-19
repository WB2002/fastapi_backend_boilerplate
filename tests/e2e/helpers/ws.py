"""Socket.IO real-time client helper for ``/socket.io`` and ``/realtime`` E2E tests."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import socketio

DEFAULT_WS_PATH = "/socket.io"
REALTIME_NAMESPACE = "/realtime"


@dataclass(slots=True)
class RealtimeClient:
    """Connected ``python-socketio`` AsyncClient with event capture."""

    sio: socketio.AsyncClient
    namespace: str = REALTIME_NAMESPACE
    events: dict[str, list[Any]] = field(default_factory=dict)
    disconnected_event: asyncio.Event = field(default_factory=asyncio.Event)
    _cond: asyncio.Condition = field(default_factory=asyncio.Condition)

    @property
    def connected(self) -> bool:
        """Return ``True`` while Socket.IO client remains connected."""
        return bool(self.sio.connected)

    async def join_room(self, room: str, *, timeout: float = 5.0) -> Any:  # noqa: ASYNC109
        """Emit ``join`` event on the realtime namespace and await ack."""
        return await self.sio.call(
            "join",
            {"room": room},
            namespace=self.namespace,
            timeout=timeout,
        )

    async def send_message(self, text: str) -> None:
        """Emit ``message`` event on the realtime namespace."""
        await self.sio.emit("message", {"text": text}, namespace=self.namespace)

    async def send_admin_broadcast(self, text: str) -> None:
        """Emit ``admin-broadcast`` event on the realtime namespace."""
        await self.sio.emit(
            "admin-broadcast",
            {"text": text},
            namespace=self.namespace,
        )

    async def wait_for_event(
        self,
        event_name: str,
        *,
        count: int = 1,
        timeout: float = 5.0,  # noqa: ASYNC109
    ) -> list[Any]:
        """Wait until at least ``count`` occurrences of ``event_name`` are captured."""

        async def _wait() -> list[Any]:
            async with self._cond:
                while len(self.events.get(event_name, [])) < count:
                    await self._cond.wait()
                return list(self.events[event_name])

        return await asyncio.wait_for(_wait(), timeout=timeout)

    async def wait_for_disconnect(
        self,
        *,
        timeout: float = 5.0,  # noqa: ASYNC109
    ) -> bool:
        """Wait for server-initiated or local disconnect event."""
        try:
            await asyncio.wait_for(self.disconnected_event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    async def disconnect(self) -> None:
        """Disconnect cleanly if still connected."""
        if self.sio.connected:
            await self.sio.disconnect()


def _append_query_param(url: str, key: str, value: str) -> str:
    parts = urlsplit(url)
    params = parse_qsl(parts.query, keep_blank_values=True)
    params.append((key, value))
    return urlunsplit(parts._replace(query=urlencode(params)))


def _merge_ws_headers(
    headers: Mapping[str, str] | None,
    bearer_token: str | None,
    cookie: str | None,
) -> dict[str, str]:
    merged: dict[str, str] = dict(headers) if headers is not None else {}
    if bearer_token is not None:
        merged["Authorization"] = f"Bearer {bearer_token}"
    if cookie is not None:
        existing_key = next(
            (k for k in merged if k.lower() == "cookie"),
            None,
        )
        cookie_pair = f"access_token={cookie}"
        if existing_key is not None:
            merged[existing_key] = f"{merged[existing_key]}; {cookie_pair}"
        else:
            merged["Cookie"] = cookie_pair
    return merged


async def connect_realtime_client(
    base_url: str,
    *,
    token: str | None = None,
    bearer_token: str | None = None,
    cookie: str | None = None,
    query_token: str | None = None,
    query_param_name: str = "access_token",
    headers: Mapping[str, str] | None = None,
    socketio_path: str = DEFAULT_WS_PATH,
    namespace: str = REALTIME_NAMESPACE,
    transports: list[str] | None = None,
    wait_timeout: float = 5.0,
) -> RealtimeClient:
    """Connect an async Socket.IO client to ``/realtime`` with explicit auth."""
    sio = socketio.AsyncClient(logger=False, engineio_logger=False)
    client = RealtimeClient(sio=sio, namespace=namespace)

    async def _record(event_name: str, data: Any) -> None:
        async with client._cond:
            client.events.setdefault(event_name, []).append(data)
            client._cond.notify_all()

    async def _on_message(data: Any) -> None:
        await _record("message", data)

    async def _on_admin_broadcast(data: Any) -> None:
        await _record("admin-broadcast", data)

    async def _on_exception(data: Any) -> None:
        await _record("exception", data)

    async def _on_disconnect() -> None:
        client.disconnected_event.set()

    async def disconnect() -> None:
        client.disconnected_event.set()

    sio.on("message", handler=_on_message, namespace=namespace)
    sio.on("admin-broadcast", handler=_on_admin_broadcast, namespace=namespace)
    sio.on("exception", handler=_on_exception, namespace=namespace)
    sio.on("disconnect", handler=_on_disconnect, namespace=namespace)
    sio.on("disconnect", handler=disconnect)

    target_url = base_url
    if query_token is not None:
        target_url = _append_query_param(target_url, query_param_name, query_token)

    merged_headers = _merge_ws_headers(headers, bearer_token, cookie)
    auth_payload = {"token": token} if token is not None else None
    connect_kwargs: dict[str, Any] = {
        "headers": merged_headers or None,
        "auth": auth_payload,
        "namespaces": [namespace],
        "socketio_path": socketio_path.lstrip("/"),
        "wait_timeout": wait_timeout,
    }
    if transports is not None:
        connect_kwargs["transports"] = transports

    await sio.connect(target_url, **connect_kwargs)
    return client


__all__ = [
    "DEFAULT_WS_PATH",
    "REALTIME_NAMESPACE",
    "RealtimeClient",
    "connect_realtime_client",
]
