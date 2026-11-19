from collections.abc import Iterator

import pytest

from app.common.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def valid_base_env() -> dict[str, str]:
    return {
        "JWT_PRIVATE_KEY": "test-private-key",
        "JWT_PUBLIC_KEY": "test-public-key",
        "COOKIE_SECRET": "test-cookie-secret",
        "ACCESS_TOKEN_TTL": "900",
        "SESSION_TTL": "604800",
        "MONGO_URI": "mongodb://localhost:27017/test",
        "REDIS_HOST": "localhost",
        "REDIS_PORT": "6379",
        "GEOIP_DATABASE_PATH": "/tmp/GeoLite2-Country.mmdb",
        "HCAPTCHA_SECRET": "test-hcaptcha-secret",
    }
