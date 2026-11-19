from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from app.core.feature_flag.cache import FeatureFlagCache
from app.core.feature_flag.constants import DEFAULT_FEATURE_FLAG_ENV
from app.core.feature_flag.evaluator import evaluate_feature_flag
from app.core.feature_flag.reconciler import FeatureFlagReconciler
from app.core.feature_flag.repository import FeatureFlagRepository
from app.core.feature_flag.subscriber import FeatureFlagSubscriber
from app.core.feature_flag.types import (
    IFeatureFlagContext,
    IFeatureFlagEvaluationOptions,
    IFeatureFlagHealthStatus,
)
from app.core.telemetry.emf_metrics import emit_feature_flag_metrics_sync

logger = logging.getLogger("FeatureFlagService")


class FeatureFlagService:
    def __init__(
        self,
        repository: FeatureFlagRepository,
        subscriber: FeatureFlagSubscriber | None = None,
        reconciler: FeatureFlagReconciler | None = None,
        cache: FeatureFlagCache | None = None,
        environment: str | None = None,
    ) -> None:
        self._repository = repository
        self._cache = cache or FeatureFlagCache()
        self._environment = (
            environment
            or os.getenv("FEATURE_FLAG_ENV")
            or os.getenv("NODE_ENV")
            or DEFAULT_FEATURE_FLAG_ENV
        )
        self._subscriber = subscriber or FeatureFlagSubscriber(
            self._environment, self._cache, self._repository
        )
        self._reconciler = reconciler or FeatureFlagReconciler(self.reconcile)
        self._last_reconciled_at: datetime | None = None
        self._last_error: str | None = None
        self._started_at = datetime.now(UTC)
        self._is_redis_available = False

    @property
    def environment(self) -> str:
        return self._environment

    @property
    def cache(self) -> FeatureFlagCache:
        return self._cache

    async def bootstrap(self) -> None:
        try:
            flags = await self._repository.load_all_flags(self._environment)
            self._cache.load_all(flags)
            self._is_redis_available = True
            self._last_reconciled_at = datetime.now(UTC)
            self._last_error = None
        except Exception as exc:
            self._is_redis_available = False
            self._last_error = str(exc)
            logger.warning("Feature flag bootstrap warning: %s", exc)

        await self._subscriber.start()
        self._reconciler.start()

    async def reconcile(self) -> None:
        try:
            flags = await self._repository.load_all_flags(self._environment)
            self._cache.load_all(flags)
            self._is_redis_available = True
            self._last_reconciled_at = datetime.now(UTC)
            self._last_error = None
        except Exception as exc:
            self._is_redis_available = False
            self._last_error = str(exc)
            logger.warning("Feature flag reconcile failure: %s", exc)

    def is_enabled(
        self,
        key: str,
        context: IFeatureFlagContext | None = None,
        options: IFeatureFlagEvaluationOptions | None = None,
    ) -> bool:
        ctx = context or IFeatureFlagContext()
        opts = options or IFeatureFlagEvaluationOptions()
        flag = self._cache.get(key)
        res = evaluate_feature_flag(flag, ctx, opts)

        emit_feature_flag_metrics_sync(
            enabled=True,
            name="feature_flag_evaluation_total",
            value=1,
            dimensions={
                "Environment": self._environment,
                "FlagKey": key,
                "Result": str(res.enabled),
            },
        )

        return res.enabled

    def get_health_status(self) -> IFeatureFlagHealthStatus:
        now = datetime.now(UTC)
        stale = False
        if self._last_reconciled_at:
            delta = (now - self._last_reconciled_at).total_seconds()
            stale = delta > 90

        healthy = not stale and self._last_error is None
        last_rec_str = (
            self._last_reconciled_at.isoformat() if self._last_reconciled_at else None
        )

        return IFeatureFlagHealthStatus(
            healthy=healthy,
            stale=stale,
            redis_available=self._is_redis_available,
            pub_sub_connected=self._subscriber.is_connected,
            flag_count=self._cache.size(),
            last_reconciled_at=last_rec_str,
            last_error=self._last_error,
        )

    async def close(self) -> None:
        self._reconciler.stop()
        await self._subscriber.stop()
