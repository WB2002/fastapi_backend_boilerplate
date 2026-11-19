from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from fastapi import Request
from starlette.routing import Match

from app.common.config.parsers import (
    DEFAULT_INBOUND_LOAD_LEVEL_MAX_QUEUE,
    DEFAULT_INBOUND_LOAD_LEVEL_MAX_WAIT_MS,
    DEFAULT_INBOUND_LOAD_LEVEL_POLL_MS,
    DEFAULT_INBOUND_MAX_IN_FLIGHT,
    DEFAULT_PRIORITY_SHEDDING_THRESHOLDS,
    ApiPriorityLevel,
    InboundBulkheadConfig,
    InboundLoadLevelConfig,
    PrioritySheddingConfig,
    PrioritySheddingThresholds,
    parse_inbound_bulkhead_config,
    parse_inbound_load_level_config,
    parse_priority_shedding_config,
)
from app.common.constants import API_PRIORITY_KEY, SKIP_INBOUND_BULKHEAD_KEY
from app.common.decorators import skip_inbound_bulkhead
from app.core.overload.errors import OverloadRejectedError, PriorityShedRejectedError
from app.core.overload.load_leveling_queue import InboundLoadLevelQueue
from app.core.overload.under_pressure import is_pressure_exempt_path
from app.core.request_deadline.deadline_context import get_request_signal, remaining_ms


def api_priority(level: ApiPriorityLevel | str | int) -> Callable[[Any], Any]:
    """Attach API priority metadata to an endpoint function or controller class."""
    resolved = _coerce_priority(level, ApiPriorityLevel.NORMAL)

    def _decorator(target: Any) -> Any:
        setattr(target, API_PRIORITY_KEY, resolved)
        return target

    return _decorator


def _coerce_priority(
    value: ApiPriorityLevel | str | int | None,
    fallback: ApiPriorityLevel = ApiPriorityLevel.NORMAL,
) -> ApiPriorityLevel:
    if isinstance(value, ApiPriorityLevel):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        for member in ApiPriorityLevel:
            if int(member.value) == value:
                return member
        return fallback
    if isinstance(value, str):
        norm = value.strip().lower()
        if norm == "critical":
            return ApiPriorityLevel.CRITICAL
        if norm == "high":
            return ApiPriorityLevel.HIGH
        if norm == "normal":
            return ApiPriorityLevel.NORMAL
        if norm == "low":
            return ApiPriorityLevel.LOW
    return fallback


def compute_inbound_utilization(
    active: int,
    max_in_flight: int,
) -> float:
    """Compute inbound bulkhead utilization in [0, 1+]; returns 1.0 if max <= 0."""
    if max_in_flight <= 0:
        return 1.0
    return active / max_in_flight


def _resolve_threshold_for_priority(
    priority: ApiPriorityLevel,
    thresholds: PrioritySheddingThresholds | Mapping[str, float],
) -> float | None:
    key_map = {
        ApiPriorityLevel.CRITICAL: "critical",
        ApiPriorityLevel.HIGH: "high",
        ApiPriorityLevel.NORMAL: "normal",
        ApiPriorityLevel.LOW: "low",
    }
    key = key_map.get(priority)
    if key is None:
        return None
    if isinstance(thresholds, PrioritySheddingThresholds):
        return float(getattr(thresholds, key))
    if isinstance(thresholds, Mapping) and key in thresholds:
        return float(thresholds[key])
    return None


def should_shed_priority_request(
    options_or_active: Mapping[str, Any] | int,
    max_in_flight: int | None = None,
    priority: ApiPriorityLevel | str | int | None = None,
    thresholds: (
        PrioritySheddingThresholds | Mapping[str, float] | None
    ) = DEFAULT_PRIORITY_SHEDDING_THRESHOLDS,
    *,
    active: int | None = None,
    maxInFlight: int | None = None,
) -> bool:
    """Pure priority shedding predicate matching NestJS `shouldShedPriorityRequest`."""
    if isinstance(options_or_active, Mapping):
        act = int(
            options_or_active["active"]
            if "active" in options_or_active
            else options_or_active.get("in_flight", 0)
        )
        max_if = int(
            options_or_active["max_in_flight"]
            if "max_in_flight" in options_or_active
            else options_or_active.get("maxInFlight", 0)
        )
        prio = _coerce_priority(options_or_active.get("priority"))
        thresh = options_or_active.get(
            "thresholds",
            DEFAULT_PRIORITY_SHEDDING_THRESHOLDS,
        )
    else:
        act = int(active if active is not None else options_or_active)
        max_if = int(
            max_in_flight
            if max_in_flight is not None
            else (maxInFlight if maxInFlight is not None else 0)
        )
        prio = _coerce_priority(priority)
        thresh = (
            thresholds
            if thresholds is not None
            else DEFAULT_PRIORITY_SHEDDING_THRESHOLDS
        )

    if act >= max_if:
        return False
    utilization = compute_inbound_utilization(act, max_if)
    max_allowed = _resolve_threshold_for_priority(prio, thresh)
    if max_allowed is None:
        return False
    return utilization >= max_allowed


