from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError

from app.auth.access_token_authenticator import UnauthorizedException
from app.auth.auth_service import AuthService
from app.auth.constants import DEFAULT_ACCESS_TOKEN_TTL, DEFAULT_SESSION_TTL
from app.auth.dto import LoginDto, RegisterDto
from app.auth.guards.jwt_auth_guard import JwtAuthGuard, get_current_user
from app.auth.types import AuthenticatedUser, AuthResponsePayload
from app.common.config.parsers import ApiPriorityLevel, parse_positive_int
from app.common.constants import (
    ACCESS_TOKEN_COOKIE,
    API_PRIORITY_KEY,
    SESSION_ID_COOKIE,
)
from app.common.decorators import public
from app.common.sanitization.sanitization_pipe import BadRequestException
from app.common.utils.cookie_utils import (
    get_access_token_cookie_options,
    get_session_id_cookie_options,
)
from app.common.utils.object_utils import read_cookie_field
from app.core.csrf.csrf_token_service import CsrfTokenService, set_csrf_cookie


def _config_lookup(config: Any, key: str, default: Any = None) -> Any:
    if config is None:
        val = os.environ.get(key)
        return default if val is None else val
    if isinstance(config, Mapping):
        val = config.get(key)
        return default if val is None else val
    get_fn = getattr(config, "get", None)
    if callable(get_fn):
        try:
            val = get_fn(key)
            return default if val is None else val
        except TypeError:
            val = get_fn(key, default)
            return default if val is None else val
    return default


def _set_response_cookie(
    response: Response | Any,
    name: str,
    value: str,
    options: Mapping[str, Any],
) -> None:
    set_cookie_fn = getattr(response, "set_cookie", None)
    if callable(set_cookie_fn):
        set_cookie_fn(
            key=name,
            value=value,
            httponly=bool(options.get("httponly", True)),
            secure=bool(options.get("secure", False)),
            samesite=str(options.get("samesite", "strict")),
            path=str(options.get("path", "/")),
            max_age=options.get("max_age"),
        )
        return
    fastify_fn = getattr(response, "setCookie", None)
    if callable(fastify_fn):
        fastify_fn(
            name,
            value,
            {
                "httpOnly": bool(options.get("httponly", True)),
                "secure": bool(options.get("secure", False)),
                "sameSite": str(options.get("samesite", "strict")),
                "path": str(options.get("path", "/")),
                "maxAge": options.get("max_age"),
            },
        )


def _clear_response_cookie(
    response: Response | Any,
    name: str,
    *,
    path: str,
) -> None:
    delete_fn = getattr(response, "delete_cookie", None)
    if callable(delete_fn):
        delete_fn(key=name, path=path)
        return
    clear_fn = getattr(response, "clearCookie", None)
    if callable(clear_fn):
        clear_fn(name, {"path": path})


async def _read_optional_json_body(request: Request) -> dict[str, Any]:
    try:
        raw = await request.body()
        if not raw or not raw.strip():
            return {}
        parsed = await request.json()
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    except Exception:
        return {}


