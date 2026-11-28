from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.auth_service import AuthService
from app.auth.guards.jwt_auth_guard import get_current_user
from app.auth.types import AuthenticatedUser
from app.core.feature_flag.audit_service import FeatureFlagAuditService
from app.core.feature_flag.dto import (
    CreateFeatureFlagDto,
    DeleteFeatureFlagDto,
    UpdateFeatureFlagDto,
)
from app.core.feature_flag.evaluator import feature_flag_to_dict
from app.core.feature_flag.feature_flag_manager import FeatureFlagManager
from app.core.feature_flag.feature_flag_permissions import (
    assert_feature_flag_admin,
    get_auth_service,
)
from app.core.feature_flag.feature_flag_service import FeatureFlagService
from app.core.feature_flag.repository import FeatureFlagRepository

_ROUTE_VALIDATION = BaseModel

router = APIRouter(tags=["FeatureFlags"])
_logger = logging.getLogger("FeatureFlagController")


async def require_feature_flag_admin(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthenticatedUser:
    try:
        return await assert_feature_flag_admin(user, auth_service=auth_service)
    except HTTPException:
        raise
    except Exception as exc:
        _logger.warning("Feature-flag admin authorization failed: %s", exc)
        raise HTTPException(
            status_code=403,
            detail="Insufficient feature-flag permissions",
        ) from exc


def get_feature_flag_manager(
    request: Request,
    _admin: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
) -> FeatureFlagManager:
    redis = getattr(request.app.state, "redis_service", None)
    mongo = getattr(request.app.state, "mongo_service", None)
    repo = FeatureFlagRepository(redis_service=redis)
    svc = FeatureFlagService(repo)
    audit = FeatureFlagAuditService(mongo_service=mongo)
    return FeatureFlagManager(repo, svc, audit)


@router.get("/feature-flags")
async def list_flags(
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
    manager: Annotated[FeatureFlagManager, Depends(get_feature_flag_manager)],
) -> list[dict[str, Any]]:
    flags = await manager.list()
    return [feature_flag_to_dict(f) for f in flags]


@router.get("/feature-flags/{key}")
async def get_flag(
    key: str,
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
    manager: Annotated[FeatureFlagManager, Depends(get_feature_flag_manager)],
) -> dict[str, Any]:
    flag = await manager.get(key)
    return feature_flag_to_dict(flag)


@router.post("/feature-flags")
async def create_flag(
    dto: CreateFeatureFlagDto,
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
    manager: Annotated[FeatureFlagManager, Depends(get_feature_flag_manager)],
) -> dict[str, Any]:
    actor = {"userId": user.id, "email": user.email}
    flag = await manager.create(dto, actor)
    return feature_flag_to_dict(flag)


@router.put("/feature-flags/{key}")
async def update_flag(
    key: str,
    dto: UpdateFeatureFlagDto,
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
    manager: Annotated[FeatureFlagManager, Depends(get_feature_flag_manager)],
) -> dict[str, Any]:
    actor = {"userId": user.id, "email": user.email}
    flag = await manager.update(key, dto, actor)
    return feature_flag_to_dict(flag)


@router.delete("/feature-flags/{key}")
async def delete_flag(
    key: str,
    dto: DeleteFeatureFlagDto,
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
    manager: Annotated[FeatureFlagManager, Depends(get_feature_flag_manager)],
) -> dict[str, bool]:
    actor = {"userId": user.id, "email": user.email}
    await manager.delete(key, dto, actor)
    return {"ok": True}
