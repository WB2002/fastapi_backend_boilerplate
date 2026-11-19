from __future__ import annotations

from typing import Final, Literal

FEATURE_FLAG_KEY_PREFIX: Final[str] = "feature_flags"
FEATURE_FLAG_EVENTS_CHANNEL: Final[str] = "feature_flags:events"
DEFAULT_FEATURE_FLAG_RECONCILE_MS: Final[int] = 45_000
DEFAULT_FEATURE_FLAG_ENV: Final[str] = "development"

FEATURE_FLAG_PERMISSIONS_KEY: Final[str] = "feature_flag_permissions"

type FeatureFlagPermission = Literal[
    "flag:read",
    "flag:create",
    "flag:update",
    "flag:delete",
]

PERMISSION_READ: Final[FeatureFlagPermission] = "flag:read"
PERMISSION_CREATE: Final[FeatureFlagPermission] = "flag:create"
PERMISSION_UPDATE: Final[FeatureFlagPermission] = "flag:update"
PERMISSION_DELETE: Final[FeatureFlagPermission] = "flag:delete"

ADMIN_FEATURE_FLAG_PERMISSIONS: Final[list[FeatureFlagPermission]] = [
    PERMISSION_READ,
    PERMISSION_CREATE,
    PERMISSION_UPDATE,
    PERMISSION_DELETE,
]


def feature_flag_key(env: str, flag_key: str) -> str:
    return f"{FEATURE_FLAG_KEY_PREFIX}:{env}:{flag_key}"


def feature_flag_index_key(env: str) -> str:
    return f"{FEATURE_FLAG_KEY_PREFIX}:index:{env}"


def feature_flag_events_channel(env: str) -> str:
    return f"{FEATURE_FLAG_EVENTS_CHANNEL}:{env}"
