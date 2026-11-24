from __future__ import annotations

import logging
from typing import Any

import socketio

from app.auth.access_token_authenticator import AccessTokenAuthenticator
from app.auth.types import AuthenticatedUser
from app.example.dto import ExampleRealtimeMessageDto
from app.realtime.connection_limiter import WsConnectionLimiter
from app.realtime.constants import REALTIME_NAMESPACE
from app.realtime.extract_token import extract_access_token
from app.realtime.message_rate_limiter import WsMessageRateLimiter

logger = logging.getLogger("RealtimeGateway")


class RealtimeGateway:
    def __init__(
        self,
        sio: socketio.AsyncServer,
        authenticator: AccessTokenAuthenticator,
        connection_limiter: WsConnectionLimiter,
        message_rate_limiter: WsMessageRateLimiter,
        allow_query_token: bool = False,
    ) -> None:
        self.sio = sio
        self._authenticator = authenticator
        self._connection_limiter = connection_limiter
        self._message_rate_limiter = message_rate_limiter
        self._allow_query_token = allow_query_token
        self._sessions: dict[str, dict[str, Any]] = {}
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.sio.on("connect", namespace=REALTIME_NAMESPACE)(self.handle_connect)
        self.sio.on("disconnect", namespace=REALTIME_NAMESPACE)(self.handle_disconnect)
        self.sio.on("join", namespace=REALTIME_NAMESPACE)(self.handle_join)
        self.sio.on("message", namespace=REALTIME_NAMESPACE)(self.handle_message)
        self.sio.on("admin-broadcast", namespace=REALTIME_NAMESPACE)(
            self.handle_admin_broadcast
        )

    async def handle_connect(
        self,
        sid: str,
        environ: dict[str, Any],
        auth: dict[str, Any] | None = None,
    ) -> bool:
        handshake: dict[str, Any] = {
            "headers": {
                "authorization": environ.get("HTTP_AUTHORIZATION"),
                "cookie": environ.get("HTTP_COOKIE"),
            },
            "auth": auth,
            "QUERY_STRING": environ.get("QUERY_STRING"),
        }
        token = extract_access_token(
            handshake, allow_query_token=self._allow_query_token
        )
        if not token:
            logger.warning(
                "WebSocket connect rejected: unauthenticated handshake for sid %s",
                sid,
            )
            return False

        try:
            user = await self._authenticator.verify_access_token(token)
            await self._connection_limiter.register_connection(user.id, sid)
            self._sessions[sid] = {"user": user, "wsConnectionRegistered": True}
            return True
        except Exception as exc:
            logger.warning("WebSocket authentication error for sid %s: %s", sid, exc)
            return False

    async def handle_disconnect(self, sid: str) -> None:
        session = self._sessions.pop(sid, None)
        if not session:
            return
        user: AuthenticatedUser | None = session.get("user")
        if user:
            await self._connection_limiter.release_connection(user.id, sid)

    async def handle_join(self, sid: str, data: dict[str, Any]) -> dict[str, Any]:
        session = self._sessions.get(sid)
        if not session or not session.get("user"):
            return {"ok": False, "error": "Unauthorized"}
        user: AuthenticatedUser = session["user"]
        allowed_room = f"user:{user.id}"
        requested_room = data.get("room") if isinstance(data, dict) else None
        if requested_room != allowed_room:
            return {"ok": False, "error": "Cannot join foreign room"}

        self.sio.enter_room(sid, allowed_room, namespace=REALTIME_NAMESPACE)
        return {"ok": True, "room": allowed_room}

    async def handle_message(
        self, sid: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        session = self._sessions.get(sid)
        if not session or not session.get("user"):
            return {"ok": False, "error": "Unauthorized"}
        user: AuthenticatedUser = session["user"]

        if not await self._message_rate_limiter.is_allowed(user.id):
            return {
                "ok": False,
                "error": "WebSocket message rate limit exceeded",
            }

        try:
            dto = ExampleRealtimeMessageDto(text=data.get("text", ""))
        except Exception as exc:
            return {"ok": False, "error": f"Invalid message: {exc}"}

        await self.sio.emit(
            "message",
            {"text": dto.text, "from": user.id},
            room=f"user:{user.id}",
            namespace=REALTIME_NAMESPACE,
        )
        return {"ok": True}

    async def handle_admin_broadcast(
        self, sid: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        session = self._sessions.get(sid)
        if not session or not session.get("user"):
            return {"ok": False, "error": "Unauthorized"}
        user: AuthenticatedUser = session["user"]

        if getattr(user, "role", None) != "admin":
            return {"ok": False, "error": "Forbidden: admin role required"}

        if not await self._message_rate_limiter.is_allowed(user.id):
            return {
                "ok": False,
                "error": "WebSocket message rate limit exceeded",
            }

        try:
            dto = ExampleRealtimeMessageDto(text=data.get("text", ""))
        except Exception as exc:
            return {"ok": False, "error": f"Invalid message: {exc}"}

        await self.sio.emit(
            "admin-broadcast",
            {"text": dto.text},
            namespace=REALTIME_NAMESPACE,
        )
        return {"ok": True}
