from __future__ import annotations

import inspect
import os
from collections.abc import Mapping
from typing import Any

import jwt
from fastapi import HTTPException

from app.auth.constants import REVOKED_JTI_PREFIX, UserRole
from app.auth.types import AuthenticatedUser
from app.common.config.settings import normalize_pem
from app.types.jwt_types import JwtPayload


class UnauthorizedException(HTTPException):
    """NestJS-compatible 401 UnauthorizedException."""

    def __init__(self, message: str = "Unauthorized") -> None:
        super().__init__(status_code=401, detail=message)
        self.message = message
        self.response = {
            "statusCode": 401,
            "message": message,
            "error": "Unauthorized",
        }

    def get_status(self) -> int:
        return 401

    def getStatus(self) -> int:
        return 401

    def get_response(self) -> dict[str, Any]:
        return dict(self.response)

    def getResponse(self) -> dict[str, Any]:
        return self.get_response()


def _resolve_public_key(config_service: Any, explicit_key: str | None) -> str:
    if explicit_key is not None:
        return normalize_pem(explicit_key)
    raw: Any = None
    if isinstance(config_service, Mapping):
        raw = config_service.get("JWT_PUBLIC_KEY")
    elif config_service is not None:
        get_fn = getattr(config_service, "get", None)
        if callable(get_fn):
            raw = get_fn("JWT_PUBLIC_KEY")
        elif hasattr(config_service, "jwt_public_key"):
            raw = config_service.jwt_public_key
    if raw is None:
        raw = os.environ.get("JWT_PUBLIC_KEY", "")
    return normalize_pem(str(raw))


class AccessTokenAuthenticator:
    """Verifies RS256 access tokens and checks Redis `revoked_jti:<jti>`."""

    def __init__(
        self,
        *,
        jwt_service: Any = None,
        auth_service: Any = None,
        redis_service: Any = None,
        config_service: Any = None,
        jwt_public_key: str | None = None,
    ) -> None:
        self._jwt_service = jwt_service
        self._auth_service = auth_service
        self._redis_service = redis_service
        self._config_service = config_service
        self._jwt_public_key = jwt_public_key

    def _get_public_key(self) -> str:
        return _resolve_public_key(self._config_service, self._jwt_public_key)

    async def _check_is_revoked(self, jti: str) -> bool:
        if self._auth_service is not None:
            fn = getattr(self._auth_service, "is_revoked", None) or getattr(
                self._auth_service, "isRevoked", None
            )
            if callable(fn):
                res = fn(jti)
                if inspect.isawaitable(res):
                    res = await res
                return bool(res)

        if self._redis_service is not None:
            get_fn = getattr(self._redis_service, "get", None)
            if callable(get_fn):
                res = get_fn(f"{REVOKED_JTI_PREFIX}{jti}")
                if inspect.isawaitable(res):
                    res = await res
                return bool(res)
            get_client = getattr(self._redis_service, "get_client", None) or getattr(
                self._redis_service, "getClient", None
            )
            if callable(get_client):
                client = get_client()
                res = client.get(f"{REVOKED_JTI_PREFIX}{jti}")
                if inspect.isawaitable(res):
                    res = await res
                return bool(res)
        return False

    async def verify_access_token(self, raw_token: str) -> AuthenticatedUser:
        token = raw_token.strip() if isinstance(raw_token, str) else ""
        if not token:
            raise UnauthorizedException("Missing access token")

        public_key = self._get_public_key()
        payload: Mapping[str, Any]
        try:
            if self._jwt_service is not None:
                verify_fn = getattr(self._jwt_service, "verify_async", None) or getattr(
                    self._jwt_service, "verifyAsync", None
                )
                if callable(verify_fn):
                    res = verify_fn(
                        token,
                        {"publicKey": public_key, "algorithms": ["RS256"]},
                    )
                    payload = await res if inspect.isawaitable(res) else res
                else:
                    payload = jwt.decode(
                        token,
                        public_key,
                        algorithms=["RS256"],
                    )
            else:
                payload = jwt.decode(
                    token,
                    public_key,
                    algorithms=["RS256"],
                )
        except Exception as exc:
            raise UnauthorizedException("Invalid access token") from exc

        return await self.user_from_verified_payload(payload)

    async def verifyAccessToken(self, raw_token: str) -> AuthenticatedUser:
        return await self.verify_access_token(raw_token)

    async def verify_token(self, raw_token: str) -> AuthenticatedUser:
        return await self.verify_access_token(raw_token)

    async def user_from_verified_payload(
        self,
        payload: JwtPayload | Mapping[str, Any],
    ) -> AuthenticatedUser:
        sub = payload.get("sub")
        email = payload.get("email")
        jti = payload.get("jti")
        if (
            not isinstance(sub, str)
            or not sub.strip()
            or not isinstance(jti, str)
            or not jti.strip()
        ):
            raise UnauthorizedException("Invalid access token")

        if await self._check_is_revoked(jti):
            raise UnauthorizedException("Session has been revoked")

        role_raw = payload.get("role")
        role = str(role_raw) if role_raw is not None else str(UserRole.USER.value)
        return AuthenticatedUser(
            id=sub,
            email=str(email) if email is not None else "",
            jti=jti,
            role=role,
        )

    async def userFromVerifiedPayload(
        self,
        payload: JwtPayload | Mapping[str, Any],
    ) -> AuthenticatedUser:
        return await self.user_from_verified_payload(payload)


AccessTokenAuthenticatorService = AccessTokenAuthenticator

__all__ = [
    "AccessTokenAuthenticator",
    "AccessTokenAuthenticatorService",
    "UnauthorizedException",
]
