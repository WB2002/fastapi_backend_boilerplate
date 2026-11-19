from __future__ import annotations

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.access_token_authenticator import (
    AccessTokenAuthenticator,
    UnauthorizedException,
)
from app.auth.auth_service import AuthService, ConflictException
from app.auth.constants import UserRole
from app.auth.dto import RegisterDto
from app.auth.password.password_hash_bulkhead import PasswordHashBulkhead


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex


@pytest.fixture(scope="module")
def rsa_pem() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private, public


def _service(
    rsa_pem: tuple[str, str],
    redis: FakeRedis,
    admin_emails: str = "",
) -> AuthService:
    private, public = rsa_pem
    return AuthService(
        redis_service=redis,
        config_service={
            "JWT_PRIVATE_KEY": private,
            "JWT_PUBLIC_KEY": public,
            "ACCESS_TOKEN_TTL": "900",
            "SESSION_TTL": "3600",
            "FEATURE_FLAG_ADMIN_EMAILS": admin_emails,
        },
        password_hash_bulkhead=PasswordHashBulkhead(max_concurrent=2),
    )


async def test_register_normalizes_email_and_rejects_duplicates(
    rsa_pem: tuple[str, str],
) -> None:
    svc = _service(rsa_pem, FakeRedis())
    user = await svc.register(
        RegisterDto(email="  A@Example.com ", password="password1")
    )
    assert user.email == "a@example.com"
    assert user.passwordHash.startswith("$argon2id$")
    assert user.role == UserRole.USER
    with pytest.raises(ConflictException):
        await svc.register(RegisterDto(email="a@example.com", password="password1"))


async def test_validate_user_checks_password(rsa_pem: tuple[str, str]) -> None:
    svc = _service(rsa_pem, FakeRedis())
    await svc.register(RegisterDto(email="a@example.com", password="password1"))
    ok = await svc.validate_user("A@example.com", "password1")
    assert ok is not None
    assert "passwordHash" not in ok
    assert await svc.validate_user("a@example.com", "nope-nope") is None
    assert await svc.validate_user("missing@example.com", "password1") is None


async def test_admin_allowlist_promotes_user(rsa_pem: tuple[str, str]) -> None:
    svc = _service(rsa_pem, FakeRedis(), admin_emails="boss@example.com")
    user = await svc.register(
        RegisterDto(email="boss@example.com", password="password1")
    )
    assert user.role == UserRole.ADMIN


async def test_session_issue_refresh_logout_and_revocation(
    rsa_pem: tuple[str, str],
) -> None:
    redis = FakeRedis()
    svc = _service(rsa_pem, redis)
    user = await svc.register(RegisterDto(email="a@example.com", password="password1"))
    tokens = await svc.create_session(user.id, "agent", "1.2.3.4")

    claims = jwt.decode(tokens["accessToken"], rsa_pem[1], algorithms=["RS256"])
    assert claims["sub"] == user.id
    assert claims["jti"] == tokens["sessionId"]
    assert claims["exp"] - claims["iat"] == 900

    refreshed = await svc.refresh_session(tokens["sessionId"])
    assert refreshed["sessionId"] != tokens["sessionId"]
    with pytest.raises(UnauthorizedException):
        await svc.refresh_session(tokens["sessionId"])

    await svc.logout(refreshed["sessionId"])
    key = f"revoked_jti:{refreshed['sessionId']}"
    assert redis.store[key] == "true"
    assert redis.ttls[key] == 900
    assert await svc.is_revoked(refreshed["sessionId"]) is True

    authenticator = AccessTokenAuthenticator(
        auth_service=svc, config_service={"JWT_PUBLIC_KEY": rsa_pem[1]}
    )
    with pytest.raises(UnauthorizedException):
        await authenticator.verify_access_token(refreshed["accessToken"])


async def test_refresh_rejects_missing_token(rsa_pem: tuple[str, str]) -> None:
    svc = _service(rsa_pem, FakeRedis())
    with pytest.raises(UnauthorizedException):
        await svc.refresh_session(None)
