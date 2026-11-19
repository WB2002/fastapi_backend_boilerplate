from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from fastapi import Request
from starlette.requests import HTTPConnection
from starlette.routing import Match

from app.auth.access_token_authenticator import (
    AccessTokenAuthenticator,
    UnauthorizedException,
)
from app.auth.types import AuthenticatedUser
from app.common.constants import ACCESS_TOKEN_COOKIE, IS_PUBLIC_KEY
from app.common.utils.object_utils import read_cookie_field
from app.core.logger.logger_context import LoggerContext


def _find_route_and_endpoint(
    request: Request | Any,
) -> tuple[Any | None, Any | None]:
    scope = getattr(request, "scope", None)
    if not isinstance(scope, Mapping):
        return None, None
    endpoint = scope.get("endpoint")
    route_obj = scope.get("route")
    if endpoint is not None:
        return route_obj, endpoint

    app = scope.get("app")
    router = getattr(app, "router", None)
    routes = getattr(router, "routes", None)
    if not routes:
        return None, None
    for route in routes:
        try:
            match, child_scope = route.matches(scope)
            if match == Match.FULL:
                ep = child_scope.get("endpoint") or getattr(route, "endpoint", None)
                return route, ep
        except Exception:  # noqa: S112
            continue
    return None, None


def is_request_public(
    request: Request | Any,
    *,
    handler: Any = None,
    controller: Any = None,
) -> bool:
    if handler is not None and bool(getattr(handler, IS_PUBLIC_KEY, False)):
        return True
    if controller is not None and bool(getattr(controller, IS_PUBLIC_KEY, False)):
        return True
    route, endpoint = _find_route_and_endpoint(request)
    if endpoint is not None and bool(getattr(endpoint, IS_PUBLIC_KEY, False)):
        return True
    if route is not None and bool(getattr(route, IS_PUBLIC_KEY, False)):
        return True
    return False


def extract_jwt_from_request(request: Request | Mapping[str, Any] | Any) -> str | None:
    """Extract JWT from `access_token` cookie first, then `Authorization: Bearer`."""
    cookies: Mapping[str, Any] | None = None
    headers: Mapping[str, Any] | None = None

    if isinstance(request, Mapping) and not isinstance(request, HTTPConnection):
        cookies = request.get("cookies")
        headers = request.get("headers")
    else:
        cookies = getattr(request, "cookies", None)
        headers = getattr(request, "headers", None)

    cookie_token = read_cookie_field(cookies, ACCESS_TOKEN_COOKIE)
    if isinstance(cookie_token, str) and cookie_token.strip():
        return cookie_token.strip()

    if isinstance(headers, Mapping):
        auth_val: str | None = None
        for k, v in headers.items():
            if str(k).lower() == "authorization" and isinstance(v, str):
                auth_val = v
                break
        if auth_val is not None:
            parts = auth_val.strip().split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                candidate = parts[1].strip()
                if candidate:
                    return candidate
    return None


class JwtAuthGuard:
    """Global default-deny JWT guard matching NestJS JwtAuthGuard + JwtStrategy."""

    def __init__(
        self,
        authenticator: AccessTokenAuthenticator | None = None,
        *,
        auth_service: Any = None,
        redis_service: Any = None,
        config_service: Any = None,
    ) -> None:
        self._authenticator = authenticator or AccessTokenAuthenticator(
            auth_service=auth_service,
            redis_service=redis_service,
            config_service=config_service,
        )

    def _resolve_authenticator(
        self, request: Request | Any
    ) -> AccessTokenAuthenticator:
        if self._authenticator is not None:
            return self._authenticator
        app = getattr(request, "app", None)
        state = getattr(app, "state", None) if app is not None else None
        auth = (
            getattr(state, "access_token_authenticator", None)
            if state is not None
            else None
        )
        if isinstance(auth, AccessTokenAuthenticator):
            return auth
        return AccessTokenAuthenticator(
            auth_service=getattr(state, "auth_service", None),
            redis_service=getattr(state, "redis_service", None),
            config_service=getattr(state, "env", os.environ),
        )

    async def can_activate(
        self,
        request: Request | Any,
        *,
        handler: Any = None,
        controller: Any = None,
    ) -> bool:
        if is_request_public(request, handler=handler, controller=controller):
            return True

        token = extract_jwt_from_request(request)
        if not token:
            raise UnauthorizedException("Unauthorized")

        authenticator = self._resolve_authenticator(request)
        user: AuthenticatedUser = await authenticator.verify_access_token(token)

        state = getattr(request, "state", None)
        if state is not None:
            state.user = user
        elif isinstance(request, dict):
            request["user"] = user

        scope = getattr(request, "scope", None)
        if isinstance(scope, dict):
            scope["user"] = user
            if isinstance(scope.get("state"), dict):
                scope["state"]["user"] = user

        LoggerContext.update(user_id=user.id)
        return True

    async def canActivate(
        self,
        request: Request | Any,
        *,
        handler: Any = None,
        controller: Any = None,
    ) -> bool:
        return await self.can_activate(
            request,
            handler=handler,
            controller=controller,
        )

    async def __call__(self, request: Request) -> bool:
        return await self.can_activate(request)


def get_current_user(request: Request) -> AuthenticatedUser:
    """FastAPI dependency extracting the authenticated user."""
    user = getattr(request.state, "user", None)
    if not isinstance(user, AuthenticatedUser):
        raise UnauthorizedException("Unauthorized")
    return user


__all__ = [
    "JwtAuthGuard",
    "extract_jwt_from_request",
    "get_current_user",
    "is_request_public",
]
