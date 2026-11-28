from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.access_token_authenticator import UnauthorizedException
from app.auth.auth_service import AuthService
from app.auth.types import AuthenticatedUser
from app.common.decorators import skip_country_access
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
    request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthenticatedUser:
    user = getattr(request.state, "user", None)
    if not isinstance(user, AuthenticatedUser):
        raise UnauthorizedException("Unauthorized")
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


def build_feature_flag_manager(request: Request) -> FeatureFlagManager:
    redis = getattr(request.app.state, "redis_service", None)
    mongo = getattr(request.app.state, "mongo_service", None)
    repo = FeatureFlagRepository(redis_service=redis)
    svc = FeatureFlagService(repo)
    audit = FeatureFlagAuditService(mongo_service=mongo)
    return FeatureFlagManager(repo, svc, audit)


@router.get("/feature-flags")
@skip_country_access
async def list_flags(
    request: Request,
    _admin: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
) -> list[dict[str, Any]]:
    manager = build_feature_flag_manager(request)
    flags = await manager.list()
    return [feature_flag_to_dict(f) for f in flags]


@router.get("/feature-flags/{key}")
@skip_country_access
async def get_flag(
    key: str,
    request: Request,
    _admin: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
) -> dict[str, Any]:
    manager = build_feature_flag_manager(request)
    flag = await manager.get(key)
    return feature_flag_to_dict(flag)


@router.post("/feature-flags")
@skip_country_access
async def create_flag(
    dto: CreateFeatureFlagDto,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
) -> dict[str, Any]:
    manager = build_feature_flag_manager(request)
    actor = {"userId": user.id, "email": user.email}
    flag = await manager.create(dto, actor)
    return feature_flag_to_dict(flag)


@router.put("/feature-flags/{key}")
@skip_country_access
async def update_flag(
    key: str,
    dto: UpdateFeatureFlagDto,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
) -> dict[str, Any]:
    manager = build_feature_flag_manager(request)
    actor = {"userId": user.id, "email": user.email}
    flag = await manager.update(key, dto, actor)
    return feature_flag_to_dict(flag)


@router.delete("/feature-flags/{key}")
@skip_country_access
async def delete_flag(
    key: str,
    dto: DeleteFeatureFlagDto,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
) -> dict[str, bool]:
    manager = build_feature_flag_manager(request)
    actor = {"userId": user.id, "email": user.email}
    await manager.delete(key, dto, actor)
    return {"ok": True}
