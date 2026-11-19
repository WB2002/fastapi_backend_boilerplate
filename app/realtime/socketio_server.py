from __future__ import annotations

import logging
from typing import Any

import socketio

from app.auth.access_token_authenticator import AccessTokenAuthenticator
from app.common.config.parsers import WebSocketConfig
from app.common.redis.redis_service import RedisService
from app.realtime.connection_limiter import WsConnectionLimiter
from app.realtime.gateway import RealtimeGateway
from app.realtime.message_rate_limiter import WsMessageRateLimiter

logger = logging.getLogger("SocketIoServer")


def create_socketio_app(
    config: WebSocketConfig,
    authenticator: AccessTokenAuthenticator,
    redis_service: RedisService | None = None,
    redis_url: str | None = None,
) -> tuple[socketio.ASGIApp, RealtimeGateway]:
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

    path = config.path.strip("/")
    socket_app = socketio.ASGIApp(sio, socketio_path=path)
    return socket_app, gateway
