"""Environment schema, RSA keypair, and cookie-signature helpers for E2E tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

DEFAULT_COOKIE_SECRET = "e2e-test-cookie-secret-32-bytes!!"
DEFAULT_HCAPTCHA_SITE_KEY = "10000000-ffff-ffff-ffff-000000000001"
DEFAULT_HCAPTCHA_SECRET = "0x0000000000000000000000000000000000000000"

ENV_REQUIRED_KEYS: tuple[str, ...] = (
    "JWT_PRIVATE_KEY",
    "JWT_PUBLIC_KEY",
    "COOKIE_SECRET",
    "ACCESS_TOKEN_TTL",
    "SESSION_TTL",
    "MONGO_URI",
    "REDIS_HOST",
    "REDIS_PORT",
    "GEOIP_DATABASE_PATH",
    "HCAPTCHA_SECRET",
)

# Exact declaration order from .reference_nestjs/src/common/config/env.schema.ts
ENV_103_KEYS: tuple[str, ...] = (
    "JWT_PRIVATE_KEY",
    "JWT_PUBLIC_KEY",
    "COOKIE_SECRET",
    "ACCESS_TOKEN_TTL",
    "SESSION_TTL",
    "AUTH_PASSWORD_HASH_MAX_CONCURRENT",
    "AUTH_PASSWORD_HASH_MAX_QUEUE",
    "AUTH_PASSWORD_HASH_MAX_WAIT_MS",
    "CSRF_ENABLED",
    "CSRF_EXEMPT_PATHS",
    "MONGO_URI",
    "MONGO_PORT",
    "REDIS_HOST",
    "REDIS_PORT",
    "PORT",
    "NODE_ENV",
    "ALLOWED_ORIGINS",
    "HTTP_KEEP_ALIVE_TIMEOUT_MS",
    "HEALTH_CHECK_CACHE_TTL_MS",
    "COMPRESSION_THRESHOLD",
    "REQUEST_TIMEOUT_MS",
    "REQUEST_TIMEOUT_MAX_MS",
    "UNDER_PRESSURE_ENABLED",
    "UNDER_PRESSURE_MAX_EVENT_LOOP_DELAY_MS",
    "UNDER_PRESSURE_MAX_HEAP_USED_BYTES",
    "UNDER_PRESSURE_MAX_RSS_BYTES",
    "UNDER_PRESSURE_EXPOSE_STATUS_ROUTE",
    "OVERLOAD_RETRY_AFTER_SEC",
    "INBOUND_BULKHEAD_ENABLED",
    "INBOUND_MAX_IN_FLIGHT",
    "INBOUND_PRIORITY_SHEDDING_ENABLED",
    "INBOUND_DEFAULT_API_PRIORITY",
    "INBOUND_PRIORITY_SHED_CRITICAL_AT",
    "INBOUND_PRIORITY_SHED_HIGH_AT",
    "INBOUND_PRIORITY_SHED_NORMAL_AT",
    "INBOUND_PRIORITY_SHED_LOW_AT",
    "INBOUND_LOAD_LEVELING_ENABLED",
    "INBOUND_LOAD_LEVEL_MAX_QUEUE",
    "INBOUND_LOAD_LEVEL_MAX_WAIT_MS",
    "INBOUND_LOAD_LEVEL_POLL_MS",
    "SHUTDOWN_DRAIN_MS",
    "SHUTDOWN_FORCE_MS",
    "TRUST_PROXY",
    "RATE_LIMIT_STORAGE",
    "RATE_LIMIT_FALLBACK_COOLDOWN_MS",
    "CACHE_STORAGE",
    "CACHE_TTL_JITTER_RATIO",
    "CACHE_LOADER_LOCK_TTL_MS",
    "CACHE_LOADER_WAIT_MS",
    "CACHE_NEGATIVE_SOFT_TTL_MS",
    "CACHE_NEGATIVE_HARD_TTL_MS",
    "GEOIP_DATABASE_PATH",
    "COUNTRY_ACCESS_REFRESH_INTERVAL_MS",
    "COUNTRY_ACCESS_UNKNOWN_COUNTRY_POLICY",
    "COUNTRY_ACCESS_BYPASS_PATHS",
    "UPLOAD_MAX_FILES",
    "UPLOAD_MAX_FILE_SIZE",
    "UPLOAD_MAX_TOTAL_SIZE",
    "UPLOAD_VIRUS_SCAN",
    "CLAMAV_HOST",
    "CLAMAV_PORT",
    "POSTGRES_HOST",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "POSTGRES_PORT",
    "HTTP_CLIENT_ALLOW_PRIVATE_URLS",
    "HTTP_CLIENT_INSTANCE_COUNT",
    "HCAPTCHA_SITE_KEY",
    "HCAPTCHA_SECRET",
    "HCAPTCHA_VERIFY_BASE_URL",
    "SLACK_WEBHOOK_URL",
    "GQL_MAX_COMPLEXITY",
    "GQL_USER_BUDGET",
    "GQL_MAX_CONCURRENCY",
    "GQL_CONCURRENCY_KEY_TTL_SEC",
    "WEBSOCKET_ENABLED",
    "WEBSOCKET_PATH",
    "WEBSOCKET_ALLOW_QUERY_TOKEN",
    "WEBSOCKET_REDIS_ADAPTER",
    "WEBSOCKET_MAX_CONNECTIONS_PER_USER",
    "WEBSOCKET_MAX_INBOUND_BUFFER_BYTES",
    "WEBSOCKET_MESSAGES_PER_MINUTE",
    "WEBSOCKET_CONNECTION_KEY_TTL_SEC",
    "FEATURE_FLAG_ENV",
    "FEATURE_FLAG_RECONCILE_MS",
    "FEATURE_FLAG_ADMIN_EMAILS",
    "FEATURE_FLAG_METRICS",
    "LOG_REDACTION_ENABLED",
    "OTEL_ENABLED",
    "OTEL_SERVICE_NAME",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "APP_METRICS_ENABLED",
    "BACKGROUND_JOBS_ENABLED",
    "BACKGROUND_WORKER_ENABLED",
    "BACKGROUND_JOBS_DEFAULT_QUEUE",
    "BACKGROUND_JOBS_CLAIM_LEASE_SEC",
    "BACKGROUND_JOBS_WORKER_CONCURRENCY",
    "BACKGROUND_JOBS_LEASE_RECLAIM_ENABLED",
    "BACKGROUND_JOBS_LEASE_RECLAIM_INTERVAL_MS",
    "BACKGROUND_JOBS_LEASE_RECLAIM_BATCH_SIZE",
    "BACKGROUND_JOBS_ORPHAN_MIN_AGE_SEC",
    "CONFORMANCE_HARNESS_ENABLED",
)

if len(ENV_103_KEYS) != 103:
    msg = f"Expected 103 env keys, got {len(ENV_103_KEYS)}"
    raise RuntimeError(msg)


@dataclass(frozen=True, slots=True)
class RsaKeyPair:
    """RS256 PEM keypair for JWT signing and verification."""

    private_key_pem: str
    public_key_pem: str

    def as_tuple(self) -> tuple[str, str]:
        """Return ``(private_key_pem, public_key_pem)``."""
        return (self.private_key_pem, self.public_key_pem)

    def __iter__(self) -> Iterator[str]:
        yield self.private_key_pem
        yield self.public_key_pem


def generate_rsa_keypair(key_size: int = 2048) -> RsaKeyPair:
    """Generate a 2048-bit (or ``key_size``-bit) RSA PKCS#8/SPKI PEM keypair."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
        )
        priv_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        pub_pem = (
            private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("utf-8")
        )
        return RsaKeyPair(private_key_pem=priv_pem, public_key_pem=pub_pem)
    except ImportError:
        pass

    openssl_bin = shutil.which("openssl")
    if openssl_bin is None:
        msg = "Neither 'cryptography' nor 'openssl' CLI is available for RSA keygen"
        raise RuntimeError(msg)

    priv_proc = subprocess.run(  # noqa: S603
        [
            openssl_bin,
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            f"rsa_keygen_bits:{key_size}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    priv_pem = priv_proc.stdout
    pub_proc = subprocess.run(  # noqa: S603
        [openssl_bin, "pkey", "-pubout"],
        input=priv_pem,
        capture_output=True,
        text=True,
        check=True,
    )
    return RsaKeyPair(private_key_pem=priv_pem, public_key_pem=pub_proc.stdout)


def sign_cookie(value: str, secret: str) -> str:
    """Sign a cookie value using ``@fastify/cookie`` ``cookie-signature`` format."""
    if not isinstance(value, str):
        msg = "Cookie value must be a str"
        raise TypeError(msg)
    if not secret:
        msg = "Cookie secret must be a non-empty str"
        raise ValueError(msg)
    digest = hmac.new(
        secret.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"{value}.{signature}"


def unsign_cookie(
    signed_value: str,
    secret: str,
    *,
    url_decode: bool = True,
) -> str | None:
    """Verify and unwrap a ``cookie-signature`` signed cookie value."""
    if not secret or not signed_value:
        return None
    candidate = signed_value.strip()
    if len(candidate) >= 2 and candidate[0] == '"' and candidate[-1] == '"':
        candidate = candidate[1:-1]
    if url_decode:
        candidate = unquote(candidate)
    if "." not in candidate:
        return None
    value, _ = candidate.rsplit(".", 1)
    expected = sign_cookie(value, secret)
    if hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8")):
        return value
    return None


def _extract_service_values(services: Any | None) -> dict[str, str]:
    if services is None:
        return {
            "MONGO_URI": "mongodb://127.0.0.1:27017/e2e_test?directConnection=true",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": "6379",
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": "5432",
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": "postgres",
            "POSTGRES_DB": "e2e_test",
        }
    pg = getattr(services, "postgres", None)
    pg_host = getattr(services, "postgres_host", getattr(pg, "host", "127.0.0.1"))
    pg_port = getattr(services, "postgres_port", getattr(pg, "port", 5432))
    pg_user = getattr(services, "postgres_user", getattr(pg, "user", "postgres"))
    pg_pass = getattr(
        services,
        "postgres_password",
        getattr(pg, "password", "postgres"),
    )
    pg_db = getattr(services, "postgres_db", getattr(pg, "database", "e2e_test"))
    mongo_uri = getattr(
        services,
        "mongo_uri",
        "mongodb://127.0.0.1:27017/e2e_test?directConnection=true",
    )
    redis_host = getattr(services, "redis_host", "127.0.0.1")
    redis_port = getattr(services, "redis_port", 6379)
    return {
        "MONGO_URI": str(mongo_uri),
        "REDIS_HOST": str(redis_host),
        "REDIS_PORT": str(redis_port),
        "POSTGRES_HOST": str(pg_host),
        "POSTGRES_PORT": str(pg_port),
        "POSTGRES_USER": str(pg_user),
        "POSTGRES_PASSWORD": str(pg_pass),
        "POSTGRES_DB": str(pg_db),
    }


def build_base_env(
    overrides: Mapping[str, str | None] | None = None,
    *,
    services: Any | None = None,
    keypair: RsaKeyPair | None = None,
    geoip_db_path: str | Path | None = None,
    port: int | str = 0,
    node_env: str = "test",
    conformance_enabled: bool = False,
) -> dict[str, str]:
    """Build a valid 103-variable test environment dictionary.

    Keys mapped to ``None`` in ``overrides`` are removed from the returned dict
    (useful for negative ``validate_env`` startup failure tests).
    """
    override_map = dict(overrides) if overrides is not None else {}
    resolved_keypair = keypair
    if resolved_keypair is None and (
        not isinstance(override_map.get("JWT_PRIVATE_KEY"), str)
        or not isinstance(override_map.get("JWT_PUBLIC_KEY"), str)
    ):
        resolved_keypair = generate_rsa_keypair()

    priv_pem = resolved_keypair.private_key_pem if resolved_keypair is not None else ""
    pub_pem = resolved_keypair.public_key_pem if resolved_keypair is not None else ""
    svc = _extract_service_values(services)
    geoip_str = (
        str(geoip_db_path)
        if geoip_db_path is not None
        else "/tmp/e2e-geolite2-country.mmdb"
    )

    env: dict[str, str] = {
        "JWT_PRIVATE_KEY": priv_pem,
        "JWT_PUBLIC_KEY": pub_pem,
        "COOKIE_SECRET": DEFAULT_COOKIE_SECRET,
        "ACCESS_TOKEN_TTL": "900",
        "SESSION_TTL": "604800",
        "AUTH_PASSWORD_HASH_MAX_CONCURRENT": "2",
        "AUTH_PASSWORD_HASH_MAX_QUEUE": "32",
        "AUTH_PASSWORD_HASH_MAX_WAIT_MS": "2000",
        "CSRF_ENABLED": "true",
        "CSRF_EXEMPT_PATHS": "",
        "MONGO_URI": svc["MONGO_URI"],
        "MONGO_PORT": "27017",
        "REDIS_HOST": svc["REDIS_HOST"],
        "REDIS_PORT": svc["REDIS_PORT"],
        "PORT": str(port),
        "NODE_ENV": node_env,
        "ALLOWED_ORIGINS": "http://127.0.0.1:3000",
        "HTTP_KEEP_ALIVE_TIMEOUT_MS": "65000",
        "HEALTH_CHECK_CACHE_TTL_MS": "2000",
        "COMPRESSION_THRESHOLD": "1024",
        "REQUEST_TIMEOUT_MS": "30000",
        "REQUEST_TIMEOUT_MAX_MS": "120000",
        "UNDER_PRESSURE_ENABLED": "false",
        "UNDER_PRESSURE_MAX_EVENT_LOOP_DELAY_MS": "1000",
        "UNDER_PRESSURE_MAX_HEAP_USED_BYTES": "0",
        "UNDER_PRESSURE_MAX_RSS_BYTES": "0",
        "UNDER_PRESSURE_EXPOSE_STATUS_ROUTE": "false",
        "OVERLOAD_RETRY_AFTER_SEC": "5",
        "INBOUND_BULKHEAD_ENABLED": "false",
        "INBOUND_MAX_IN_FLIGHT": "100",
        "INBOUND_PRIORITY_SHEDDING_ENABLED": "false",
        "INBOUND_DEFAULT_API_PRIORITY": "normal",
        "INBOUND_PRIORITY_SHED_CRITICAL_AT": "1",
        "INBOUND_PRIORITY_SHED_HIGH_AT": "0.95",
        "INBOUND_PRIORITY_SHED_NORMAL_AT": "0.85",
        "INBOUND_PRIORITY_SHED_LOW_AT": "0.7",
        "INBOUND_LOAD_LEVELING_ENABLED": "false",
        "INBOUND_LOAD_LEVEL_MAX_QUEUE": "100",
        "INBOUND_LOAD_LEVEL_MAX_WAIT_MS": "2000",
        "INBOUND_LOAD_LEVEL_POLL_MS": "25",
        "SHUTDOWN_DRAIN_MS": "100",
        "SHUTDOWN_FORCE_MS": "1000",
        "TRUST_PROXY": "false",
        "RATE_LIMIT_STORAGE": "memory",
        "RATE_LIMIT_FALLBACK_COOLDOWN_MS": "5000",
        "CACHE_STORAGE": "memory",
        "CACHE_TTL_JITTER_RATIO": "0.15",
        "CACHE_LOADER_LOCK_TTL_MS": "30000",
        "CACHE_LOADER_WAIT_MS": "5000",
        "CACHE_NEGATIVE_SOFT_TTL_MS": "60000",
        "CACHE_NEGATIVE_HARD_TTL_MS": "300000",
        "GEOIP_DATABASE_PATH": geoip_str,
        "COUNTRY_ACCESS_REFRESH_INTERVAL_MS": "60000",
        "COUNTRY_ACCESS_UNKNOWN_COUNTRY_POLICY": "allow",
        "COUNTRY_ACCESS_BYPASS_PATHS": "",
        "UPLOAD_MAX_FILES": "5",
        "UPLOAD_MAX_FILE_SIZE": "5000000",
        "UPLOAD_MAX_TOTAL_SIZE": "20000000",
        "UPLOAD_VIRUS_SCAN": "false",
        "CLAMAV_HOST": "127.0.0.1",
        "CLAMAV_PORT": "3310",
        "POSTGRES_HOST": svc["POSTGRES_HOST"],
        "POSTGRES_USER": svc["POSTGRES_USER"],
        "POSTGRES_PASSWORD": svc["POSTGRES_PASSWORD"],
        "POSTGRES_DB": svc["POSTGRES_DB"],
        "POSTGRES_PORT": svc["POSTGRES_PORT"],
        "HTTP_CLIENT_ALLOW_PRIVATE_URLS": "false",
        "HTTP_CLIENT_INSTANCE_COUNT": "1",
        "HCAPTCHA_SITE_KEY": DEFAULT_HCAPTCHA_SITE_KEY,
        "HCAPTCHA_SECRET": DEFAULT_HCAPTCHA_SECRET,
        "HCAPTCHA_VERIFY_BASE_URL": "https://api.hcaptcha.com",
        "SLACK_WEBHOOK_URL": "",
        "GQL_MAX_COMPLEXITY": "50",
        "GQL_USER_BUDGET": "1000",
        "GQL_MAX_CONCURRENCY": "3",
        "GQL_CONCURRENCY_KEY_TTL_SEC": "300",
        "WEBSOCKET_ENABLED": "true",
        "WEBSOCKET_PATH": "/socket.io",
        "WEBSOCKET_ALLOW_QUERY_TOKEN": "false",
        "WEBSOCKET_REDIS_ADAPTER": "false",
        "WEBSOCKET_MAX_CONNECTIONS_PER_USER": "5",
        "WEBSOCKET_MAX_INBOUND_BUFFER_BYTES": "1000000",
        "WEBSOCKET_MESSAGES_PER_MINUTE": "120",
        "WEBSOCKET_CONNECTION_KEY_TTL_SEC": "86400",
        "FEATURE_FLAG_ENV": node_env,
        "FEATURE_FLAG_RECONCILE_MS": "45000",
        "FEATURE_FLAG_ADMIN_EMAILS": "",
        "FEATURE_FLAG_METRICS": "true",
        "LOG_REDACTION_ENABLED": "true",
        "OTEL_ENABLED": "false",
        "OTEL_SERVICE_NAME": "fastapi-boilerplate-e2e",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "",
        "APP_METRICS_ENABLED": "true",
        "BACKGROUND_JOBS_ENABLED": "false",
        "BACKGROUND_WORKER_ENABLED": "false",
        "BACKGROUND_JOBS_DEFAULT_QUEUE": "default",
        "BACKGROUND_JOBS_CLAIM_LEASE_SEC": "300",
        "BACKGROUND_JOBS_WORKER_CONCURRENCY": "5",
        "BACKGROUND_JOBS_LEASE_RECLAIM_ENABLED": "true",
        "BACKGROUND_JOBS_LEASE_RECLAIM_INTERVAL_MS": "30000",
        "BACKGROUND_JOBS_LEASE_RECLAIM_BATCH_SIZE": "100",
        "BACKGROUND_JOBS_ORPHAN_MIN_AGE_SEC": "30",
        "CONFORMANCE_HARNESS_ENABLED": "true" if conformance_enabled else "false",
    }

    for key, val in override_map.items():
        if val is None:
            env.pop(key, None)
        else:
            env[key] = str(val)

    return env


__all__ = [
    "DEFAULT_COOKIE_SECRET",
    "DEFAULT_HCAPTCHA_SECRET",
    "DEFAULT_HCAPTCHA_SITE_KEY",
    "ENV_103_KEYS",
    "ENV_REQUIRED_KEYS",
    "RsaKeyPair",
    "build_base_env",
    "generate_rsa_keypair",
    "sign_cookie",
    "unsign_cookie",
]
