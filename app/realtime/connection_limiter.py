from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, status

from app.common.redis.redis_service import RedisService
from app.realtime.constants import (
    DEFAULT_WEBSOCKET_CONNECTION_KEY_TTL_SEC,
    DEFAULT_WEBSOCKET_MAX_CONNECTIONS_PER_USER,
)

logger = logging.getLogger("WsConnectionLimiter")

CONNECTION_KEY_PREFIX = "ws:conn:"
CONNECTION_ALIVE_PREFIX = "ws:conn:alive:"


class WsConnectionLimiter:
    def __init__(
        self,
        max_connections_per_user: int = DEFAULT_WEBSOCKET_MAX_CONNECTIONS_PER_USER,
        redis_service: RedisService | None = None,
        redis_adapter_enabled: bool = False,
        connection_key_ttl_sec: int = DEFAULT_WEBSOCKET_CONNECTION_KEY_TTL_SEC,
    ) -> None:
        self._max_connections = max_connections_per_user
        self._redis_service = redis_service
        self._redis_adapter_enabled = redis_adapter_enabled
        self._ttl_sec = connection_key_ttl_sec
        self._local_connections: dict[str, set[str]] = {}

    def _alive_key(self, user_id: str, socket_id: str) -> str:
        return f"{CONNECTION_ALIVE_PREFIX}{user_id}:{socket_id}"

    async def _prune_stale_redis_connections(
        self, client: Any, user_id: str, set_key: str
    ) -> None:
        members = await client.smembers(set_key)
        if not members:
            return
        for member in members:
            socket_id = member.decode() if isinstance(member, bytes) else str(member)
            exists = await client.exists(self._alive_key(user_id, socket_id))
            if not exists:
                await client.srem(set_key, socket_id)

    async def register_connection(self, user_id: str, socket_id: str) -> None:
        if (
            self._redis_adapter_enabled
            and self._redis_service is not None
            and self._redis_service.is_connected
        ):
            client = self._redis_service.get_client()
            set_key = f"{CONNECTION_KEY_PREFIX}{user_id}"
            await self._prune_stale_redis_connections(client, user_id, set_key)
            await client.sadd(set_key, socket_id)
            alive_key = self._alive_key(user_id, socket_id)
            await client.set(alive_key, "1", ex=self._ttl_sec)
            await client.expire(set_key, self._ttl_sec)
            count = await client.scard(set_key)
            if count > self._max_connections:
                await client.srem(set_key, socket_id)
                await client.delete(alive_key)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="WebSocket connection limit exceeded",
                )
            return

        sockets = self._local_connections.setdefault(user_id, set())
        if len(sockets) >= self._max_connections:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="WebSocket connection limit exceeded",
            )
        sockets.add(socket_id)

    async def release_connection(self, user_id: str, socket_id: str) -> None:
        if (
            self._redis_adapter_enabled
            and self._redis_service is not None
            and self._redis_service.is_connected
        ):
            client = self._redis_service.get_client()
            set_key = f"{CONNECTION_KEY_PREFIX}{user_id}"
            alive_key = self._alive_key(user_id, socket_id)
            await client.srem(set_key, socket_id)
            await client.delete(alive_key)
            return

        sockets = self._local_connections.get(user_id)
        if sockets is not None:
            sockets.discard(socket_id)
            if not sockets:
                self._local_connections.pop(user_id, None)
