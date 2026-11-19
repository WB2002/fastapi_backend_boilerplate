from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app.auth.constants import UserRole
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
from app.core.feature_flag.feature_flag_service import FeatureFlagService
from app.core.feature_flag.repository import FeatureFlagRepository

router = APIRouter(prefix="/feature-flags", tags=["FeatureFlags"])


def require_feature_flag_admin(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    role = user.role.value if isinstance(user.role, UserRole) else str(user.role)
    if role.lower() != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=403, detail="Insufficient feature-flag permissions"
        )
    return user


def get_feature_flag_manager() -> FeatureFlagManager:
    repo = FeatureFlagRepository()
    svc = FeatureFlagService(repo)
    audit = FeatureFlagAuditService()
    return FeatureFlagManager(repo, svc, audit)


@router.get("")
async def list_flags(
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
    manager: Annotated[FeatureFlagManager, Depends(get_feature_flag_manager)],
) -> list[dict[str, Any]]:
    flags = await manager.list()
    return [feature_flag_to_dict(f) for f in flags]


@router.get("/{key}")
async def get_flag(
    key: str,
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
    manager: Annotated[FeatureFlagManager, Depends(get_feature_flag_manager)],
) -> dict[str, Any]:
    flag = await manager.get(key)
    return feature_flag_to_dict(flag)


@router.post("")
async def create_flag(
    dto: CreateFeatureFlagDto,
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
    manager: Annotated[FeatureFlagManager, Depends(get_feature_flag_manager)],
) -> dict[str, Any]:
    actor = {"userId": user.id, "email": user.email}
    flag = await manager.create(dto, actor)
    return feature_flag_to_dict(flag)


@router.put("/{key}")
async def update_flag(
    key: str,
    dto: UpdateFeatureFlagDto,
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
    manager: Annotated[FeatureFlagManager, Depends(get_feature_flag_manager)],
) -> dict[str, Any]:
    actor = {"userId": user.id, "email": user.email}
    flag = await manager.update(key, dto, actor)
    return feature_flag_to_dict(flag)


@router.delete("/{key}")
async def delete_flag(
    key: str,
    dto: DeleteFeatureFlagDto,
    user: Annotated[AuthenticatedUser, Depends(require_feature_flag_admin)],
    manager: Annotated[FeatureFlagManager, Depends(get_feature_flag_manager)],
) -> dict[str, bool]:
    actor = {"userId": user.id, "email": user.email}
    await manager.delete(key, dto, actor)
    return {"ok": True}