def _coerce_bulkhead_config(
    config: InboundBulkheadConfig | Mapping[str, Any] | None,
) -> InboundBulkheadConfig:
    if isinstance(config, InboundBulkheadConfig):
        return config
    if isinstance(config, Mapping):
        enabled = bool(config.get("enabled", False))
        max_in_flight = int(
            config["max_in_flight"]
            if "max_in_flight" in config
            else config.get("maxInFlight", DEFAULT_INBOUND_MAX_IN_FLIGHT)
        )
        return InboundBulkheadConfig(
            enabled=enabled,
            max_in_flight=max(1, max_in_flight),
        )
    return parse_inbound_bulkhead_config()


def _coerce_load_level_config(
    config: InboundLoadLevelConfig | Mapping[str, Any] | None,
) -> InboundLoadLevelConfig:
    if isinstance(config, InboundLoadLevelConfig):
        return config
    if isinstance(config, Mapping):
        enabled = bool(config.get("enabled", False))
        max_queue = int(
            config["max_queue"]
            if "max_queue" in config
            else config.get("maxQueue", DEFAULT_INBOUND_LOAD_LEVEL_MAX_QUEUE)
        )
        max_wait_ms = int(
            config["max_wait_ms"]
            if "max_wait_ms" in config
            else config.get("maxWaitMs", DEFAULT_INBOUND_LOAD_LEVEL_MAX_WAIT_MS)
        )
        poll_ms = int(
            config["poll_interval_ms"]
            if "poll_interval_ms" in config
            else config.get("pollIntervalMs", DEFAULT_INBOUND_LOAD_LEVEL_POLL_MS)
        )
        return InboundLoadLevelConfig(
            enabled=enabled,
            max_queue=max(1, max_queue),
            max_wait_ms=max(1, max_wait_ms),
            poll_interval_ms=max(1, poll_ms),
        )
    return parse_inbound_load_level_config()


def _coerce_shedding_config(
    config: PrioritySheddingConfig | Mapping[str, Any] | None,
) -> PrioritySheddingConfig:
    if isinstance(config, PrioritySheddingConfig):
        return config
    if isinstance(config, Mapping):
        enabled = bool(config.get("enabled", False))
        raw_default = (
            config["default_priority"]
            if "default_priority" in config
            else config.get("defaultPriority", ApiPriorityLevel.NORMAL)
        )
        default_prio = _coerce_priority(raw_default, ApiPriorityLevel.NORMAL)
        raw_thresh = config.get("thresholds", DEFAULT_PRIORITY_SHEDDING_THRESHOLDS)
        if isinstance(raw_thresh, PrioritySheddingThresholds):
            thresh = raw_thresh
        elif isinstance(raw_thresh, Mapping):
            thresh = PrioritySheddingThresholds(
                critical=float(raw_thresh.get("critical", 1.0)),
                high=float(raw_thresh.get("high", 0.95)),
                normal=float(raw_thresh.get("normal", 0.85)),
                low=float(raw_thresh.get("low", 0.7)),
            )
        else:
            thresh = DEFAULT_PRIORITY_SHEDDING_THRESHOLDS
        return PrioritySheddingConfig(
            enabled=enabled,
            default_priority=default_prio,
            thresholds=thresh,
        )
    return parse_priority_shedding_config()


