from __future__ import annotations

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.main import create_app


@pytest.fixture(scope="module")
def keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private, public_pem


def _user_token(private: str, *, role: str = "user") -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "u1",
            "email": "user@example.test",
            "jti": "jti-1",
            "role": role,
            "iat": now,
            "exp": now + 600,
        },
        private,
        algorithm="RS256",
    )


@pytest.mark.asyncio
async def test_standard_user_denied_on_feature_flags_list(
    keys: tuple[str, str],
) -> None:
    private, public = keys
    env = {
        "JWT_PRIVATE_KEY": private,
        "JWT_PUBLIC_KEY": public,
        "COOKIE_SECRET": "conformance-cookie-secret-32-chars-min",
        "ACCESS_TOKEN_TTL": "900",
        "SESSION_TTL": "604800",
        "MONGO_URI": "mongodb://127.0.0.1:27017/ff-rbac",
        "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": "6379",
        "GEOIP_DATABASE_PATH": "/tmp/ff-rbac.mmdb",
        "HCAPTCHA_SECRET": "conformance-secret",
        "NODE_ENV": "development",
        "INBOUND_BULKHEAD_ENABLED": "false",
    }
    app = create_app(env=env, validate_environment=False)
    token = _user_token(private, role="user")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get(
            "/feature-flags",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Forwarded-For": "198.51.100.116",
            },
        )
    assert response.status_code == 403
    assert "Insufficient feature-flag permissions" in response.text
