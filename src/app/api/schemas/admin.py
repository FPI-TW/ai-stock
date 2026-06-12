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


class TwoFactorSetupResponse(BaseModel):
    provisioning_uri: str = Field(serialization_alias="provisioningUri")
    secret: str


class TwoFactorVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str


class TwoFactorVerifyResponse(BaseModel):
    access_token: str = Field(serialization_alias="accessToken")
    token_type: str = Field(default="Bearer", serialization_alias="tokenType")
    expires_in: int = Field(serialization_alias="expiresIn")
    role: str
