from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ConformanceCacheRequestDto(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    value: str | None = Field(default=None)
    loaderDelayMs: int | None = Field(default=None, ge=0, le=10_000)
    shouldFailLoader: bool | None = Field(default=None)
    softTtlMs: int = Field(..., ge=1, le=600_000)
    hardTtlMs: int = Field(..., ge=1, le=600_000)
    shouldCacheNull: bool | None = Field(default=None)
    negativeSoftTtlMs: int | None = Field(default=None, ge=1, le=600_000)
    negativeHardTtlMs: int | None = Field(default=None, ge=1, le=600_000)


class ConformanceDeadlineQueryDto(BaseModel):
    delayMs: int = Field(default=0, ge=0, le=30_000)


class ConformanceHttpClientRequestDto(BaseModel):
    clientKind: Literal["internal", "external"]
    clientName: str = Field(..., pattern=r"^[a-z0-9-]{1,64}$")
    targetUrl: str
    maxRetries: int | None = Field(default=None, ge=0, le=5)
    baseDelayMs: int | None = Field(default=None, ge=0, le=2000)
    circuitBreakerFailureThreshold: int | None = Field(default=None, ge=1, le=20)
    circuitBreakerResetTimeoutMs: int | None = Field(default=None, ge=100, le=60_000)
    shouldCoalesce: bool | None = Field(default=None)
    concurrencyMode: Literal["fixed", "adaptive"] | None = Field(default=None)
    maxConcurrent: int | None = Field(default=None, ge=1, le=100)
    shouldPropagateDeadline: bool | None = Field(default=None)
    timeoutMs: int | None = Field(default=None, ge=50, le=30_000)


class ConformancePriorityQueryDto(BaseModel):
    holdMs: int = Field(default=0, ge=0, le=10_000)


class ConformanceRedactionRequestDto(BaseModel):
    payload: dict[str, Any]
