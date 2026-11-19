from __future__ import annotations

import datetime
import inspect
import os
import time
import uuid
from collections.abc import Mapping
from typing import Any

import jwt
from fastapi import HTTPException

from app.auth.access_token_authenticator import UnauthorizedException
from app.auth.constants import (
    DEFAULT_ACCESS_TOKEN_TTL,
    DEFAULT_SESSION_TTL,
    REVOKED_JTI_PREFIX,
    UserRole,
)
from app.auth.dto import RegisterDto
from app.auth.password import PasswordHashBulkhead
from app.auth.schemas import Session, SessionRepository, User, UserRepository
from app.auth.types import SessionTokenResult
from app.common.config.parsers import parse_positive_int
from app.common.config.settings import normalize_pem


class ConflictException(HTTPException):
    """NestJS-compatible 409 ConflictException."""

    def __init__(self, message: str = "Conflict") -> None:
        super().__init__(status_code=409, detail=message)
        self.message = message
        self.response = {
            "statusCode": 409,
            "message": message,
            "error": "Conflict",
        }

    def get_status(self) -> int:
        return 409

    def getStatus(self) -> int:
        return 409

    def get_response(self) -> dict[str, Any]:
        return dict(self.response)

    def getResponse(self) -> dict[str, Any]:
        return self.get_response()


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


