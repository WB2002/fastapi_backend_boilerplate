from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FeatureFlagType = Literal["boolean", "percentage"]
FeatureFlagTargetingAttribute = Literal["userId", "organizationId"]
FeatureFlagEventType = Literal["FLAG_UPDATED", "FLAG_DELETED"]


@dataclass(slots=True)
class IFeatureFlagTargeting:
    attribute: FeatureFlagTargetingAttribute


@dataclass(slots=True)
class IFeatureFlag:
    key: str
    enabled: bool
    type: FeatureFlagType
    environment: str
    created_at: str
    updated_at: str
    version: int
    rollout_percentage: int | None = None
    targeting: IFeatureFlagTargeting | None = None
    description: str | None = None
    owner: str | None = None
    expires_at: str | None = None

    @property
    def rolloutPercentage(self) -> int | None:
        return self.rollout_percentage

    @property
    def createdAt(self) -> str:
        return self.created_at

    @property
    def updatedAt(self) -> str:
        return self.updated_at

    @property
    def expiresAt(self) -> str | None:
        return self.expires_at


@dataclass(slots=True)
class IFeatureFlagContext:
    user_id: str | None = None
    organization_id: str | None = None

    @property
    def userId(self) -> str | None:
        return self.user_id

    @property
    def organizationId(self) -> str | None:
        return self.organization_id


@dataclass(slots=True)
class IFeatureFlagEvaluationOptions:
    default_value: bool | None = None

    @property
    def defaultValue(self) -> bool | None:
        return self.default_value


EvaluationReason = Literal[
    "missing_flag",
    "boolean",
    "percentage",
    "missing_targeting",
    "invalid_percentage",
    "disabled",
]


@dataclass(slots=True)
class IFeatureFlagEvaluationResult:
    enabled: bool
    used_default: bool
    reason: EvaluationReason

    @property
    def usedDefault(self) -> bool:
        return self.used_default


@dataclass(slots=True)
class IFeatureFlagHealthStatus:
    healthy: bool
    stale: bool
    redis_available: bool
    pub_sub_connected: bool
    flag_count: int
    last_reconciled_at: str | None
    last_error: str | None

    @property
    def redisAvailable(self) -> bool:
        return self.redis_available

    @property
    def pubSubConnected(self) -> bool:
        return self.pub_sub_connected

    @property
    def flagCount(self) -> int:
        return self.flag_count

    @property
    def lastReconciledAt(self) -> str | None:
        return self.last_reconciled_at

    @property
    def lastError(self) -> str | None:
        return self.last_error
