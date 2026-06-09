"""Auth request/response models. JSON is camelCase at the boundary (serialization
aliases); Python attributes stay snake_case."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


class SessionTokenResponse(BaseModel):
    """Returned by both /auth/login and /auth/refresh. The refresh + CSRF tokens
    travel as cookies, not in this body."""

    access_token: str = Field(serialization_alias="accessToken")
    token_type: str = Field(default="Bearer", serialization_alias="tokenType")
    expires_in: int = Field(serialization_alias="expiresIn")
    role: str


class MeResponse(BaseModel):
    id: UUID
    email: str
    role: str
    status: str
    mfa_enabled: bool = Field(serialization_alias="mfaEnabled")
