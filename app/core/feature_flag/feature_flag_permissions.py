from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from app.auth.auth_service import AuthService
from app.auth.constants import UserRole
from app.auth.types import AuthenticatedUser

logger = logging.getLogger("FeatureFlagPermissions")


def resolve_auth_service(request: Request) -> AuthService | None:
    auth = getattr(request.app.state, "auth_service", None)
    if isinstance(auth, AuthService):
        return auth
    return None


async def assert_feature_flag_admin(
    user: AuthenticatedUser,
    *,
    auth_service: AuthService | None = None,
) -> AuthenticatedUser:
    if auth_service is not None:
        try:
            if auth_service.is_admin_email(user.email):
                return user
        except Exception:
            logger.warning(
                "Feature-flag allowlist check failed for %s",
                user.email,
                exc_info=True,
            )

    jwt_role = str(user.role).lower()
    if jwt_role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=403,
            detail="Insufficient feature-flag permissions",
        )

    if auth_service is None:
        raise HTTPException(
            status_code=403,
            detail="Insufficient feature-flag permissions",
        )

    try:
        db_user = await auth_service.user_repository.find_by_id(user.id)
    except Exception:
        logger.warning("Feature-flag admin DB lookup failed for user %s", user.id)
        raise HTTPException(
            status_code=403,
            detail="Insufficient feature-flag permissions",
        ) from None

    if db_user is None:
        raise HTTPException(
            status_code=403,
            detail="Insufficient feature-flag permissions",
        )

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


__all__ = ["assert_feature_flag_admin", "resolve_auth_service"]
