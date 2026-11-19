from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FeatureFlagType = Literal["boolean", "percentage"]
FeatureFlagTargetingAttribute = Literal["userId", "organizationId"]


class FeatureFlagTargetingDto(BaseModel):
    attribute: FeatureFlagTargetingAttribute


class CreateFeatureFlagDto(BaseModel):
    key: str = Field(..., min_length=1, max_length=128)
    enabled: bool
    type: FeatureFlagType
    rolloutPercentage: int | None = Field(default=None, ge=0, le=100)
    targeting: FeatureFlagTargetingDto | None = None
    description: str | None = Field(default=None, max_length=512)
    owner: str | None = Field(default=None, max_length=128)
    expiresAt: str | None = Field(default=None, max_length=32)


class UpdateFeatureFlagDto(BaseModel):
    expectedVersion: int = Field(..., ge=1)
    enabled: bool
    type: FeatureFlagType
    rolloutPercentage: int | None = Field(default=None, ge=0, le=100)
    targeting: FeatureFlagTargetingDto | None = None
    description: str | None = Field(default=None, max_length=512)
    owner: str | None = Field(default=None, max_length=128)
    expiresAt: str | None = Field(default=None, max_length=32)


class DeleteFeatureFlagDto(BaseModel):
    expectedVersion: int = Field(..., ge=1)
