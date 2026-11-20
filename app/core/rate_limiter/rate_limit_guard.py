from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from fastapi import HTTPException, Request, Response
from starlette.routing import Match

from app.common.config.resolve_coordination_storage import RateLimitStorage
from app.common.constants import DEFAULT_GUEST_COOKIE_NAME, SKIP_RATE_LIMIT_KEY
from app.common.country_access.trust_proxy import resolve_client_ip_from_request
from app.common.utils.object_utils import read_cookie_field
from app.core.rate_limiter.stores import (
    ConsumeContext,
    ConsumeResult,
    RateLimitAlgorithm,
    RateLimitBackend,
    RateLimitIdentifier,
    normalize_algorithm,
    normalize_identifier,
)

RATE_LIMIT_KEY: Final[str] = "rateLimit"
RATE_LIMIT_COST_KEY: Final[str] = "rateLimitCost"
LEGACY_RATE_LIMIT_KEY: Final[str] = "rateLimitOptions"
DEFAULT_FINGERPRINT_HEADER: Final[str] = "x-device-fingerprint"

type KeyScope = str
type OnDecisionHook = Callable[[ConsumeContext, ConsumeResult], Awaitable[None] | None]


class MissingGuestIdError(Exception):
    def __init__(self) -> None:
        super().__init__("Guest ID cookie is required for this rate limit policy")


class MissingFingerprintError(Exception):
    def __init__(self) -> None:
        super().__init__(
            "Device fingerprint header is required for this rate limit policy"
        )


@dataclass(frozen=True, slots=True)
class RateLimitMethodConfig:
    identifier: RateLimitIdentifier
    duration: int
    limit: int
    name: str | None = None
    policy: str | None = None
    cost: int | None = None
    burst: int | None = None
    algorithm: RateLimitAlgorithm | None = None
    key_scope: KeyScope = "route"


@dataclass(frozen=True, slots=True)
class RateLimitDecoratorOptions:
    methods: tuple[RateLimitMethodConfig, ...]
    algorithm: RateLimitAlgorithm | None = None


@dataclass(slots=True)
class RateLimiterOptions:
    storage: RateLimitStorage = RateLimitStorage.MEMORY
    algorithm: RateLimitAlgorithm = RateLimitAlgorithm.FIXED_WINDOW_COUNTER
    methods: tuple[RateLimitMethodConfig, ...] = field(default_factory=tuple)
    guest_cookie_name: str = DEFAULT_GUEST_COOKIE_NAME
    fingerprint_header: str = DEFAULT_FINGERPRINT_HEADER
    hash_route_scope: bool | None = None
    on_decision: OnDecisionHook | None = None


GLOBAL_IP_METHOD: Final[RateLimitMethodConfig] = RateLimitMethodConfig(
    name="global-ip",
    identifier=RateLimitIdentifier.IP,
    duration=60_000,
    limit=10,
    cost=1,
    key_scope="global",
)


def default_rate_limiter_options(
    *,
    storage: RateLimitStorage = RateLimitStorage.MEMORY,
    guest_cookie_name: str = DEFAULT_GUEST_COOKIE_NAME,
) -> RateLimiterOptions:
    return RateLimiterOptions(
        storage=storage,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW_COUNTER,
        methods=(GLOBAL_IP_METHOD,),
        guest_cookie_name=guest_cookie_name,
    )


def _method_from_mapping(raw: Mapping[str, Any]) -> RateLimitMethodConfig:
    algo_raw = raw.get("algorithm")
    burst_raw = raw.get("burst")
    cost_raw = raw.get("cost")
    scope_raw = raw.get("key_scope", raw.get("keyScope", "route"))
    return RateLimitMethodConfig(
        name=raw.get("name"),
        policy=raw.get("policy"),
        identifier=normalize_identifier(raw.get("identifier")),
        duration=int(raw["duration"]),
        limit=int(raw["limit"]),
        cost=int(cost_raw) if cost_raw is not None else None,
        burst=int(burst_raw) if burst_raw is not None else None,
        algorithm=normalize_algorithm(algo_raw) if algo_raw is not None else None,
        key_scope="global" if scope_raw == "global" else "route",
    )