class AuthController:
    """Authentication controller (`/auth/*`) matching NestJS AuthController."""

    def __init__(
        self,
        auth_service: AuthService,
        config_service: Any = None,
        csrf_token_service: CsrfTokenService | None = None,
    ) -> None:
        self.auth_service = auth_service
        self.config_service = config_service
        self.csrf_token_service = csrf_token_service or CsrfTokenService()

    def _node_env(self) -> str:
        raw = _config_lookup(self.config_service, "NODE_ENV", "development")
        return str(raw) if raw is not None else "development"

    def _access_token_ttl(self) -> int:
        raw = _config_lookup(
            self.config_service,
            "ACCESS_TOKEN_TTL",
            DEFAULT_ACCESS_TOKEN_TTL,
        )
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return raw
        return parse_positive_int(
            str(raw) if raw is not None else None,
            DEFAULT_ACCESS_TOKEN_TTL,
        )

    def _session_ttl(self) -> int:
        raw = _config_lookup(
            self.config_service,
            "SESSION_TTL",
            DEFAULT_SESSION_TTL,
        )
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return raw
        return parse_positive_int(
            str(raw) if raw is not None else None,
            DEFAULT_SESSION_TTL,
        )

    @public
    def get_csrf(self, response: Response | Any) -> dict[str, str]:
        csrf_token = self.csrf_token_service.generate_token()
        set_csrf_cookie(response, csrf_token, node_env=self._node_env())
        return {"csrfToken": csrf_token}

    def getCsrf(self, response: Response | Any) -> dict[str, str]:
        return self.get_csrf(response)

    @public
    async def register(
        self,
        register_dto: RegisterDto | Mapping[str, Any],
    ) -> dict[str, str]:
        dto = (
            register_dto
            if isinstance(register_dto, RegisterDto)
            else RegisterDto.model_validate(register_dto)
        )
        await self.auth_service.register(dto)
        return {"message": "User registered successfully"}

    @public
    async def login(
        self,
        login_dto: LoginDto | Mapping[str, Any],
        request: Request | Any,
        response: Response | Any,
    ) -> AuthResponsePayload:
        try:
            dto = (
                login_dto
                if isinstance(login_dto, LoginDto)
                else LoginDto.model_validate(login_dto)
            )
        except ValidationError as exc:
            raise BadRequestException("Validation failed") from exc

        user = await self.auth_service.validate_user(dto.email, dto.password)
        if user is None:
            raise UnauthorizedException("Invalid credentials")

        headers = getattr(request, "headers", {}) or {}
        user_agent = headers.get("user-agent") if isinstance(headers, Mapping) else None
        client = getattr(request, "client", None)
        ip = getattr(request, "ip", None) or (
            getattr(client, "host", None) if client is not None else None
        )

        tokens = await self.auth_service.create_session(
            user_id=str(user["id"]),
            user_agent=str(user_agent) if user_agent is not None else None,
            ip=str(ip) if ip is not None else None,
        )
        return self._apply_session_cookies(response, tokens)

    @public
    async def refresh(
        self,
        request: Request | Any,
        response: Response | Any,
        session_id_body: str | None = None,
    ) -> AuthResponsePayload:
        cookies = getattr(request, "cookies", None)
        incoming_session_id = (
            read_cookie_field(cookies, SESSION_ID_COOKIE) or session_id_body
        )
        tokens = await self.auth_service.refresh_session(incoming_session_id)
        return self._apply_session_cookies(response, tokens)

    def get_me(self, user: AuthenticatedUser) -> dict[str, str]:
        return {"id": user.id, "email": user.email}

    async def logout(
        self,
        request: Request | Any,
        response: Response | Any,
        session_id_body: str | None = None,
    ) -> dict[str, str]:
        cookies = getattr(request, "cookies", None)
        user = getattr(getattr(request, "state", None), "user", None) or getattr(
            request, "user", None
        )
        user_jti: str | None = None
        if isinstance(user, Mapping):
            raw_jti = user.get("jti")
            user_jti = str(raw_jti) if raw_jti is not None else None
        elif user is not None:
            raw_jti = getattr(user, "jti", None)
            user_jti = str(raw_jti) if raw_jti is not None else None

        session_id = (
            read_cookie_field(cookies, SESSION_ID_COOKIE) or session_id_body or user_jti
        )
        await self.auth_service.logout(session_id)

        _clear_response_cookie(response, ACCESS_TOKEN_COOKIE, path="/")
        _clear_response_cookie(response, SESSION_ID_COOKIE, path="/auth/refresh")
        return {"message": "Logged out successfully"}

    def _apply_session_cookies(
        self,
        response: Response | Any,
        tokens: Mapping[str, object],
    ) -> AuthResponsePayload:
        access_token = str(tokens["accessToken"])
        session_id = str(tokens["sessionId"])
        node_env = self._node_env()

        _set_response_cookie(
            response,
            ACCESS_TOKEN_COOKIE,
            access_token,
            get_access_token_cookie_options(
                self._access_token_ttl(),
                node_env=node_env,
            ),
        )
        _set_response_cookie(
            response,
            SESSION_ID_COOKIE,
            session_id,
            get_session_id_cookie_options(
                self._session_ttl(),
                node_env=node_env,
            ),
        )
        csrf_token = self.csrf_token_service.generate_token()
        set_csrf_cookie(response, csrf_token, node_env=node_env)

        return {
            "accessToken": access_token,
            "sessionId": session_id,
            "csrfToken": csrf_token,
        }


