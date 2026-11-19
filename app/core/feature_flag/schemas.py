from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

FeatureFlagAuditAction = Literal["create", "update", "delete"]


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclass(slots=True)
class FeatureFlagAudit:
    key: str
    environment: str
    action: FeatureFlagAuditAction
    actor_user_id: str
    actor_email: str
    old_value: dict[str, Any] | None
    new_value: dict[str, Any] | None
    version: int
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime.datetime = field(default_factory=_now_utc)

    @property
    def actorUserId(self) -> str:
        return self.actor_user_id

    @property
    def actorEmail(self) -> str:
        return self.actor_email

    @property
    def oldValue(self) -> dict[str, Any] | None:
        return self.old_value

    @property
    def newValue(self) -> dict[str, Any] | None:
        return self.new_value

    @property
    def createdAt(self) -> datetime.datetime:
        return self.created_at

    def to_mongo_doc(self) -> dict[str, Any]:
        return {
            "_id": self.id,
            "key": self.key,
            "environment": self.environment,
            "action": self.action,
            "actorUserId": self.actor_user_id,
            "actorEmail": self.actor_email,
            "oldValue": self.old_value,
            "newValue": self.new_value,
            "version": self.version,
            "createdAt": self.created_at,
        }