class InboundBulkheadService:
    """Tracks active in-flight requests and coordinates with InboundLoadLevelQueue."""

    def __init__(
        self,
        config: InboundBulkheadConfig | Mapping[str, Any] | None = None,
        load_level_config: InboundLoadLevelConfig | Mapping[str, Any] | None = None,
    ) -> None:
        self.config = _coerce_bulkhead_config(config)
        self.load_level_config = _coerce_load_level_config(load_level_config)
        self._active = 0
        self.queue = InboundLoadLevelQueue(
            self.load_level_config.max_queue,
            self.load_level_config.poll_interval_ms,
        )

    @property
    def active_count(self) -> int:
        return self._active

    @property
    def activeCount(self) -> int:
        return self._active

    @property
    def max_in_flight(self) -> int:
        return self.config.max_in_flight

    @property
    def maxInFlight(self) -> int:
        return self.config.max_in_flight

    def is_enabled(self) -> bool:
        return self.config.enabled

    def isEnabled(self) -> bool:
        return self.config.enabled

    def is_load_leveling_enabled(self) -> bool:
        return self.config.enabled and self.load_level_config.enabled

    def isLoadLevelingEnabled(self) -> bool:
        return self.is_load_leveling_enabled()

    def try_acquire(self) -> bool:
        if not self.config.enabled:
            return True
        if self._active >= self.config.max_in_flight:
            return False
        self._active += 1
        return True

    def tryAcquire(self) -> bool:
        return self.try_acquire()

    async def acquire(
        self,
        options: Mapping[str, Any] | None = None,
        *,
        priority: ApiPriorityLevel | str | int = ApiPriorityLevel.NORMAL,
        max_wait_ms: int | None = None,
        signal: Any = None,
    ) -> None:
        if not self.config.enabled:
            return
        if self.try_acquire():
            return
        if not self.load_level_config.enabled:
            raise OverloadRejectedError()

        if isinstance(options, Mapping):
            prio = options.get("priority", priority)
            wait_ms = (
                options["max_wait_ms"]
                if "max_wait_ms" in options
                else options.get("maxWaitMs", max_wait_ms)
            )
            sig = options.get("signal", signal)
        else:
            prio = priority
            wait_ms = max_wait_ms
            sig = signal

        await self.queue.enqueue(
            priority=prio,
            max_wait_ms=(
                int(wait_ms)
                if wait_ms is not None
                else self.load_level_config.max_wait_ms
            ),
            signal=sig,
        )

    def release(self) -> None:
        if not self.config.enabled:
            return
        if self._active > 0:
            self._active -= 1
        if not self.load_level_config.enabled:
            return
        grant = self.queue.dequeue_next_waiter()
        if grant is not None:
            self._active += 1
            grant()


def _find_route_endpoint(request: Request | Any) -> tuple[Any | None, Any | None]:
    scope = getattr(request, "scope", None)
    if not isinstance(scope, Mapping):
        return None, None
    ep = scope.get("endpoint")
    route = scope["route"]
    if ep is not None:
        return route, ep
    app = scope.get("app")
    router = getattr(app, "router", None)
    routes = getattr(router, "routes", None)
    if not routes:
        return None, None
    for r in routes:
        try:
            match, child_scope = r.matches(scope)
            if match == Match.FULL:
                return r, child_scope.get("endpoint") or getattr(r, "endpoint", None)
        except Exception:  # noqa: S112
            continue
    return None, None


def _extract_request_url(request: Request | Any) -> str:
    if isinstance(request, Request):
        path = request.url.path
        query = request.url.query
        return f"{path}?{query}" if query else path
    if isinstance(request, Mapping):
        u = request["url"]
        if isinstance(u, str):
            return u
    raw_url = getattr(request, "url", None)
    if isinstance(raw_url, str):
        return raw_url
    if raw_url is not None and hasattr(raw_url, "path"):
        return str(raw_url.path)
    return ""


