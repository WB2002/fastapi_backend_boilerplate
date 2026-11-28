from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from app.auth.auth_service import AuthService
from app.auth.constants import UserRole
from app.auth.types import AuthenticatedUser

logger = logging.getLogger("FeatureFlagPermissions")


def get_auth_service(request: Request) -> AuthService:
    auth = getattr(request.app.state, "auth_service", None)
    if not isinstance(auth, AuthService):
        raise HTTPException(status_code=503, detail="Auth service unavailable")
    return auth


async def assert_feature_flag_admin(
    user: AuthenticatedUser,
    *,
    auth_service: AuthService,
) -> AuthenticatedUser:
    if auth_service.is_admin_email(user.email):
        return user

    jwt_role = str(user.role).lower()
    if jwt_role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=403,
            detail="Insufficient feature-flag permissions",
        )

    db_user = None
    try:
        db_user = await auth_service.user_repository.find_by_id(user.id)
    except Exception:
        db_user = None
    if db_user is not None:
        raw_role = db_user.role
        if isinstance(raw_role, UserRole):
            is_admin = raw_role == UserRole.ADMIN
        else:
            is_admin = str(raw_role).lower() == UserRole.ADMIN.value
        if not is_admin:
            raise HTTPException(
                status_code=403,
                detail="Insufficient feature-flag permissions",
            )
    return user


__all__ = ["assert_feature_flag_admin", "get_auth_service"]
