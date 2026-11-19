from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

ClientKind = Literal["internal", "external"]
ConnectionPoolMode = Literal["dedicated", "shared"]


class HttpClientError(Exception):
    """Base error for outbound HTTP client."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response = response


class HttpTimeoutError(HttpClientError):
    pass


class HttpNetworkError(HttpClientError):
    pass


class CircuitOpenError(HttpClientError):
    pass


class AdaptiveLimitExceededError(HttpClientError):
    pass


class RetryExhaustedError(HttpClientError):
    def __init__(
        self,
        message: str,
        *,
        cause: Exception | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.metadata = metadata or {}


class SsrBlockedError(HttpClientError):
    pass


@dataclass
class ClientIdentity:
    kind: ClientKind
    service_name: str


@dataclass
class HttpRequest:
    method: str
    url: str
    path: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    data: Any = None
    json: Any = None
    timeout: float | None = None
    deadline_ms: int | None = None


@dataclass
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    request: HttpRequest

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        import json

        return json.loads(self.content.decode("utf-8"))


@dataclass
class TimeoutPolicy:
    timeout_ms: int | None = None
    total_timeout_ms: int | None = None


@dataclass
class DeadlinePropagationPolicy:
    propagate_header: bool = True


@dataclass
class CircuitBreakerPolicy:
    failure_threshold: int = 5
    reset_timeout_ms: int = 30000
    half_open_max_attempts: int = 1


@dataclass
class AdaptiveConcurrencyTuning:
    algorithm: Literal["aimd", "vegas", "gradient2"] = "vegas"
    initial_limit: int = 10
    min_limit: int = 1
    max_limit: int = 200
    success_threshold: int = 10
    vegas_alpha: int = 3
    vegas_beta: int = 6
    vegas_min_samples: int = 10
    gradient_min_samples: int = 10
    gradient_smoothing: float = 0.2


@dataclass
class ConcurrencyLimitPolicy:
    mode: Literal["fixed", "adaptive"] = "fixed"
    max_concurrent: int = 50
    cluster_aware: bool = False
    adaptive: AdaptiveConcurrencyTuning = field(
        default_factory=AdaptiveConcurrencyTuning
    )


@dataclass
class CoalescingPolicy:
    enabled: bool = False
    methods: list[str] = field(default_factory=lambda: ["GET", "HEAD"])
    key_builder: Callable[[HttpRequest], str] | None = None


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    strategy: Literal["fixed", "linear", "exponential"] = "exponential"
    base_delay_ms: int = 1000
    max_delay_ms: int = 10000
    factor: float = 2.0
    jitter: Literal["none", "full", "equal", "decorrelated"] = "full"
    can_retry: Callable[[HttpRequest], bool] | None = None
    is_transient: Callable[[Exception | int | None], bool] | None = None


@dataclass
class ClientPolicySnapshot:
    identity: ClientIdentity
    base_url: str | None = None
    default_headers: dict[str, str] = field(default_factory=dict)
    timeout: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    deadline_propagation: DeadlinePropagationPolicy = field(
        default_factory=DeadlinePropagationPolicy
    )
    retry: RetryPolicy | None = None
    circuit_breaker: CircuitBreakerPolicy | None = None
    concurrency_limit: ConcurrencyLimitPolicy | None = None
    coalescing: CoalescingPolicy = field(default_factory=CoalescingPolicy)
    allow_private_urls: bool = False
