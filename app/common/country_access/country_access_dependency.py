from __future__ import annotations

from fastapi import Request

from app.common.country_access.country_access_guard import CountryAccessGuard
from app.common.country_access.country_access_service import CountryAccessService


class CountryAccessDependency:
    """FastAPI router dependency enforcing GeoIP country access rules."""

    def __init__(
        self,
        guard: CountryAccessGuard,
        service: CountryAccessService,
    ) -> None:
        self._guard = guard
        self._service = service
        self._refresh_started = False

    async def _ensure_refresh_loop(self) -> None:
        if self._refresh_started:
            return
        await self._service.refresh_snapshot()
        self._service.start_periodic_refresh()
        self._refresh_started = True

    async def __call__(self, request: Request) -> bool:
        await self._ensure_refresh_loop()
        await self._guard.check_access(request)
        return True


__all__ = ["CountryAccessDependency"]
