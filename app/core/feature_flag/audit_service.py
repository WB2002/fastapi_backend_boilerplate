from __future__ import annotations

import logging
from typing import Any

from app.core.feature_flag.schemas import (
    FeatureFlagAudit,
    FeatureFlagAuditAction,
)

logger = logging.getLogger("FeatureFlagAuditService")


class FeatureFlagAuditService:
    def __init__(self, mongo_service: Any = None) -> None:
        self._mongo_service = mongo_service
        self._memory_audits: list[FeatureFlagAudit] = []

    async def record(
        self,
        *,
        key: str,
        environment: str,
        action: FeatureFlagAuditAction,
        actor_user_id: str,
        actor_email: str,
        old_value: dict[str, Any] | None,
        new_value: dict[str, Any] | None,
        version: int,
    ) -> None:
        audit = FeatureFlagAudit(
            key=key,
            environment=environment,
            action=action,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            old_value=old_value,
            new_value=new_value,
            version=version,
        )

        collection = self._get_collection()
        if collection is not None:
            try:
                await collection.insert_one(audit.to_mongo_doc())
                return
            except Exception as exc:
                logger.warning("MongoDB audit insert failed, saving in memory: %s", exc)

        self._memory_audits.append(audit)

    def _get_collection(self) -> Any:
        if self._mongo_service is None:
            return None
        get_db = getattr(self._mongo_service, "get_database", None)
        if not callable(get_db):
            return None
        db = get_db()
        return db["feature_flag_audits"] if db is not None else None

    def get_memory_audits(self) -> list[FeatureFlagAudit]:
        return list(self._memory_audits)
