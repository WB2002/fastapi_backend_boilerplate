from __future__ import annotations

from app.core.http_client.circuit_breaker import CircuitBreaker, CircuitState
from app.core.http_client.concurrency_limiter import (
    AdaptiveConcurrencyGate,
    FixedConcurrencyGate,
    scale_cluster_aware_limit,
)
from app.core.http_client.configured_http_client import ConfiguredHttpClient
from app.core.http_client.http_client import HttpClient
from app.core.http_client.retry_policy import (
    apply_jitter,
    calculate_retry_delay,
    compute_backoff_delay,
    default_can_retry,
    default_is_transient,
)
from app.core.http_client.single_flight import (
    SingleFlightCoalescer,
    default_coalescing_key,
)
from app.core.http_client.types import (
    AdaptiveConcurrencyTuning,
    AdaptiveLimitExceededError,
    CircuitBreakerPolicy,
    CircuitOpenError,
    ClientIdentity,
    ClientKind,
    ClientPolicySnapshot,
    CoalescingPolicy,
    ConcurrencyLimitPolicy,
    DeadlinePropagationPolicy,
    HttpClientError,
    HttpNetworkError,
    HttpRequest,
    HttpResponse,
    HttpTimeoutError,
    RetryExhaustedError,
    RetryPolicy,
    SsrBlockedError,
    TimeoutPolicy,
)

# Aliases matching NestJS naming
HttpError = HttpClientError
TimeoutError = HttpTimeoutError
ClientClosedError = HttpClientError
RequestAbortedError = HttpClientError
RequestCoalescer = SingleFlightCoalescer
ConcurrencyLimiter = FixedConcurrencyGate

__all__ = [
    "AdaptiveConcurrencyGate",
    "AdaptiveConcurrencyTuning",
    "AdaptiveLimitExceededError",
    "CircuitBreaker",
    "CircuitBreakerPolicy",
    "CircuitOpenError",
    "CircuitState",
    "ClientClosedError",
    "ClientIdentity",
    "ClientKind",
    "ClientPolicySnapshot",
    "CoalescingPolicy",
    "ConcurrencyLimitPolicy",
    "ConcurrencyLimiter",
    "ConfiguredHttpClient",
    "DeadlinePropagationPolicy",
    "FixedConcurrencyGate",
    "HttpClient",
    "HttpClientError",
    "HttpError",
    "HttpNetworkError",
    "HttpRequest",
    "HttpResponse",
    "HttpTimeoutError",
    "RequestAbortedError",
    "RequestCoalescer",
    "RetryExhaustedError",
    "RetryPolicy",
    "SingleFlightCoalescer",
    "SsrBlockedError",
    "TimeoutError",
    "TimeoutPolicy",
    "apply_jitter",
    "calculate_retry_delay",
    "compute_backoff_delay",
    "default_can_retry",
    "default_coalescing_key",
    "default_is_transient",
    "scale_cluster_aware_limit",
]