def _options_from_mapping(raw: Mapping[str, Any]) -> RateLimitDecoratorOptions:
    algo_raw = raw.get("algorithm")
    methods = tuple(_method_from_mapping(m) for m in raw.get("methods", ()))
    return RateLimitDecoratorOptions(
        methods=methods,
        algorithm=normalize_algorithm(algo_raw) if algo_raw is not None else None,
    )


def rate_limit[F: Callable[..., Any]](
    *methods: RateLimitMethodConfig | Mapping[str, Any],
    algorithm: RateLimitAlgorithm | None = None,
) -> Callable[[F], F]:
    """Attach route-level rate limit policies (replaces the global defaults)."""
    resolved = tuple(
        m if isinstance(m, RateLimitMethodConfig) else _method_from_mapping(m)
        for m in methods
    )

    def _decorator(target: F) -> F:
        setattr(
            target,
            RATE_LIMIT_KEY,
            RateLimitDecoratorOptions(methods=resolved, algorithm=algorithm),
        )
        return target

    return _decorator


def rate_limit_cost[F: Callable[..., Any]](cost: int) -> Callable[[F], F]:
    def _decorator(target: F) -> F:
        setattr(target, RATE_LIMIT_COST_KEY, cost)
        return target

    return _decorator


def hash_route_scope(route_scope: str) -> str:
    return hashlib.sha256(route_scope.encode("utf-8")).hexdigest()[:16]


def build_rate_limit_key(
    *,
    policy: str | None,
    identifier: RateLimitIdentifier,
    tracker: str,
    route_scope: str,
    storage: RateLimitStorage,
    hash_scope: bool | None = None,
) -> str:
    should_hash = (
        hash_scope if hash_scope is not None else (storage == RateLimitStorage.REDIS)
    )
    scope = hash_route_scope(route_scope) if should_hash else route_scope
    return f"rl:{policy or 'default'}:{identifier.value}:{tracker}:{scope}"


def _matched_route(request: Request) -> tuple[Any | None, Any | None]:
    scope = request.scope
    endpoint = scope.get("endpoint")
    route = scope.get("route")
    if endpoint is not None:
        return route, endpoint
    router = getattr(scope.get("app"), "router", None)
    for candidate in getattr(router, "routes", ()) or ():
        try:
            match, child = candidate.matches(scope)
        except Exception:  # noqa: S112
            continue
        if match == Match.FULL:
            return candidate, child.get("endpoint") or getattr(
                candidate, "endpoint", None
            )
    return None, None


def resolve_route_scope(request: Request, key_scope: KeyScope = "route") -> str:
    if key_scope == "global":
        return "app"
    route, _ = _matched_route(request)
    path = getattr(route, "path", None)
    if not isinstance(path, str) or not path:
        return "app:unmatched"
    trimmed = path
    while len(trimmed) > 1 and trimmed.endswith("/"):
        trimmed = trimmed[:-1]
    if not trimmed.startswith("/"):
        trimmed = f"/{trimmed}"
    return f"{request.method.upper()}:{trimmed}"


def resolve_tracker(
    request: Request,
    identifier: RateLimitIdentifier,
    options: RateLimiterOptions,
) -> str | None:
    if identifier == RateLimitIdentifier.IP:
        resolved = resolve_client_ip_from_request(request)
        return resolved or "unknown"
    if identifier == RateLimitIdentifier.USER_ID:
        user = getattr(request.state, "user", None)
        raw = user.get("id") if isinstance(user, Mapping) else getattr(user, "id", None)
        return str(raw) if raw else None
    if identifier == RateLimitIdentifier.GUEST_ID:
        guest = read_cookie_field(request.cookies, options.guest_cookie_name)
        if not guest:
            raise MissingGuestIdError
        return str(guest)
    fingerprint = request.headers.get(options.fingerprint_header)
    if not fingerprint:
        raise MissingFingerprintError
    return fingerprint


