"""Admin routes (L1 minimal: account CRUD). Gated by AdminUserDep — admin role +
verified 2FA. Account disable + cascade and the user list arrive in a later sub-step.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Request, status

from app.api.deps import AdminUserDep, CreateUserCommandDep
from app.api.errors import get_request_id
from app.api.schemas.admin import CreateUserRequest, CreateUserResponse
from app.commands.account import CreateUserInput

router = APIRouter()


@router.post("/users", response_model=CreateUserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    request: Request,
    body: CreateUserRequest,
    admin: AdminUserDep,
    command: CreateUserCommandDep,
) -> CreateUserResponse:
    created = command.execute(
        CreateUserInput(
            email=body.email,
            role=body.role,
            created_by_admin_id=admin.user_id,
            now=datetime.now(UTC),
            request_id=get_request_id(request),
        )
    )
    return CreateUserResponse(id=created.user_id, status="invited")
