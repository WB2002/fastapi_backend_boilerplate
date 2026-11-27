from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.common.decorators import public
from app.core.notification import (
    INotification,
    NotificationService,
    SlackNotificationService,
)
from app.example.dto import CreateExampleNotificationDto

_ROUTE_VALIDATION = BaseModel

router = APIRouter(tags=["Example"])


def get_notification_service() -> NotificationService:
    return SlackNotificationService()


@router.post("/example/notification")
@public
async def send_example_notification(
    dto: CreateExampleNotificationDto,
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> dict[str, Any]:
    op_id = str(uuid.uuid4())
    meta = dict(dto.metadata or {})
    meta["operationId"] = op_id
    meta["operationStatus"] = "success"

    notification = INotification(
        title="Example Notification",
        message=dto.message,
        metadata=meta,
    )
    await service.send(notification)

    return {
        "status": "success",
        "operationId": op_id,
        "notificationSent": True,
    }
