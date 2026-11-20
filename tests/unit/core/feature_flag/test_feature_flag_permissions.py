from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.auth.constants import UserRole
from app.auth.types import AuthenticatedUser
from app.core.feature_flag.feature_flag_permissions import assert_feature_flag_admin


@pytest.mark.asyncio
async def test_stale_jwt_admin_rejected_when_db_user() -> None:
    user = AuthenticatedUser(
        id="u1",
        email="user@example.com",
        jti="j1",
        role="admin",
    )
    auth = MagicMock()
    auth.is_admin_email.return_value = False
    auth.user_repository.find_by_id = AsyncMock(
        return_value=MagicMock(role=UserRole.USER, email="user@example.com")
    )
    with pytest.raises(HTTPException) as exc:
        await assert_feature_flag_admin(user, auth_service=auth)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_allowlisted_email_without_admin_role() -> None:
    user = AuthenticatedUser(
        id="u1",
        email="boss@example.com",
        jti="j1",
        role="user",
    )
    auth = MagicMock()
    auth.is_admin_email.return_value = True
    auth.user_repository.find_by_id = AsyncMock(
        return_value=MagicMock(role=UserRole.USER, email="boss@example.com")
    )
    result = await assert_feature_flag_admin(user, auth_service=auth)
    assert result.id == "u1"
