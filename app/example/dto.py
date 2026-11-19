from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExampleCaptchaRequestDto(BaseModel):
    captchaToken: str = Field(..., min_length=1)


class CreateExampleNotificationDto(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    metadata: dict[str, Any] | None = None


class EnqueueEchoJobDto(BaseModel):
    message: str = Field(..., min_length=1)
    dedupeKey: str | None = Field(default=None)


class ExampleRealtimeMessageDto(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
