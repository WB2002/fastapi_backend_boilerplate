from __future__ import annotations

from typing import Any

from app.core.feature_flag.feature_flag_service import FeatureFlagService


class FeatureFlagHealthIndicator:
    def __init__(self, feature_flag_service: FeatureFlagService) -> None:
        self._service = feature_flag_service

    def get_health(self) -> dict[str, Any]:
        status = self._service.get_health_status()
        return {
            "status": "up",
            "stale": status.stale,
            "healthy": status.healthy,
            "redisAvailable": status.redis_available,
            "pubSubConnected": status.pub_sub_connected,
            "flagCount": status.flag_count,
            "lastReconciledAt": status.last_reconciled_at,
            "lastError": status.last_error,
        }
