from __future__ import annotations

import logging
from typing import Any

import socketio
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.access_token_authenticator import AccessTokenAuthenticator
from app.common.config.parsers import WebSocketConfig
from app.common.redis.redis_service import RedisService
from app.realtime.connection_limiter import WsConnectionLimiter
from app.realtime.extract_token import extract_access_token
from app.realtime.gateway import RealtimeGateway
from app.realtime.message_rate_limiter import WsMessageRateLimiter

logger = logging.getLogger("SocketIoServer")


class AuthenticatedSocketIOApp:
    """ASGI wrapper rejecting unauthenticated HTTP/WebSocket upgrade handshakes."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        authenticator: AccessTokenAuthenticator,
        allow_query_token: bool = False,
    ) -> None:
        self._app = app
        self._authenticator = authenticator
        self._allow_query_token = allow_query_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        raw_headers = scope.get("headers") or []
        headers: dict[str, str] = {}
        for key, value in raw_headers:
            name = key.decode("latin-1").lower()
            if name in ("authorization", "cookie"):
                headers[name] = value.decode("latin-1")

        raw_qs = scope.get("query_string") or b""
        query_string = (
            raw_qs.decode("latin-1") if isinstance(raw_qs, bytes) else str(raw_qs)
        )
        handshake: dict[str, Any] = {
            "headers": headers,
            "QUERY_STRING": query_string,
        }
        token = extract_access_token(
            handshake,
            allow_query_token=self._allow_query_token,
        )
        if not token:
            await self._reject(scope_type, send)
            return

        try:
            await self._authenticator.verify_access_token(token)
        except Exception:
            await self._reject(scope_type, send)
            return

        await self._app(scope, receive, send)

    async def _reject(self, scope_type: str, send: Send) -> None:
        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b'{"statusCode":401,"message":"Unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})


def create_socketio_app(
    config: WebSocketConfig,
    authenticator: AccessTokenAuthenticator,
    redis_service: RedisService | None = None,
    redis_url: str | None = None,
) -> tuple[ASGIApp, RealtimeGateway]:
    client_manager: Any = None
    if config.redis_adapter_enabled and redis_url:
        try:
            client_manager = socketio.AsyncRedisManager(redis_url)
        except Exception as exc:
            logger.warning("Could not initialize socketio AsyncRedisManager: %s", exc)

    cors_allowed_origins: Any = config.cors_origins
    if cors_allowed_origins is True:
        cors_allowed_origins = "*"

    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins=cors_allowed_origins,
        max_http_buffer_size=config.max_inbound_buffer_bytes,
        client_manager=client_manager,
    )

    connection_limiter = WsConnectionLimiter(
        max_connections_per_user=config.max_connections_per_user,
        redis_service=redis_service,
        redis_adapter_enabled=config.redis_adapter_enabled,
        connection_key_ttl_sec=config.connection_key_ttl_sec,
    )

    message_rate_limiter = WsMessageRateLimiter(
        messages_per_minute=config.messages_per_minute,
        redis_service=redis_service,
        redis_adapter_enabled=config.redis_adapter_enabled,
    )

    gateway = RealtimeGateway(
        sio=sio,
        authenticator=authenticator,
        connection_limiter=connection_limiter,
        message_rate_limiter=message_rate_limiter,
        allow_query_token=config.allow_query_token,
    )

    inner_app = socketio.ASGIApp(sio, socketio_path="")
    socket_app = AuthenticatedSocketIOApp(
        inner_app,
        authenticator=authenticator,
        allow_query_token=config.allow_query_token,
    )
    return socket_app, gateway
