"""Admin routes (L1 minimal: account CRUD). Gated by AdminUserDep — admin role +
verified 2FA. Account disable + cascade and the user list arrive in a later sub-step.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request, status

from app.api.deps import (
    AdminUserDep,
    CreateUserCommandDep,
    DisableUserCommandDep,
    ResendInvitationCommandDep,
    UserRepoDep,
)
from app.api.errors import get_request_id
from app.api.schemas.admin import CreateUserRequest, CreateUserResponse, UserListResponse, UserSummary
from app.commands.account import CreateUserInput, DisableUserInput, ResendInvitationInput

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


@router.get("/users", response_model=UserListResponse)
def list_users(admin: AdminUserDep, users: UserRepoDep) -> UserListResponse:
    return UserListResponse(
        data=[
            UserSummary(
                id=u.id,
                email=u.email,
                role=u.role,
                status=u.status,
                mfa_enabled=u.mfa_enabled,
                created_at=u.created_at,
            )
            for u in users.list_all()
        ]
    )


@router.post("/users/{user_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
def disable_user(
    user_id: UUID,
    request: Request,
    admin: AdminUserDep,
    command: DisableUserCommandDep,
) -> None:
    command.execute(
        DisableUserInput(
            target_user_id=user_id,
            actor_admin_id=admin.user_id,
            now=datetime.now(UTC),
            request_id=get_request_id(request),
        )
    )


@router.post("/users/{user_id}/resend-invitation", status_code=status.HTTP_204_NO_CONTENT)
def resend_invitation(
    user_id: UUID,
    request: Request,
    admin: AdminUserDep,
    command: ResendInvitationCommandDep,
) -> None:
    command.execute(
        ResendInvitationInput(
            target_user_id=user_id,
            actor_admin_id=admin.user_id,
            now=datetime.now(UTC),
            request_id=get_request_id(request),
        )
    )
