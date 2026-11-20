from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request

from app.common.country_access.country_access_dependency import CountryAccessDependency


@pytest.mark.asyncio
async def test_denies_when_guard_raises() -> None:
    guard = MagicMock()
    guard.check_access = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Country access denied")
    )
    service = MagicMock()
    service.refresh_snapshot = AsyncMock()
    service.start_periodic_refresh = MagicMock()
    dep = CountryAccessDependency(guard, service)
    request = Request(
        {"type": "http", "method": "GET", "path": "/uploads", "headers": []}
    )
    with pytest.raises(HTTPException) as exc:
        await dep(request)
    assert exc.value.status_code == 403
