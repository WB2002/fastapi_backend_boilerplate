from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.common.country_access.country_access_service import (
    CountryAccessService,
)
from app.common.country_access.trust_proxy import resolve_client_ip_from_request
from app.common.country_access.types import GeoLocationService
from app.common.decorators import should_skip_country_access
from app.core.telemetry.emf_metrics import CountryAccessTelemetry

SKIP_COUNTRY_ACCESS_ATTR = "_skip_country_access"


def skip_country_access[F: Callable[..., Any] | type[Any]](target: F) -> F:
    """Decorator marking an endpoint or class as exempt from country access checks."""
    setattr(target, SKIP_COUNTRY_ACCESS_ATTR, True)
    return target


class CountryAccessGuard:
    def __init__(
        self,
        service: CountryAccessService,
        geo_location_service: GeoLocationService,
        telemetry: CountryAccessTelemetry | None = None,
    ) -> None:
        self._service = service
        self._geo = geo_location_service
        self._telemetry = telemetry or CountryAccessTelemetry()

    async def check_access(self, request: Request) -> None:
        # Check bypass paths
        path = request.url.path
        if self._is_bypass_path(path):
            return

        # Check endpoint exemption
        endpoint = request.scope.get("endpoint")
        route = request.scope.get("route")
        if endpoint and (
            getattr(endpoint, SKIP_COUNTRY_ACCESS_ATTR, False)
            or should_skip_country_access(endpoint, route)
        ):
            return

        if not self._service.is_active():
            await self._telemetry.inactive_bypass()
            return

        ip = self._resolve_client_ip(request)
        if not ip:
            await self._telemetry.invalid_ip()
            await self._telemetry.access_denied(None, "invalid_ip")
            raise HTTPException(status_code=403, detail="Country access denied")

        country_code = self._geo.get_country_code(ip)
        if not country_code:
            if self._service.get_unknown_country_policy() == "allow":
                return
            await self._telemetry.access_denied(None, "unknown_country")
            raise HTTPException(status_code=403, detail="Country access denied")

        if not self._service.is_country_allowed(country_code):
            await self._telemetry.access_denied(country_code, "country_not_allowed")
            raise HTTPException(status_code=403, detail="Country access denied")

    def _is_bypass_path(self, path: str) -> bool:
        clean = path.split("?")[0]
        for bypass in self._service.get_bypass_paths():
            if clean == bypass or clean.startswith(f"{bypass}/"):
                return True
        return False

    def _resolve_client_ip(self, request: Request) -> str | None:
        return resolve_client_ip_from_request(request)


class CountryAccessMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        guard: CountryAccessGuard,
    ) -> None:
        self.app = app
        self._guard = guard

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        await self._guard.check_access(request)
        await self.app(scope, receive, send)