class AuthService:
    """Authentication & session lifecycle service matching NestJS AuthService."""

    def __init__(
        self,
        *,
        user_repository: UserRepository | Any | None = None,
        session_repository: SessionRepository | Any | None = None,
        mongo_service: Any = None,
        redis_service: Any = None,
        password_hash_bulkhead: PasswordHashBulkhead | None = None,
        config_service: Any = None,
        jwt_service: Any = None,
    ) -> None:
        self.user_repository: UserRepository = (
            user_repository
            if isinstance(user_repository, UserRepository)
            else UserRepository(mongo_service)
        )
        self.session_repository: SessionRepository = (
            session_repository
            if isinstance(session_repository, SessionRepository)
            else SessionRepository(mongo_service)
        )
        self._redis_service = redis_service
        self._config_service = config_service
        self._jwt_service = jwt_service
        self.password_hash_bulkhead = password_hash_bulkhead or PasswordHashBulkhead(
            config_service
        )

    @property
    def access_token_ttl(self) -> int:
        raw = _config_lookup(
            self._config_service,
            "ACCESS_TOKEN_TTL",
            DEFAULT_ACCESS_TOKEN_TTL,
        )
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return raw
        return parse_positive_int(
            str(raw) if raw is not None else None,
            DEFAULT_ACCESS_TOKEN_TTL,
        )

    @property
    def session_ttl(self) -> int:
        raw = _config_lookup(
            self._config_service,
            "SESSION_TTL",
            DEFAULT_SESSION_TTL,
        )
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return raw
        return parse_positive_int(
            str(raw) if raw is not None else None,
            DEFAULT_SESSION_TTL,
        )

    def _get_private_key(self) -> str:
        raw = _config_lookup(self._config_service, "JWT_PRIVATE_KEY", "")
        return normalize_pem(str(raw))

    async def register(
        self,
        register_dto: RegisterDto | Mapping[str, Any],
    ) -> User:
        if isinstance(register_dto, RegisterDto):
            email = register_dto.email
            password = register_dto.password
            first_name = register_dto.firstName
            last_name = register_dto.lastName
        else:
            email = str(register_dto["email"])
            password = str(register_dto["password"])
            first_name = register_dto.get("firstName") or register_dto.get("first_name")
            last_name = register_dto.get("lastName") or register_dto.get("last_name")

        normalized_email = email.strip().lower()
        existing = await self.user_repository.find_by_email(normalized_email)
        if existing is not None:
            raise ConflictException("Email already in use")

        password_hash = await self.password_hash_bulkhead.hash(password)
        role = self.resolve_role_for_email(normalized_email)
        new_user = User(
            email=normalized_email,
            passwordHash=password_hash,
            role=role,
            firstName=first_name,
            lastName=last_name,
        )
        return await self.user_repository.create(new_user)

    async def validate_user(
        self,
        email: str,
        password: str,
    ) -> dict[str, Any] | None:
        normalized_email = email.strip().lower()
        user = await self.user_repository.find_by_email(normalized_email)
        if user is not None and user.isActive:
            is_valid = await self.password_hash_bulkhead.verify(
                user.passwordHash,
                password,
            )
            if is_valid:
                await self.ensure_admin_role_if_allowlisted(user)
                plain = user.to_object()
                plain.pop("passwordHash", None)
                return plain
        return None

    async def validateUser(
        self,
        email: str,
        password: str,
    ) -> dict[str, Any] | None:
        return await self.validate_user(email, password)

    async def create_session(
        self,
        user_id: str,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> SessionTokenResult:
        token = str(uuid.uuid4())
        session_ttl = self.session_ttl
        expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            seconds=session_ttl
        )
        session = Session(
            userId=user_id,
            token=token,
            userAgent=user_agent,
            ip=ip,
            ipAddress=ip,
            expiresAt=expires_at,
        )
        await self.session_repository.create(session)

        user_for_token = await self.user_repository.find_by_id(user_id)
        if user_for_token is None:
            raise UnauthorizedException("User not found")

        role = await self.ensure_admin_role_if_allowlisted(user_for_token)
        access_token = await self.sign_access_token(
            user_id=user_id,
            email=user_for_token.email,
            jti=token,
            role=role,
        )
        return {
            "accessToken": access_token,
            "sessionId": token,
        }

    async def createSession(
        self,
        user_id: str,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> SessionTokenResult:
        return await self.create_session(user_id, user_agent, ip)

    async def refresh_session(
        self,
        incoming_token: str | None,
    ) -> SessionTokenResult:
        if not incoming_token or not isinstance(incoming_token, str):
            raise UnauthorizedException("Invalid or expired session")

        new_token = str(uuid.uuid4())
        session_ttl = self.session_ttl
        new_expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            seconds=session_ttl
        )

        session = await self.session_repository.rotate_session(
            incoming_token,
            new_token=new_token,
            new_expires_at=new_expires_at,
        )
        if session is None:
            raise UnauthorizedException("Invalid or expired session")

        user = await self.user_repository.find_by_id(session.userId)
        if user is None:
            raise UnauthorizedException("User not found")

        role = await self.ensure_admin_role_if_allowlisted(user)
        access_token = await self.sign_access_token(
            user_id=user.id,
            email=user.email,
            jti=new_token,
            role=role,
        )
        return {
            "accessToken": access_token,
            "sessionId": new_token,
        }

    async def refreshSession(
        self,
        incoming_token: str | None,
    ) -> SessionTokenResult:
        return await self.refresh_session(incoming_token)

    async def logout(self, session_id: str | None) -> None:
        if not session_id or not isinstance(session_id, str):
            return
        session = await self.session_repository.delete_by_token(session_id)
        if session is not None:
            await self._revoke_jti_in_redis(session_id)

    async def _revoke_jti_in_redis(self, jti: str) -> None:
        if self._redis_service is None:
            return
        key = f"{REVOKED_JTI_PREFIX}{jti}"
        ttl = self.access_token_ttl

        set_fn = getattr(self._redis_service, "set", None)
        if callable(set_fn):
            res = set_fn(key, "true", ex=ttl)
            if inspect.isawaitable(res):
                await res
            return

        get_client = getattr(self._redis_service, "get_client", None) or getattr(
            self._redis_service, "getClient", None
        )
        if callable(get_client):
            client = get_client()
            setex_fn = getattr(client, "setex", None)
            if callable(setex_fn):
                res = setex_fn(key, ttl, "true")
                if inspect.isawaitable(res):
                    await res

    async def is_revoked(self, jti: str) -> bool:
        if not jti or self._redis_service is None:
            return False
        key = f"{REVOKED_JTI_PREFIX}{jti}"
        get_fn = getattr(self._redis_service, "get", None)
        if callable(get_fn):
            res = get_fn(key)
            if inspect.isawaitable(res):
                res = await res
            return bool(res)

        get_client = getattr(self._redis_service, "get_client", None) or getattr(
            self._redis_service, "getClient", None
        )
        if callable(get_client):
            client = get_client()
            res = client.get(key)
            if inspect.isawaitable(res):
                res = await res
            return bool(res)
        return False

    async def isRevoked(self, jti: str) -> bool:
        return await self.is_revoked(jti)

    async def sign_access_token(
        self,
        user_id: str,
        email: str,
        jti: str,
        role: UserRole | str,
    ) -> str:
        role_val = str(role.value if isinstance(role, UserRole) else role)
        if self._jwt_service is not None:
            sign_fn = getattr(self._jwt_service, "sign_async", None) or getattr(
                self._jwt_service, "signAsync", None
            )
            if callable(sign_fn):
                res = sign_fn(
                    {
                        "sub": user_id,
                        "email": email,
                        "jti": jti,
                        "role": role_val,
                    }
                )
                return str(await res if inspect.isawaitable(res) else res)

        now_sec = int(time.time())
        exp_sec = now_sec + self.access_token_ttl
        payload = {
            "sub": user_id,
            "email": email,
            "jti": jti,
            "role": role_val,
            "iat": now_sec,
            "exp": exp_sec,
        }
        return jwt.encode(payload, self._get_private_key(), algorithm="RS256")

    def resolve_role_for_email(self, email: str) -> UserRole:
        return UserRole.ADMIN if self.is_admin_email(email) else UserRole.USER

    def is_admin_email(self, email: str) -> bool:
        raw = _config_lookup(self._config_service, "FEATURE_FLAG_ADMIN_EMAILS", "")
        if not raw:
            return False
        if isinstance(raw, (list, tuple, set, frozenset)):
            allowlist = [str(item).strip().lower() for item in raw if str(item).strip()]
        else:
            allowlist = [
                part.strip().lower() for part in str(raw).split(",") if part.strip()
            ]
        return email.strip().lower() in allowlist

    async def ensure_admin_role_if_allowlisted(self, user: User) -> UserRole:
        current_role = (
            user.role
            if isinstance(user.role, UserRole)
            else (
                UserRole.ADMIN
                if str(user.role).lower() == UserRole.ADMIN.value
                else UserRole.USER
            )
        )
        if current_role == UserRole.ADMIN:
            return UserRole.ADMIN
        if not self.is_admin_email(user.email):
            return current_role
        user.role = UserRole.ADMIN
        await user.save()
        return UserRole.ADMIN


__all__ = [
    "AuthService",
    "ConflictException",
]
