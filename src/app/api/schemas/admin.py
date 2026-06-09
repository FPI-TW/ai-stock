"""Admin request/response models. camelCase at the boundary; snake_case within."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    role: Literal["user", "admin"]


class CreateUserResponse(BaseModel):
    id: UUID
    status: str


class UserSummary(BaseModel):
    id: UUID
    email: str
    role: str
    status: str
    mfa_enabled: bool = Field(serialization_alias="mfaEnabled")
    created_at: datetime = Field(serialization_alias="createdAt")


class UserListResponse(BaseModel):
    data: list[UserSummary]
