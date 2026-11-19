from __future__ import annotations

from app.core.feature_flag.audit_service import FeatureFlagAuditService
from app.core.feature_flag.cache import FeatureFlagCache
from app.core.feature_flag.constants import (
    ADMIN_FEATURE_FLAG_PERMISSIONS,
    DEFAULT_FEATURE_FLAG_ENV,
    DEFAULT_FEATURE_FLAG_RECONCILE_MS,
    FEATURE_FLAG_EVENTS_CHANNEL,
    FEATURE_FLAG_KEY_PREFIX,
    FEATURE_FLAG_PERMISSIONS_KEY,
    PERMISSION_CREATE,
    PERMISSION_DELETE,
    PERMISSION_READ,
    PERMISSION_UPDATE,
    FeatureFlagPermission,
    feature_flag_events_channel,
    feature_flag_index_key,
    feature_flag_key,
)
from app.core.feature_flag.dto import (
    CreateFeatureFlagDto,
    DeleteFeatureFlagDto,
    FeatureFlagTargetingDto,
    UpdateFeatureFlagDto,
)
from app.core.feature_flag.evaluator import (
    compute_rollout_bucket,
    dict_to_feature_flag,
    evaluate_feature_flag,
    feature_flag_to_dict,
    is_valid_feature_flag_shape,
)
from app.core.feature_flag.feature_flag_controller import (
    router as feature_flag_router,
)
from app.core.feature_flag.feature_flag_manager import FeatureFlagManager
from app.core.feature_flag.feature_flag_service import FeatureFlagService
from app.core.feature_flag.health import FeatureFlagHealthIndicator
from app.core.feature_flag.reconciler import FeatureFlagReconciler
from app.core.feature_flag.repository import FeatureFlagRepository
from app.core.feature_flag.schemas import (
    FeatureFlagAudit,
    FeatureFlagAuditAction,
)
from app.core.feature_flag.subscriber import FeatureFlagSubscriber
from app.core.feature_flag.types import (
    EvaluationReason,
    FeatureFlagEventType,
    FeatureFlagTargetingAttribute,
    FeatureFlagType,
    IFeatureFlag,
    IFeatureFlagContext,
    IFeatureFlagEvaluationOptions,
    IFeatureFlagEvaluationResult,
    IFeatureFlagHealthStatus,
    IFeatureFlagTargeting,
)

__all__ = [
    "ADMIN_FEATURE_FLAG_PERMISSIONS",
    "DEFAULT_FEATURE_FLAG_ENV",
    "DEFAULT_FEATURE_FLAG_RECONCILE_MS",
    "FEATURE_FLAG_EVENTS_CHANNEL",
    "FEATURE_FLAG_KEY_PREFIX",
    "FEATURE_FLAG_PERMISSIONS_KEY",
    "PERMISSION_CREATE",
    "PERMISSION_DELETE",
    "PERMISSION_READ",
    "PERMISSION_UPDATE",
    "CreateFeatureFlagDto",
    "DeleteFeatureFlagDto",
    "EvaluationReason",
    "FeatureFlagAudit",
    "FeatureFlagAuditAction",
    "FeatureFlagAuditService",
    "FeatureFlagCache",
    "FeatureFlagEventType",
    "FeatureFlagHealthIndicator",
    "FeatureFlagManager",
    "FeatureFlagPermission",
    "FeatureFlagReconciler",
    "FeatureFlagRepository",
    "FeatureFlagService",
    "FeatureFlagSubscriber",
    "FeatureFlagTargetingAttribute",
    "FeatureFlagTargetingDto",
    "FeatureFlagType",
    "IFeatureFlag",
    "IFeatureFlagContext",
    "IFeatureFlagEvaluationOptions",
    "IFeatureFlagEvaluationResult",
    "IFeatureFlagHealthStatus",
    "IFeatureFlagTargeting",
    "UpdateFeatureFlagDto",
    "compute_rollout_bucket",
    "dict_to_feature_flag",
    "evaluate_feature_flag",
    "feature_flag_events_channel",
    "feature_flag_index_key",
    "feature_flag_key",
    "feature_flag_router",
    "feature_flag_to_dict",
    "is_valid_feature_flag_shape",
]
