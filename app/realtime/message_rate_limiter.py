from __future__ import annotations

import time

from app.common.redis.redis_service import RedisService
from app.realtime.constants import DEFAULT_WEBSOCKET_MESSAGES_PER_MINUTE

MESSAGE_KEY_PREFIX = "ws:msg:"
MESSAGE_WINDOW_SEC = 60


class WsMessageRateLimiter:
    def __init__(
        self,
        messages_per_minute: int = DEFAULT_WEBSOCKET_MESSAGES_PER_MINUTE,
        redis_service: RedisService | None = None,
        redis_adapter_enabled: bool = False,
    ) -> None:
        self._messages_per_minute = messages_per_minute
        self._redis_service = redis_service
        self._redis_adapter_enabled = redis_adapter_enabled
        self._local_counts: dict[str, dict[str, float]] = {}

    async def is_allowed(self, user_id: str) -> bool:
        if (
            self._redis_adapter_enabled
            and self._redis_service is not None
            and self._redis_service.is_connected
        ):
            client = self._redis_service.get_client()
            key = f"{MESSAGE_KEY_PREFIX}{user_id}"
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, MESSAGE_WINDOW_SEC)
            return bool(count <= self._messages_per_minute)

        now = time.time()
        entry = self._local_counts.get(user_id)
        if entry is None or entry["reset_at"] <= now:
            self._local_counts[user_id] = {
                "count": 1.0,
                "reset_at": now + MESSAGE_WINDOW_SEC,
            }
            return True

        entry["count"] += 1.0
        return bool(entry["count"] <= self._messages_per_minute)