class InboundPriorityBulkheadGuard:
    """Inbound bulkhead + priority shedding + load-leveling queue guard."""

    def __init__(
        self,
        bulkhead: InboundBulkheadService,
        shedding_config: PrioritySheddingConfig | Mapping[str, Any] | None = None,
        load_level_config: InboundLoadLevelConfig | Mapping[str, Any] | None = None,
    ) -> None:
        self.bulkhead = bulkhead
        self.shedding_config = _coerce_shedding_config(shedding_config)
        self.load_level_config = (
            _coerce_load_level_config(load_level_config)
            if load_level_config is not None
            else bulkhead.load_level_config
        )

    def _resolve_metadata(
        self,
        request_or_ctx: Any,
        *,
        handler: Any = None,
        controller: Any = None,
    ) -> tuple[str, bool, ApiPriorityLevel, Any, Any]:
        ctx_type = "http"
        get_type = getattr(request_or_ctx, "getType", None) or getattr(
            request_or_ctx, "get_type", None
        )
        if callable(get_type):
            ctx_type = str(get_type())

        get_handler = getattr(request_or_ctx, "getHandler", None) or getattr(
            request_or_ctx, "get_handler", None
        )
        if handler is None and callable(get_handler):
            handler = get_handler()

        get_class = getattr(request_or_ctx, "getClass", None) or getattr(
            request_or_ctx, "get_class", None
        )
        if controller is None and callable(get_class):
            controller = get_class()

        req: Any = request_or_ctx
        res: Any = None
        switch_http = getattr(request_or_ctx, "switchToHttp", None) or getattr(
            request_or_ctx, "switch_to_http", None
        )
        if callable(switch_http):
            http_ctx = switch_http()
            get_req = getattr(http_ctx, "getRequest", None) or getattr(
                http_ctx, "get_request", None
            )
            if callable(get_req):
                req = get_req()
            get_res = getattr(http_ctx, "getResponse", None) or getattr(
                http_ctx, "get_response", None
            )
            if callable(get_res):
                res = get_res()

        route, endpoint = _find_route_endpoint(req)
        targets = [t for t in (handler, endpoint, route, controller) if t is not None]

        should_skip = any(
            bool(getattr(t, SKIP_INBOUND_BULKHEAD_KEY, False)) for t in targets
        )

        resolved_prio = self.shedding_config.default_priority
        for t in targets:
            val = getattr(t, API_PRIORITY_KEY, None)
            if val is not None:
                resolved_prio = _coerce_priority(val, resolved_prio)
                break

        return ctx_type, should_skip, resolved_prio, req, res

    def _attach_release(self, response: Any) -> bool:
        if response is None:
            return False
        raw = getattr(response, "raw", response)
        once_fn = getattr(raw, "once", None)
        if not callable(once_fn):
            return False

        has_released = False

        def _release_once() -> None:
            nonlocal has_released
            if has_released:
                return
            has_released = True
            self.bulkhead.release()

        once_fn("finish", _release_once)
        once_fn("close", _release_once)
        return True

    async def can_activate(
        self,
        request_or_ctx: Any,
        *,
        handler: Any = None,
        controller: Any = None,
        attach_release: bool = True,
    ) -> bool:
        ctx_type, should_skip, priority, req, res = self._resolve_metadata(
            request_or_ctx,
            handler=handler,
            controller=controller,
        )
        if ctx_type != "http":
            return True

        if not self.bulkhead.is_enabled():
            return True

        if should_skip:
            return True

        url = _extract_request_url(req)
        if is_pressure_exempt_path(url):
            return True

        if self.shedding_config.enabled and should_shed_priority_request(
            self.bulkhead.active_count,
            self.bulkhead.max_in_flight,
            priority,
            self.shedding_config.thresholds,
        ):
            raise PriorityShedRejectedError()

        configured_wait = self.load_level_config.max_wait_ms
        budget = remaining_ms()
        max_wait_ms = (
            configured_wait if budget is None else min(configured_wait, budget)
        )

        signal = get_request_signal()
        acquire_opts: dict[str, Any] = {
            "priority": priority,
            "maxWaitMs": max_wait_ms,
        }
        if signal is not None:
            acquire_opts["signal"] = signal

        await self.bulkhead.acquire(acquire_opts)

        if attach_release:
            self._attach_release(res)
        return True

    async def canActivate(
        self,
        request_or_ctx: Any,
        *,
        handler: Any = None,
        controller: Any = None,
    ) -> bool:
        return await self.can_activate(
            request_or_ctx,
            handler=handler,
            controller=controller,
        )

    async def __call__(self, request: Request) -> AsyncIterator[bool]:
        """FastAPI yield dependency ensuring bulkhead slot release in `finally`."""
        ctx_type, should_skip, _prio, req, _res = self._resolve_metadata(request)
        url = _extract_request_url(req)
        will_acquire = (
            ctx_type == "http"
            and self.bulkhead.is_enabled()
            and not should_skip
            and not is_pressure_exempt_path(url)
        )
        await self.can_activate(request, attach_release=False)
        try:
            yield True
        finally:
            if will_acquire:
                self.bulkhead.release()


ApiPriority = api_priority
SkipInboundBulkhead = skip_inbound_bulkhead
computeInboundUtilization = compute_inbound_utilization
shouldShedPriorityRequest = should_shed_priority_request

__all__ = [
    "DEFAULT_PRIORITY_SHEDDING_THRESHOLDS",
    "ApiPriority",
    "ApiPriorityLevel",
    "InboundBulkheadConfig",
    "InboundBulkheadService",
    "InboundLoadLevelConfig",
    "InboundPriorityBulkheadGuard",
    "PrioritySheddingConfig",
    "PrioritySheddingThresholds",
    "SkipInboundBulkhead",
    "api_priority",
    "computeInboundUtilization",
    "compute_inbound_utilization",
    "parse_inbound_bulkhead_config",
    "parse_inbound_load_level_config",
    "parse_priority_shedding_config",
    "shouldShedPriorityRequest",
    "should_shed_priority_request",
    "skip_inbound_bulkhead",
]
