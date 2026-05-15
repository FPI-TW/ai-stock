from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class RequestUser:
    user_id: UUID
    role: Literal["local"]


def build_local_user(settings: Settings) -> RequestUser:
    if settings.local_user_id is None:
        raise RuntimeError("LOCAL_USER_ID missing — Settings validator should have rejected this state")
    return RequestUser(user_id=settings.local_user_id, role="local")


def get_owner_user_id(user: RequestUser) -> UUID:
    return user.user_id
