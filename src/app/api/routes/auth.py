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
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response, status

from app.api.deps import (
    AcceptInvitationCommandDep,
    ActiveUserDep,
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
from app.domain.auth import CsrfFailedError

router = APIRouter()

_REFRESH_COOKIE = "refresh_token"
_CSRF_COOKIE = "csrf_token"
_CSRF_HEADER = "X-CSRF-Token"
_REFRESH_COOKIE_PATH = "/auth"
_CSRF_COOKIE_PATH = "/"


def _client_ip(request: Request, settings: Settings) -> str | None:
    """The real client IP for the per-IP throttles (login:ip, pwreset:ip).

    Returns None when the peer is not a parseable IP (TestClient uses 'testclient',
    which the INET column would reject) — the IP gate is simply skipped then.

    Behind a reverse proxy the TCP peer is the proxy, not the user, so a naive peer
    IP makes every request share one bucket (users self-DoS, credential-stuffing
    defence becomes meaningless). When `trusted_proxy_ips` is configured and the peer
    is one of those proxies, the real client is taken from X-Forwarded-For: the
    right-most entry that is NOT itself a trusted proxy. We never trust XFF from an
    untrusted peer, and never take the left-most entry — both are client-spoofable,
    which would let an attacker forge IPs to bypass or frame the throttle.
    """
    if request.client is None:
        return None
    try:
        peer = ipaddress.ip_address(request.client.host)
    except ValueError:
        return None

    trusted = settings.trusted_proxy_networks
    if not trusted or not any(peer in net for net in trusted):
        # Direct connection, or a peer we don't recognise as our proxy: trust the
        # peer only, ignore any (spoofable) forwarded header.
        return request.client.host

    forwarded = request.headers.get("X-Forwarded-For")
    if not forwarded:
        return request.client.host
    for hop in reversed([part.strip() for part in forwarded.split(",") if part.strip()]):
        try:
            candidate = ipaddress.ip_address(hop)
        except ValueError:
            continue
        if not any(candidate in net for net in trusted):
            return str(candidate)
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


def _enforce_csrf_origin(request: Request, allowed_origins: list[str]) -> None:
    """Defense-in-depth on top of the double-submit token (spec §13): reject a
    cookie-authenticated state-changing request whose Origin (or, failing that,
    Referer) is present but not in the CORS allow-list. When neither header is
    present we fall through to the double-submit check — non-browser clients and
    some same-origin requests omit both, and the token still guards them."""
    origin = request.headers.get("Origin")
    if origin is not None:
        if origin not in allowed_origins:
            raise CsrfFailedError()
        return
    referer = request.headers.get("Referer")
    if referer is not None:
        parsed = urlsplit(referer)
        if f"{parsed.scheme}://{parsed.netloc}" not in allowed_origins:
            raise CsrfFailedError()


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
            ip=_client_ip(request, settings),
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
            ip=_client_ip(request, settings),
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
    _enforce_csrf_origin(request, settings.cors_allow_origins_list)
    now = datetime.now(UTC)
    issued = command.execute(
        RefreshInput(
            raw_refresh_token=request.cookies.get(_REFRESH_COOKIE),
            csrf_cookie=request.cookies.get(_CSRF_COOKIE),
            csrf_header=request.headers.get(_CSRF_HEADER),
            now=now,
            user_agent=request.headers.get("User-Agent"),
            ip=_client_ip(request, settings),
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
    settings: SettingsDep,
) -> None:
    # Always 202: never reveals whether the email exists.
    command.execute(
        PasswordResetRequestInput(
            email=body.email,
            now=datetime.now(UTC),
            ip=_client_ip(request, settings),
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
    settings: SettingsDep,
    response: Response,
) -> None:
    _enforce_csrf_origin(request, settings.cors_allow_origins_list)
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
def me(user: ActiveUserDep, users: UserRepoDep) -> MeResponse:
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
