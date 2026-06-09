"""Auth routes: login, refresh, logout, me.

Cookie design (spec §13):
- refresh_token: HttpOnly, SameSite=Lax, Secure outside LOCAL_MODE, scoped to
  `/auth` so it rides along to /auth/refresh and /auth/logout but never to the
  business APIs (which authenticate via the Bearer access token instead).
- csrf_token: readable by JS (not HttpOnly) so the SPA can echo it back in the
  X-CSRF-Token header for the double-submit check on cookie-authenticated calls.
"""

import ipaddress
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response, status

from app.api.deps import (
    AcceptInvitationCommandDep,
    CurrentUserDep,
    LoginCommandDep,
    LogoutCommandDep,
    PasswordResetConfirmCommandDep,
    PasswordResetRequestCommandDep,
    RefreshCommandDep,
    SettingsDep,
    UserRepoDep,
)
from app.api.errors import ApiError, ErrorCode, get_request_id
from app.api.schemas.auth import (
    AcceptInvitationRequest,
    LoginRequest,
    MeResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequestRequest,
    SessionTokenResponse,
)
from app.commands.account import AcceptInvitationInput
from app.commands.auth import IssuedSession, LoginInput, LogoutInput, RefreshInput
from app.commands.password_reset import PasswordResetConfirmInput, PasswordResetRequestInput
from app.core.config import Settings

router = APIRouter()

_REFRESH_COOKIE = "refresh_token"
_CSRF_COOKIE = "csrf_token"
_CSRF_HEADER = "X-CSRF-Token"
_REFRESH_COOKIE_PATH = "/auth"
_CSRF_COOKIE_PATH = "/"


def _client_ip(request: Request) -> str | None:
    """The peer IP, but only if it parses as one (TestClient uses 'testclient',
    which the INET column would reject)."""
    if request.client is None:
        return None
    try:
        ipaddress.ip_address(request.client.host)
    except ValueError:
        return None
    return request.client.host


def _set_session_cookies(response: Response, issued: IssuedSession, settings: Settings, now: datetime) -> None:
    refresh_max_age = int((issued.refresh_expires_at - now).total_seconds())
    response.set_cookie(
        _REFRESH_COOKIE,
        issued.refresh_token,
        max_age=refresh_max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=_REFRESH_COOKIE_PATH,
    )
    response.set_cookie(
        _CSRF_COOKIE,
        issued.csrf_token,
        max_age=refresh_max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path=_CSRF_COOKIE_PATH,
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(_REFRESH_COOKIE, path=_REFRESH_COOKIE_PATH)
    response.delete_cookie(_CSRF_COOKIE, path=_CSRF_COOKIE_PATH)


def _session_response(issued: IssuedSession, now: datetime) -> SessionTokenResponse:
    return SessionTokenResponse(
        access_token=issued.access_token,
        expires_in=int((issued.access_expires_at - now).total_seconds()),
        role=issued.role,
    )


@router.post("/login", response_model=SessionTokenResponse)
def login(
    request: Request,
    body: LoginRequest,
    command: LoginCommandDep,
    settings: SettingsDep,
    response: Response,
) -> SessionTokenResponse:
    now = datetime.now(UTC)
    issued = command.execute(
        LoginInput(
            email=body.email,
            password=body.password,
            now=now,
            user_agent=request.headers.get("User-Agent"),
            ip=_client_ip(request),
            request_id=get_request_id(request),
        )
    )
    _set_session_cookies(response, issued, settings, now)
    return _session_response(issued, now)


@router.post("/invitations/accept", response_model=SessionTokenResponse)
def accept_invitation(
    request: Request,
    body: AcceptInvitationRequest,
    command: AcceptInvitationCommandDep,
    settings: SettingsDep,
    response: Response,
) -> SessionTokenResponse:
    now = datetime.now(UTC)
    issued = command.execute(
        AcceptInvitationInput(
            raw_token=body.token,
            password=body.password,
            terms_version=body.terms_version,
            now=now,
            user_agent=request.headers.get("User-Agent"),
            ip=_client_ip(request),
            request_id=get_request_id(request),
        )
    )
    _set_session_cookies(response, issued, settings, now)
    return _session_response(issued, now)


@router.post("/refresh", response_model=SessionTokenResponse)
def refresh(
    request: Request,
    command: RefreshCommandDep,
    settings: SettingsDep,
    response: Response,
) -> SessionTokenResponse:
    now = datetime.now(UTC)
    issued = command.execute(
        RefreshInput(
            raw_refresh_token=request.cookies.get(_REFRESH_COOKIE),
            csrf_cookie=request.cookies.get(_CSRF_COOKIE),
            csrf_header=request.headers.get(_CSRF_HEADER),
            now=now,
            user_agent=request.headers.get("User-Agent"),
            ip=_client_ip(request),
            request_id=get_request_id(request),
        )
    )
    _set_session_cookies(response, issued, settings, now)
    return _session_response(issued, now)


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def password_reset_request(
    request: Request,
    body: PasswordResetRequestRequest,
    command: PasswordResetRequestCommandDep,
) -> None:
    # Always 202: never reveals whether the email exists.
    command.execute(
        PasswordResetRequestInput(
            email=body.email,
            now=datetime.now(UTC),
            ip=_client_ip(request),
            request_id=get_request_id(request),
        )
    )


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_confirm(
    request: Request,
    body: PasswordResetConfirmRequest,
    command: PasswordResetConfirmCommandDep,
) -> None:
    command.execute(
        PasswordResetConfirmInput(
            raw_token=body.token,
            new_password=body.new_password,
            now=datetime.now(UTC),
            request_id=get_request_id(request),
        )
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    command: LogoutCommandDep,
    response: Response,
) -> None:
    command.execute(
        LogoutInput(
            raw_refresh_token=request.cookies.get(_REFRESH_COOKIE),
            csrf_cookie=request.cookies.get(_CSRF_COOKIE),
            csrf_header=request.headers.get(_CSRF_HEADER),
            now=datetime.now(UTC),
            request_id=get_request_id(request),
        )
    )
    _clear_session_cookies(response)


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUserDep, users: UserRepoDep) -> MeResponse:
    profile = users.get_by_id(user.user_id)
    if profile is None:
        # Token verified but the user no longer exists (deleted / mid-disable race).
        raise ApiError(code=ErrorCode.UNAUTHENTICATED, status_code=status.HTTP_401_UNAUTHORIZED)
    return MeResponse(
        id=profile.id,
        email=profile.email,
        role=profile.role,
        status=profile.status,
        mfa_enabled=profile.mfa_enabled,
    )
