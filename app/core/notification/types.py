from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class INotification:
    message: str
    title: str | None = None
    metadata: dict[str, Any] | None = None


class NotificationService(Protocol):
    async def send(self, notification: INotification) -> None: ...
