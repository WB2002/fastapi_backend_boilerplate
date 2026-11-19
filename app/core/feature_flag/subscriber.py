from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.common.redis.redis_service import RedisService
from app.core.feature_flag.cache import FeatureFlagCache
from app.core.feature_flag.constants import feature_flag_events_channel
from app.core.feature_flag.repository import FeatureFlagRepository

logger = logging.getLogger("FeatureFlagSubscriber")


class FeatureFlagSubscriber:
    def __init__(
        self,
        environment: str,
        cache: FeatureFlagCache,
        repository: FeatureFlagRepository,
        redis_service: RedisService | None = None,
    ) -> None:
        self._environment = environment
        self._cache = cache
        self._repository = repository
        self._redis_service = redis_service
        self._is_running = False
        self._task: asyncio.Task[None] | None = None
        self._pub_sub_connected = False

    @property
    def is_connected(self) -> bool:
        return self._pub_sub_connected

    async def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True

        if self._redis_service is None or not self._redis_service.is_connected:
            logger.info(
                "Redis not connected; FeatureFlagSubscriber running in local mode"
            )
            return

        channel = feature_flag_events_channel(self._environment)

        async def _listen() -> None:
            while self._is_running:
                try:
                    client = self._redis_service.get_client()  # type: ignore[union-attr]
                    pubsub = client.pubsub()
                    await pubsub.subscribe(channel)
                    self._pub_sub_connected = True
                    logger.info("Subscribed to feature flag events on %s", channel)

                    while self._is_running:
                        msg = await pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=1.0
                        )
                        if msg and msg.get("type") == "message":
                            await self._handle_event(msg.get("data"))

                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    self._pub_sub_connected = False
                    logger.warning("Feature flag pub/sub loop error: %s", exc)
                    await asyncio.sleep(2.0)

        self._task = asyncio.create_task(_listen())

    async def stop(self) -> None:
        self._is_running = False
        self._pub_sub_connected = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _handle_event(self, raw_data: Any) -> None:
        try:
            text = raw_data if isinstance(raw_data, str) else raw_data.decode()
            data = json.loads(text)
            event_type = data.get("event")
            flag_key = data.get("key")

            if not flag_key:
                return

            if event_type == "FLAG_DELETED":
                self._cache.delete(flag_key)
            elif event_type == "FLAG_UPDATED":
                updated = await self._repository.get_flag(self._environment, flag_key)
                if updated is not None:
                    self._cache.set(updated)
        except Exception as exc:
            logger.error("Error handling feature flag event: %s", exc)
