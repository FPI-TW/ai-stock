"""Admin request/response models. camelCase at the boundary; snake_case within."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: Literal["user", "admin"]


class CreateUserResponse(BaseModel):
    id: UUID
    status: str


class ProvisionUserRequest(BaseModel):
    """Admin creates an active account with an admin-chosen password (no invitation)."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str
    role: Literal["user", "admin"]


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


class KillSwitchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    # strip_whitespace + min_length=1 makes reason mandatory and rejects a blank /
    # whitespace-only reason with a standard (JSON-serializable) validation error.
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class KillSwitchStateResponse(BaseModel):
    enabled: bool
    reason: str | None = None
    updated_at: datetime | None = Field(default=None, serialization_alias="updatedAt")
    updated_by: UUID | None = Field(default=None, serialization_alias="updatedBy")
