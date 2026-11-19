import pytest

from app.common.config import (
    CacheStorage,
    RateLimitStorage,
    resolve_cache_storage,
    resolve_cache_storage_from_config,
    resolve_rate_limit_storage,
    resolve_rate_limit_storage_from_config,
)


def test_resolve_rate_limit_storage_defaults_and_overrides() -> None:
    assert resolve_rate_limit_storage(node_env="development") == RateLimitStorage.MEMORY
    assert resolve_rate_limit_storage(node_env="test") == RateLimitStorage.MEMORY
    assert resolve_rate_limit_storage(node_env="production") == RateLimitStorage.REDIS
    assert (
        resolve_rate_limit_storage(node_env="production", rate_limit_storage="")
        == RateLimitStorage.REDIS
    )
    assert (
        resolve_rate_limit_storage(node_env="production", rate_limit_storage="   ")
        == RateLimitStorage.REDIS
    )
    assert (
        resolve_rate_limit_storage(node_env="development", rate_limit_storage="redis")
        == RateLimitStorage.REDIS
    )
    assert (
        resolve_rate_limit_storage(node_env="production", rate_limit_storage="memory")
        == RateLimitStorage.MEMORY
    )
    assert (
        resolve_rate_limit_storage(
            node_env="development", rate_limit_storage="  REDIS  "
        )
        == RateLimitStorage.REDIS
    )
    assert (
        resolve_rate_limit_storage(node_env="production", rate_limit_storage="Memory")
        == RateLimitStorage.MEMORY
    )


def test_resolve_rate_limit_storage_invalid_errors() -> None:
    with pytest.raises(
        ValueError,
        match=r'^RATE_LIMIT_STORAGE must be "memory" or "redis" \(got "dynamodb"\)$',
    ):
        resolve_rate_limit_storage(
            node_env="development", rate_limit_storage="dynamodb"
        )

    with pytest.raises(
        ValueError,
        match=(
            r'^RATE_LIMIT_STORAGE must be "memory" or "redis" '
            r'\(got "  dynamodb  "\)$'
        ),
    ):
        resolve_rate_limit_storage(
            node_env="development", rate_limit_storage="  dynamodb  "
        )


def test_resolve_cache_storage_defaults_and_overrides() -> None:
    assert resolve_cache_storage(node_env="test") == CacheStorage.MEMORY
    assert resolve_cache_storage(node_env="production") == CacheStorage.REDIS
    assert (
        resolve_cache_storage(node_env="development", cache_storage="redis")
        == CacheStorage.REDIS
    )
    assert (
        resolve_cache_storage(node_env="production", cache_storage="memory")
        == CacheStorage.MEMORY
    )
    assert (
        resolve_cache_storage(node_env="development", cache_storage="  ReDiS ")
        == CacheStorage.REDIS
    )


def test_resolve_cache_storage_invalid_errors() -> None:
    with pytest.raises(
        ValueError,
        match=r'^CACHE_STORAGE must be "memory" or "redis" \(got "memcached"\)$',
    ):
        resolve_cache_storage(node_env="development", cache_storage="memcached")


def test_from_config_helpers_trim_and_default() -> None:
    assert resolve_rate_limit_storage_from_config({}) == RateLimitStorage.MEMORY
    assert (
        resolve_rate_limit_storage_from_config({"NODE_ENV": "production"})
        == RateLimitStorage.REDIS
    )
    assert (
        resolve_rate_limit_storage_from_config({"RATE_LIMIT_STORAGE": "   "})
        == RateLimitStorage.MEMORY
    )
    with pytest.raises(
        ValueError,
        match=r'^RATE_LIMIT_STORAGE must be "memory" or "redis" \(got "dynamodb"\)$',
    ):
        resolve_rate_limit_storage_from_config({"RATE_LIMIT_STORAGE": "  dynamodb  "})

    assert resolve_cache_storage_from_config({}) == CacheStorage.MEMORY
    assert (
        resolve_cache_storage_from_config({"NODE_ENV": "production"})
        == CacheStorage.REDIS
    )
    assert (
        resolve_cache_storage_from_config({"CACHE_STORAGE": " redis "})
        == CacheStorage.REDIS
    )