setattr(AuthController, API_PRIORITY_KEY, ApiPriorityLevel.CRITICAL)
for _method_name in ("get_csrf", "getCsrf", "register", "login", "refresh", "logout"):
    _fn = getattr(AuthController, _method_name, None)
    if _fn is not None:
        setattr(_fn, API_PRIORITY_KEY, ApiPriorityLevel.CRITICAL)


def create_auth_router(
    controller: AuthController | None = None,
) -> APIRouter:
    """Create the FastAPI APIRouter for `/auth` endpoints."""
    router = APIRouter(tags=["auth"])

    def _resolve_controller(request: Request) -> AuthController:
        if controller is not None:
            return controller
        app_ctrl: AuthController | None = getattr(
            request.app.state,
            "auth_controller",
            None,
        )
        if app_ctrl is not None:
            return app_ctrl
        auth_svc: AuthService | None = getattr(
            request.app.state,
            "auth_service",
            None,
        )
        if auth_svc is None:
            auth_svc = AuthService(
                mongo_service=getattr(request.app.state, "mongo_service", None),
                redis_service=getattr(request.app.state, "redis_service", None),
                config_service=getattr(request.app.state, "env", os.environ),
            )
            request.app.state.auth_service = auth_svc
        fallback = AuthController(
            auth_service=auth_svc,
            config_service=getattr(request.app.state, "env", os.environ),
            csrf_token_service=getattr(
                request.app.state,
                "csrf_token_service",
                None,
            ),
        )
        request.app.state.auth_controller = fallback
        return fallback

    @router.get("/auth/csrf")
    @public
    async def auth_csrf(request: Request, response: Response) -> dict[str, str]:
        ctrl = _resolve_controller(request)
        return ctrl.get_csrf(response)

    @router.post("/auth/register", status_code=201)
    @public
    async def auth_register(
        register_dto: RegisterDto,
        request: Request,
    ) -> dict[str, str]:
        ctrl = _resolve_controller(request)
        return await ctrl.register(register_dto)

    @router.post("/auth/login", status_code=200)
    @public
    async def auth_login(
        request: Request,
        response: Response,
    ) -> AuthResponsePayload:
        body = await _read_optional_json_body(request)
        ctrl = _resolve_controller(request)
        return await ctrl.login(body, request, response)

    @router.post("/auth/refresh", status_code=200)
    @public
    async def auth_refresh(
        request: Request,
        response: Response,
    ) -> AuthResponsePayload:
        body = await _read_optional_json_body(request)
        raw_sid = body.get("sessionId")
        session_id_body = str(raw_sid) if isinstance(raw_sid, str) else None
        ctrl = _resolve_controller(request)
        return await ctrl.refresh(request, response, session_id_body)

    @router.get("/auth/me")
    async def auth_me(
        request: Request,
        user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    ) -> dict[str, str]:
        ctrl = _resolve_controller(request)
        return ctrl.get_me(user)

    @router.post("/auth/logout", status_code=200)
    async def auth_logout(
        request: Request,
        response: Response,
    ) -> dict[str, str]:
        body = await _read_optional_json_body(request)
        raw_sid = body.get("sessionId")
        session_id_body = str(raw_sid) if isinstance(raw_sid, str) else None
        ctrl = _resolve_controller(request)
        return await ctrl.logout(request, response, session_id_body)

    for route_fn in (
        auth_csrf,
        auth_register,
        auth_login,
        auth_refresh,
        auth_me,
        auth_logout,
    ):
        setattr(route_fn, API_PRIORITY_KEY, ApiPriorityLevel.CRITICAL)

    return router


__all__ = [
    "AuthController",
    "JwtAuthGuard",
    "create_auth_router",
]
