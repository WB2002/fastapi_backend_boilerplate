from collections.abc import Mapping
from enum import StrEnum
from typing import Literal, Protocol


class RateLimitStorage(StrEnum):
    MEMORY = "Memory"
    REDIS = "Redis"


class CacheStorage(StrEnum):
    MEMORY = "Memory"
    REDIS = "Redis"


StorageToken = Literal["memory", "redis"]


class ConfigGetter(Protocol):
    def get(self, key: str, default: str | None = None) -> str | None: ...


def _parse_storage_token(raw: str | None, label: str) -> StorageToken | None:
    if raw is None or raw.strip() == "":
        return None
    normalized = raw.strip().lower()
    if normalized == "memory":
        return "memory"
    if normalized == "redis":
        return "redis"
    raise ValueError(f'{label} must be "memory" or "redis" (got "{raw}")')


def _default_storage_for_node_env(node_env: str | None) -> StorageToken:
    return "redis" if node_env == "production" else "memory"


def resolve_rate_limit_storage(
    *,
    node_env: str | None = None,
    rate_limit_storage: str | None = None,
) -> RateLimitStorage:
    storage = _parse_storage_token(rate_limit_storage, "RATE_LIMIT_STORAGE")
    if storage is None:
        storage = _default_storage_for_node_env(node_env)
    return RateLimitStorage.REDIS if storage == "redis" else RateLimitStorage.MEMORY


def resolve_cache_storage(
    *,
    node_env: str | None = None,
    cache_storage: str | None = None,
) -> CacheStorage:
    storage = _parse_storage_token(cache_storage, "CACHE_STORAGE")
    if storage is None:
        storage = _default_storage_for_node_env(node_env)
    return CacheStorage.REDIS if storage == "redis" else CacheStorage.MEMORY


def resolve_rate_limit_storage_from_config(
    config: Mapping[str, str | None] | ConfigGetter,
) -> RateLimitStorage:
    raw_node_env = config.get("NODE_ENV")
    node_env = raw_node_env if raw_node_env is not None else "development"
    raw_storage = config.get("RATE_LIMIT_STORAGE")
    trimmed = raw_storage.strip() if raw_storage is not None else None
    return resolve_rate_limit_storage(
        node_env=node_env,
        rate_limit_storage=trimmed if trimmed else None,
    )


def resolve_cache_storage_from_config(
    config: Mapping[str, str | None] | ConfigGetter,
) -> CacheStorage:
    raw_node_env = config.get("NODE_ENV")
    node_env = raw_node_env if raw_node_env is not None else "development"
    raw_storage = config.get("CACHE_STORAGE")
    trimmed = raw_storage.strip() if raw_storage is not None else None
    return resolve_cache_storage(
        node_env=node_env,
        cache_storage=trimmed if trimmed else None,
    )
