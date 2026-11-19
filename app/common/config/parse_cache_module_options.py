import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from app.common.config.parsers import parse_js_number, trim_js


class ConfigGetter(Protocol):
    def get(self, key: str, default: str | None = None) -> str | None: ...


@dataclass(frozen=True, slots=True)
class CacheModuleOptions:
    ttl_jitter_ratio: float | None = None
    loader_lock_ttl_ms: int | None = None
    loader_wait_ms: int | None = None
    default_negative_soft_ttl_ms: int | None = None
    default_negative_hard_ttl_ms: int | None = None

    def to_dict(self) -> dict[str, float | int]:
        result: dict[str, float | int] = {}
        if self.ttl_jitter_ratio is not None:
            result["ttlJitterRatio"] = self.ttl_jitter_ratio
        if self.loader_lock_ttl_ms is not None:
            result["loaderLockTtlMs"] = self.loader_lock_ttl_ms
        if self.loader_wait_ms is not None:
            result["loaderWaitMs"] = self.loader_wait_ms
        if self.default_negative_soft_ttl_ms is not None:
            result["defaultNegativeSoftTtlMs"] = self.default_negative_soft_ttl_ms
        if self.default_negative_hard_ttl_ms is not None:
            result["defaultNegativeHardTtlMs"] = self.default_negative_hard_ttl_ms
        return result


def _parse_ratio(raw: str | None) -> float | None:
    if raw is None or trim_js(raw) == "":
        return None
    value = parse_js_number(raw)
    if not math.isfinite(value) or value < 0 or value > 1:
        raise ValueError("CACHE_TTL_JITTER_RATIO must be a number between 0 and 1")
    return value


def _parse_positive_int(raw: str | None) -> int | None:
    if raw is None or trim_js(raw) == "":
        return None
    value = parse_js_number(raw)
    if not math.isfinite(value) or not value.is_integer() or value <= 0:
        raise ValueError(f'Invalid cache config integer: "{raw}"')
    return int(value)


def parse_cache_module_options(
    config: Mapping[str, str | None] | ConfigGetter,
) -> CacheModuleOptions:
    return CacheModuleOptions(
        ttl_jitter_ratio=_parse_ratio(config.get("CACHE_TTL_JITTER_RATIO")),
        loader_lock_ttl_ms=_parse_positive_int(config.get("CACHE_LOADER_LOCK_TTL_MS")),
        loader_wait_ms=_parse_positive_int(config.get("CACHE_LOADER_WAIT_MS")),
        default_negative_soft_ttl_ms=_parse_positive_int(
            config.get("CACHE_NEGATIVE_SOFT_TTL_MS")
        ),
        default_negative_hard_ttl_ms=_parse_positive_int(
            config.get("CACHE_NEGATIVE_HARD_TTL_MS")
        ),
    )
