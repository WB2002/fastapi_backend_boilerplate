from __future__ import annotations

import hashlib
import math
from typing import Any

from app.core.feature_flag.types import (
    IFeatureFlag,
    IFeatureFlagContext,
    IFeatureFlagEvaluationOptions,
    IFeatureFlagEvaluationResult,
    IFeatureFlagTargeting,
)


def compute_rollout_bucket(flag_key: str, targeting_value: str) -> int:
    digest = hashlib.sha256(f"{flag_key}:{targeting_value}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def resolve_targeting_value(
    flag: IFeatureFlag, context: IFeatureFlagContext
) -> str | None:
    attr = flag.targeting.attribute if flag.targeting else "userId"
    if attr == "organizationId":
        return context.organization_id
    return context.user_id


def evaluate_feature_flag(
    flag: IFeatureFlag | None,
    context: IFeatureFlagContext,
    options: IFeatureFlagEvaluationOptions | None = None,
) -> IFeatureFlagEvaluationResult:
    default_enabled = (
        options.default_value
        if options and options.default_value is not None
        else False
    )

    if flag is None:
        return IFeatureFlagEvaluationResult(
            enabled=default_enabled, used_default=True, reason="missing_flag"
        )

    if not flag.enabled:
        return IFeatureFlagEvaluationResult(
            enabled=False, used_default=False, reason="disabled"
        )

    if flag.type == "boolean":
        return IFeatureFlagEvaluationResult(
            enabled=True, used_default=False, reason="boolean"
        )

    if flag.type == "percentage":
        pct = flag.rollout_percentage
        if pct is None or not math.isfinite(pct) or pct < 0 or pct > 100:
            return IFeatureFlagEvaluationResult(
                enabled=default_enabled,
                used_default=True,
                reason="invalid_percentage",
            )

        targeting_value = resolve_targeting_value(flag, context)
        if not targeting_value:
            return IFeatureFlagEvaluationResult(
                enabled=default_enabled,
                used_default=True,
                reason="missing_targeting",
            )

        bucket = compute_rollout_bucket(flag.key, targeting_value)
        return IFeatureFlagEvaluationResult(
            enabled=bucket < pct, used_default=False, reason="percentage"
        )

    return IFeatureFlagEvaluationResult(
        enabled=default_enabled, used_default=True, reason="missing_flag"
    )


def is_valid_feature_flag_shape(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get("key"), str) or not value["key"]:
        return False
    if not isinstance(value.get("enabled"), bool):
        return False
    if value.get("type") not in ("boolean", "percentage"):
        return False
    if not isinstance(value.get("environment"), str):
        return False
    if not isinstance(value.get("version"), int) or value["version"] < 1:
        return False
    if not isinstance(value.get("createdAt"), str) or not isinstance(
        value.get("updatedAt"), str
    ):
        return False
    if value["type"] == "percentage":
        pct = value.get("rolloutPercentage")
        if not isinstance(pct, int) or pct < 0 or pct > 100:
            return False
        targeting = value.get("targeting")
        if not isinstance(targeting, dict) or targeting.get("attribute") not in (
            "userId",
            "organizationId",
        ):
            return False
    return True


def dict_to_feature_flag(data: dict[str, Any]) -> IFeatureFlag:
    targeting = None
    if "targeting" in data and isinstance(data["targeting"], dict):
        targeting = IFeatureFlagTargeting(attribute=data["targeting"]["attribute"])
    return IFeatureFlag(
        key=data["key"],
        enabled=data["enabled"],
        type=data["type"],
        environment=data["environment"],
        created_at=data["createdAt"],
        updated_at=data["updatedAt"],
        version=data["version"],
        rollout_percentage=data.get("rolloutPercentage"),
        targeting=targeting,
        description=data.get("description"),
        owner=data.get("owner"),
        expires_at=data.get("expiresAt"),
    )


def feature_flag_to_dict(flag: IFeatureFlag) -> dict[str, Any]:
    res: dict[str, Any] = {
        "key": flag.key,
        "enabled": flag.enabled,
        "type": flag.type,
        "environment": flag.environment,
        "createdAt": flag.created_at,
        "updatedAt": flag.updated_at,
        "version": flag.version,
    }
    if flag.rollout_percentage is not None:
        res["rolloutPercentage"] = flag.rollout_percentage
    if flag.targeting is not None:
        res["targeting"] = {"attribute": flag.targeting.attribute}
    if flag.description is not None:
        res["description"] = flag.description
    if flag.owner is not None:
        res["owner"] = flag.owner
    if flag.expires_at is not None:
        res["expiresAt"] = flag.expires_at
    return res