class RateLimitGuard:
    """Global rate limit dependency enforcing route and global policies."""

    def __init__(
        self,
        backend: RateLimitBackend,
        options: RateLimiterOptions | None = None,
        *,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.backend = backend
        self.options = options or default_rate_limiter_options()
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    def _resolve_options(
        self, request: Request
    ) -> tuple[RateLimitDecoratorOptions | None, Any | None]:
        route, endpoint = _matched_route(request)
        for target in (endpoint, route):
            meta = getattr(target, RATE_LIMIT_KEY, None)
            if isinstance(meta, RateLimitDecoratorOptions) and meta.methods:
                return meta, endpoint
            legacy = getattr(target, LEGACY_RATE_LIMIT_KEY, None)
            if isinstance(legacy, Mapping):
                parsed = _options_from_mapping(legacy)
                if parsed.methods:
                    return parsed, endpoint
        return None, endpoint

    async def __call__(self, request: Request, response: Response) -> bool:
        meta, endpoint = self._resolve_options(request)
        route, _ = _matched_route(request)
        if any(getattr(t, SKIP_RATE_LIMIT_KEY, False) for t in (endpoint, route)):
            return True
        methods = meta.methods if meta is not None else self.options.methods
        if not methods:
            return True
        decorator_algorithm = meta.algorithm if meta is not None else None
        handler_cost = getattr(endpoint, RATE_LIMIT_COST_KEY, None)
        now_ms = self._now_ms()
        for method in methods:
            await self._enforce(
                request, response, method, handler_cost, decorator_algorithm, now_ms
            )
        return True

    async def _enforce(
        self,
        request: Request,
        response: Response,
        method: RateLimitMethodConfig,
        handler_cost: int | None,
        decorator_algorithm: RateLimitAlgorithm | None,
        now_ms: int,
    ) -> None:
        try:
            tracker = resolve_tracker(request, method.identifier, self.options)
        except (MissingGuestIdError, MissingFingerprintError) as err:
            raise HTTPException(
                status_code=400,
                detail={"statusCode": 400, "message": str(err)},
            ) from err
        if tracker is None:
            return

        policy = method.policy or method.name or "default"
        algorithm = method.algorithm or decorator_algorithm or self.options.algorithm
        cost = handler_cost if handler_cost is not None else (method.cost or 1)
        ctx = ConsumeContext(
            algorithm=algorithm,
            key=build_rate_limit_key(
                policy=policy,
                identifier=method.identifier,
                tracker=tracker,
                route_scope=resolve_route_scope(request, method.key_scope),
                storage=self.options.storage,
                hash_scope=self.options.hash_route_scope,
            ),
            limit=method.limit,
            duration_ms=method.duration,
            cost=cost,
            now_ms=now_ms,
            burst=method.burst,
            policy=policy,
        )
        result = await self.backend.consume(ctx)
        if self.options.on_decision is not None:
            hook = self.options.on_decision(ctx, result)
            if hook is not None:
                await hook
        headers = {
            "X-RateLimit-Limit": str(method.limit),
            "X-RateLimit-Remaining": str(max(0, result.remaining)),
            "X-RateLimit-Reset": str(math.ceil((now_ms + result.reset_ms) / 1000)),
        }
        for key, value in headers.items():
            response.headers[key] = value
        if not result.allowed:
            retry_after = max(1, math.ceil(result.retry_after_ms / 1000))
            raise HTTPException(
                status_code=429,
                detail={
                    "statusCode": 429,
                    "message": "Too Many Requests",
                    "retryAfterMs": result.retry_after_ms,
                    "policy": policy,
                },
                headers={**headers, "Retry-After": str(retry_after)},
            )


__all__ = [
    "GLOBAL_IP_METHOD",
    "MissingFingerprintError",
    "MissingGuestIdError",
    "OnDecisionHook",
    "RateLimitDecoratorOptions",
    "RateLimitGuard",
    "RateLimitMethodConfig",
    "RateLimiterOptions",
    "build_rate_limit_key",
    "default_rate_limiter_options",
    "hash_route_scope",
    "rate_limit",
    "rate_limit_cost",
    "resolve_route_scope",
    "resolve_tracker",
]
