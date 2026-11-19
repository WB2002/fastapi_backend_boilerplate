from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from app.core.feature_flag.audit_service import FeatureFlagAuditService
from app.core.feature_flag.dto import (
    CreateFeatureFlagDto,
    DeleteFeatureFlagDto,
    UpdateFeatureFlagDto,
)
from app.core.feature_flag.evaluator import feature_flag_to_dict
from app.core.feature_flag.feature_flag_service import FeatureFlagService
from app.core.feature_flag.repository import FeatureFlagRepository
from app.core.feature_flag.types import IFeatureFlag

logger = logging.getLogger("FeatureFlagManager")


class FeatureFlagManager:
    def __init__(
        self,
        repository: FeatureFlagRepository,
        feature_flag_service: FeatureFlagService,
        audit_service: FeatureFlagAuditService,
    ) -> None:
        self._repository = repository
        self._feature_flag_service = feature_flag_service
        self._audit_service = audit_service

    async def list(self) -> list[IFeatureFlag]:
        env = self._feature_flag_service.environment
        return await self._repository.load_all_flags(env)

    async def get(self, key: str) -> IFeatureFlag:
        env = self._feature_flag_service.environment
        flag = await self._repository.get_flag(env, key)
        if flag is None:
            raise HTTPException(status_code=404, detail=f"Feature flag {key} not found")
        return flag

    async def create(
        self,
        dto: CreateFeatureFlagDto,
        actor: dict[str, str],
    ) -> IFeatureFlag:
        self._assert_percentage_fields(dto)
        env = self._feature_flag_service.environment
        now_str = datetime.now(UTC).isoformat()

        payload: dict[str, Any] = {
            "key": dto.key,
            "enabled": dto.enabled,
            "type": dto.type,
            "environment": env,
            "createdAt": now_str,
            "updatedAt": now_str,
            "description": dto.description,
            "owner": dto.owner,
            "expiresAt": dto.expiresAt,
        }
        if dto.type == "percentage":
            payload["rolloutPercentage"] = dto.rolloutPercentage
            payload["targeting"] = (
                {"attribute": dto.targeting.attribute}
                if dto.targeting
                else {"attribute": "userId"}
            )

        status, _version, flag = await self._repository.cas_write(
            env=env,
            op="create",
            key=dto.key,
            expected_version=0,
            flag_payload=payload,
        )

        if status == "conflict":
            raise HTTPException(
                status_code=409, detail=f"Feature flag {dto.key} already exists"
            )
        if status != "ok" or flag is None:
            raise HTTPException(status_code=400, detail="Failed to create feature flag")

        self._feature_flag_service.cache.set(flag)

        await self._audit_service.record(
            key=flag.key,
            environment=env,
            action="create",
            actor_user_id=actor.get("userId", "unknown"),
            actor_email=actor.get("email", "unknown"),
            old_value=None,
            new_value=feature_flag_to_dict(flag),
            version=flag.version,
        )

        return flag

    async def update(
        self,
        key: str,
        dto: UpdateFeatureFlagDto,
        actor: dict[str, str],
    ) -> IFeatureFlag:
        self._assert_percentage_fields(dto)
        env = self._feature_flag_service.environment
        existing = await self.get(key)
        now_str = datetime.now(UTC).isoformat()

        payload: dict[str, Any] = {
            "key": key,
            "enabled": dto.enabled,
            "type": dto.type,
            "environment": env,
            "createdAt": existing.created_at,
            "updatedAt": now_str,
            "description": dto.description,
            "owner": dto.owner,
            "expiresAt": dto.expiresAt,
        }
        if dto.type == "percentage":
            payload["rolloutPercentage"] = dto.rolloutPercentage
            payload["targeting"] = (
                {"attribute": dto.targeting.attribute}
                if dto.targeting
                else {"attribute": "userId"}
            )

        status, _version, flag = await self._repository.cas_write(
            env=env,
            op="update",
            key=key,
            expected_version=dto.expectedVersion,
            flag_payload=payload,
        )

        if status == "conflict":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Version conflict for flag {key}; expected {dto.expectedVersion}"
                ),
            )
        if status == "not_found" or flag is None:
            raise HTTPException(status_code=404, detail=f"Feature flag {key} not found")

        self._feature_flag_service.cache.set(flag)

        await self._audit_service.record(
            key=flag.key,
            environment=env,
            action="update",
            actor_user_id=actor.get("userId", "unknown"),
            actor_email=actor.get("email", "unknown"),
            old_value=feature_flag_to_dict(existing),
            new_value=feature_flag_to_dict(flag),
            version=flag.version,
        )

        return flag

    async def delete(
        self,
        key: str,
        dto: DeleteFeatureFlagDto,
        actor: dict[str, str],
    ) -> None:
        env = self._feature_flag_service.environment
        existing = await self.get(key)

        status, version, _ = await self._repository.cas_write(
            env=env,
            op="delete",
            key=key,
            expected_version=dto.expectedVersion,
            flag_payload=None,
        )

        if status == "conflict":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Version conflict for flag {key}; expected {dto.expectedVersion}"
                ),
            )
        if status == "not_found":
            raise HTTPException(status_code=404, detail=f"Feature flag {key} not found")

        self._feature_flag_service.cache.delete(key)

        await self._audit_service.record(
            key=key,
            environment=env,
            action="delete",
            actor_user_id=actor.get("userId", "unknown"),
            actor_email=actor.get("email", "unknown"),
            old_value=feature_flag_to_dict(existing),
            new_value=None,
            version=version,
        )

    def _assert_percentage_fields(
        self, dto: CreateFeatureFlagDto | UpdateFeatureFlagDto
    ) -> None:
        if dto.type == "percentage":
            if (
                dto.rolloutPercentage is None
                or dto.rolloutPercentage < 0
                or dto.rolloutPercentage > 100
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "rolloutPercentage must be between 0 and 100 "
                        "for percentage flags"
                    ),
                )
            if not dto.targeting:
                raise HTTPException(
                    status_code=400,
                    detail="targeting attribute is required for percentage flags",
                )
