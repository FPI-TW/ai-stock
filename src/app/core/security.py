from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class RequestUser:
    """The authenticated principal for a request, derived from the access token.

    `session_id` / `mfa_verified` default so test dependency-overrides can construct
    a principal with just `user_id` + `role`; real requests always populate them
    from the decoded token.
    """

    user_id: UUID
    role: str
    session_id: UUID = field(default_factory=uuid4)
    mfa_verified: bool = False


def get_owner_user_id(user: RequestUser) -> UUID:
    return user.user_id
