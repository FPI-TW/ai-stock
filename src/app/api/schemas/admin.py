"""Admin request/response models. camelCase at the boundary; snake_case within."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    role: Literal["user", "admin"]


class CreateUserResponse(BaseModel):
    id: UUID
    status: str
