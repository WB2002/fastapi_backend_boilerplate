from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.feature_flag import (
    CreateFeatureFlagDto,
    DeleteFeatureFlagDto,
    FeatureFlagAuditService,
    FeatureFlagCache,
    FeatureFlagHealthIndicator,
    FeatureFlagManager,
    FeatureFlagRepository,
    FeatureFlagService,
    FeatureFlagTargetingDto,
    IFeatureFlag,
    IFeatureFlagContext,
    IFeatureFlagEvaluationOptions,
    IFeatureFlagTargeting,
    UpdateFeatureFlagDto,
    compute_rollout_bucket,
    evaluate_feature_flag,
)


def test_compute_rollout_bucket() -> None:
    bucket1 = compute_rollout_bucket("flag-a", "user-123")
    bucket2 = compute_rollout_bucket("flag-a", "user-123")
    assert bucket1 == bucket2
    assert 0 <= bucket1 < 100

    bucket_diff_user = compute_rollout_bucket("flag-a", "user-456")
    assert 0 <= bucket_diff_user < 100


def test_evaluate_feature_flag() -> None:
    # 1. Missing flag
    res1 = evaluate_feature_flag(
        None,
        IFeatureFlagContext(),
        IFeatureFlagEvaluationOptions(default_value=True),
    )
    assert res1.enabled is True
    assert res1.used_default is True
    assert res1.reason == "missing_flag"

    # 2. Disabled flag
    flag_disabled = IFeatureFlag(
        key="f1",
        enabled=False,
        type="boolean",
        environment="test",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        version=1,
    )
    res2 = evaluate_feature_flag(flag_disabled, IFeatureFlagContext())
    assert res2.enabled is False
    assert res2.reason == "disabled"

    # 3. Boolean enabled flag
    flag_bool = IFeatureFlag(
        key="f2",
        enabled=True,
        type="boolean",
        environment="test",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        version=1,
    )
    res3 = evaluate_feature_flag(flag_bool, IFeatureFlagContext())
    assert res3.enabled is True
    assert res3.reason == "boolean"

    # 4. Percentage flag 100%
    flag_100 = IFeatureFlag(
        key="f3",
        enabled=True,
        type="percentage",
        rollout_percentage=100,
        targeting=IFeatureFlagTargeting(attribute="userId"),
        environment="test",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        version=1,
    )
    res4 = evaluate_feature_flag(flag_100, IFeatureFlagContext(user_id="usr-1"))
    assert res4.enabled is True

    # 5. Percentage flag 0%
    flag_0 = IFeatureFlag(
        key="f4",
        enabled=True,
        type="percentage",
        rollout_percentage=0,
        targeting=IFeatureFlagTargeting(attribute="userId"),
        environment="test",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        version=1,
    )
    res5 = evaluate_feature_flag(flag_0, IFeatureFlagContext(user_id="usr-1"))
    assert res5.enabled is False

    # 6. Percentage flag missing targeting
    res6 = evaluate_feature_flag(
        flag_100,
        IFeatureFlagContext(user_id=None),
        IFeatureFlagEvaluationOptions(default_value=False),
    )
    assert res6.enabled is False
    assert res6.reason == "missing_targeting"


def test_feature_flag_cache() -> None:
    cache = FeatureFlagCache()
    assert cache.size() == 0

    flag = IFeatureFlag(
        key="beta_feature",
        enabled=True,
        type="boolean",
        environment="test",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        version=1,
    )
    cache.set(flag)
    assert cache.size() == 1
    assert cache.get("beta_feature") == flag

    cache.delete("beta_feature")
    assert cache.size() == 0
    assert cache.get("beta_feature") is None


@pytest.mark.asyncio
async def test_feature_flag_repository_and_manager() -> None:
    repo = FeatureFlagRepository()
    cache = FeatureFlagCache()
    service = FeatureFlagService(repository=repo, cache=cache, environment="test")
    audit = FeatureFlagAuditService()
    manager = FeatureFlagManager(repo, service, audit)

    actor = {"userId": "admin-1", "email": "admin@example.com"}

    # 1. Create flag
    dto_create = CreateFeatureFlagDto(
        key="new_checkout",
        enabled=True,
        type="percentage",
        rolloutPercentage=50,
        targeting=FeatureFlagTargetingDto(attribute="userId"),
    )
    created = await manager.create(dto_create, actor)
    assert created.key == "new_checkout"
    assert created.version == 1
    assert created.rollout_percentage == 50

    # 2. Duplicate create -> 409
    with pytest.raises(HTTPException) as exc1:
        await manager.create(dto_create, actor)
    assert exc1.value.status_code == 409

    # 3. Synchronous local is_enabled
    # Find a user where bucket < 50
    b = compute_rollout_bucket("new_checkout", "user_test_1")
    expected_enabled = b < 50
    assert (
        service.is_enabled("new_checkout", IFeatureFlagContext(user_id="user_test_1"))
        == expected_enabled
    )

    # 4. Update with version conflict -> 409
    dto_bad_update = UpdateFeatureFlagDto(
        expectedVersion=99,
        enabled=False,
        type="boolean",
    )
    with pytest.raises(HTTPException) as exc2:
        await manager.update("new_checkout", dto_bad_update, actor)
    assert exc2.value.status_code == 409

    # 5. Successful update
    dto_good_update = UpdateFeatureFlagDto(
        expectedVersion=1,
        enabled=True,
        type="boolean",
    )
    updated = await manager.update("new_checkout", dto_good_update, actor)
    assert updated.version == 2
    assert updated.type == "boolean"

    # Now boolean is enabled for all
    assert service.is_enabled("new_checkout") is True

    # 6. Delete
    await manager.delete("new_checkout", DeleteFeatureFlagDto(expectedVersion=2), actor)
    with pytest.raises(HTTPException) as exc3:
        await manager.get("new_checkout")
    assert exc3.value.status_code == 404

    # Audit recorded
    audits = audit.get_memory_audits()
    assert len(audits) == 3


def test_feature_flag_health() -> None:
    repo = FeatureFlagRepository()
    service = FeatureFlagService(repository=repo, environment="test")
    indicator = FeatureFlagHealthIndicator(service)

    health = indicator.get_health()
    assert health["status"] == "up"
    assert "flagCount" in health
    assert "stale" in health
