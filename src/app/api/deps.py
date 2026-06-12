from collections.abc import Callable, Generator
from typing import Annotated

from argon2 import PasswordHasher
from fastapi import Depends, Request, status
from sqlalchemy.orm import Session

from app.api.errors import ApiError, ErrorCode
from app.commands.account import (
    AcceptInvitationCommand,
    CreateUserCommand,
    DisableUserCommand,
    ResendInvitationCommand,
)
from app.commands.auth import LoginCommand, LogoutCommand, RefreshCommand
from app.commands.intent_lifecycle import IntentLifecycleCommand
from app.commands.notification import MarkNotificationReadCommand
from app.commands.password_reset import PasswordResetConfirmCommand, PasswordResetRequestCommand
from app.commands.trade_intent import CancelTradeIntentCommand, CreateTradeIntentCommand
from app.commands.trigger_intent import TriggerIntentCommand
from app.commands.twap import TwapConfirmCommand, TwapPlanCommand, TwapSliceWorkerCommand
from app.commands.two_factor import SetupTwoFactorCommand, VerifyTwoFactorCommand
from app.core.config import REQUIRED_CURRENT_PRICE_PROVIDER, Settings, get_settings
from app.core.passwords import build_password_hasher
from app.core.rate_limiter import RateLimiter
from app.core.security import RequestUser
from app.core.tokens import InvalidAccessTokenError, decode_access_token
from app.db.session import check_database_connectivity, get_session_factory
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trading_session import TradingSessionService
from app.repositories.intent_repository import IntentRepository
from app.repositories.invitation_repository import InvitationRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.password_reset_repository import PasswordResetRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.symbol_repository import SymbolRepository
from app.repositories.user_repository import UserRepository
from app.services.audit import AuditEventWriter
from app.services.mailer import LoggingMailer, Mailer
from app.services.quote.base import QuoteProvider
from app.services.quote.current_price import CurrentPriceProvider
from app.services.symbol import SymbolService

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_database_health_checker() -> Callable[[], bool]:
    return check_database_connectivity


def get_db() -> Generator[Session]:
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


DatabaseDep = Annotated[Session, Depends(get_db)]


def get_symbol_service(db: DatabaseDep) -> SymbolService:
    return SymbolService(SymbolRepository(db))


SymbolServiceDep = Annotated[SymbolService, Depends(get_symbol_service)]


def get_current_user(request: Request, settings: SettingsDep) -> RequestUser:
    """Resolve the caller from the `Authorization: Bearer <access-jwt>` header.

    Missing / malformed / expired token -> 401 UNAUTHENTICATED. Production verifies
    expiry against the real clock (decode_access_token default).
    """
    header = request.headers.get("Authorization")
    if header is None or not header.startswith("Bearer "):
        raise ApiError(code=ErrorCode.UNAUTHENTICATED, status_code=status.HTTP_401_UNAUTHORIZED)
    token = header.removeprefix("Bearer ").strip()
    try:
        claims = decode_access_token(settings.resolved_jwt_access_secret, token)
    except InvalidAccessTokenError as exc:
        raise ApiError(code=ErrorCode.UNAUTHENTICATED, status_code=status.HTTP_401_UNAUTHORIZED) from exc
    return RequestUser(
        user_id=claims.sub,
        role=claims.role,
        session_id=claims.session_id,
        mfa_verified=claims.mfa_verified,
    )


CurrentUserDep = Annotated[RequestUser, Depends(get_current_user)]


def get_active_user(user: CurrentUserDep, db: DatabaseDep) -> RequestUser:
    """Like `get_current_user`, but additionally re-validates the session against the
    DB on every request. Access tokens are stateless JWTs valid until TTL, so logout /
    password reset / account disable cannot invalidate an already-issued token on their
    own — the principal would otherwise keep access for up to the token lifetime
    (≤15 min). Each of those flows revokes the session's refresh token, so we look it up
    by the access token's `session_id` and reject when it has been revoked for any reason
    other than normal rotation (`rotated` is benign — the successor token supersedes it).

    Disabled accounts -> 403 ACCOUNT_DISABLED (revoked_reason "account_disabled");
    every other revocation, and a missing session, -> 401 SESSION_REVOKED so the client
    re-authenticates.
    """
    token = RefreshTokenRepository(db).get_by_id(user.session_id)
    if token is None:
        raise ApiError(code=ErrorCode.SESSION_REVOKED, status_code=status.HTTP_401_UNAUTHORIZED)
    if token.revoked_at is not None and token.revoked_reason != "rotated":
        if token.revoked_reason == "account_disabled":
            raise ApiError(code=ErrorCode.ACCOUNT_DISABLED, status_code=status.HTTP_403_FORBIDDEN)
        raise ApiError(code=ErrorCode.SESSION_REVOKED, status_code=status.HTTP_401_UNAUTHORIZED)
    return user


ActiveUserDep = Annotated[RequestUser, Depends(get_active_user)]


def require_role(required_role: str) -> Callable[[RequestUser], RequestUser]:
    """Dependency factory: 403 FORBIDDEN unless the caller holds `required_role`.

    Layered on `ActiveUserDep` so admin-role endpoints also get session revocation
    (a logged-out / disabled admin is rejected before the role check runs)."""

    def _dependency(user: ActiveUserDep) -> RequestUser:
        if user.role != required_role:
            raise ApiError(code=ErrorCode.FORBIDDEN, status_code=status.HTTP_403_FORBIDDEN)
        return user

    return _dependency


def get_admin_user(user: ActiveUserDep) -> RequestUser:
    """Admin gate: must hold a live session, the admin role, AND a verified second
    factor. The `ActiveUserDep` layer revokes already-issued tokens on logout / disable
    / password reset before the role + mfa checks below.

    Until the 2FA verify flow (Phase 4) sets mfa_verified, admin endpoints answer
    403 MFA_REQUIRED — the deliberate "set up 2FA before using admin" gate (spec §13).
    """
    if user.role != "admin":
        raise ApiError(code=ErrorCode.FORBIDDEN, status_code=status.HTTP_403_FORBIDDEN)
    if not user.mfa_verified:
        raise ApiError(code=ErrorCode.MFA_REQUIRED, status_code=status.HTTP_403_FORBIDDEN)
    return user


AdminUserDep = Annotated[RequestUser, Depends(get_admin_user)]

# Admin role WITHOUT the 2FA requirement — for the 2FA enrolment/verify endpoints,
# which must be reachable before a factor is verified (chicken-and-egg otherwise).
_require_admin_role = require_role("admin")
AdminRoleDep = Annotated[RequestUser, Depends(_require_admin_role)]


def get_intent_repository(db: DatabaseDep) -> IntentRepository:
    return IntentRepository(db)


IntentRepoDep = Annotated[IntentRepository, Depends(get_intent_repository)]


def get_trading_session_service() -> TradingSessionService:
    return TradingSessionService()


TradingSessionServiceDep = Annotated[TradingSessionService, Depends(get_trading_session_service)]


def get_intent_lifecycle_command(
    intent_repo: IntentRepoDep,
    session_service: TradingSessionServiceDep,
) -> IntentLifecycleCommand:
    return IntentLifecycleCommand(intent_repo, session_service)


IntentLifecycleCommandDep = Annotated[IntentLifecycleCommand, Depends(get_intent_lifecycle_command)]


def get_quote_provider(request: Request) -> QuoteProvider:
    """Return the process-wide quote provider stored on app.state.

    `create_app()` instantiates the provider via the factory and stores it here
    so every request shares the same in-memory subscription / snapshot state.
    Tests substitute this dep with a `MagicMock` via `app.dependency_overrides`.
    """

    provider = getattr(request.app.state, "quote_provider", None)
    if provider is None:
        raise RuntimeError(
            "quote_provider is not initialised on app.state; "
            "check that create_app() ran and that QUOTE_PROVIDER is set."
        )
    return provider


QuoteProviderDep = Annotated[QuoteProvider, Depends(get_quote_provider)]

CURRENT_PRICE_ALLOWED_SYMBOLS: frozenset[str] = frozenset({"2330", "2317", "0050", "00878"})


def get_current_price_symbol(symbol: str) -> str:
    if symbol not in CURRENT_PRICE_ALLOWED_SYMBOLS:
        raise ApiError(
            code=ErrorCode.CURRENT_PRICE_SYMBOL_NOT_ALLOWED,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            details={"symbol": symbol, "allowed": sorted(CURRENT_PRICE_ALLOWED_SYMBOLS)},
        )
    return symbol


CurrentPriceSymbolDep = Annotated[str, Depends(get_current_price_symbol)]


def get_current_price_provider(
    _symbol: CurrentPriceSymbolDep,
    settings: SettingsDep,
    quote_provider: QuoteProviderDep,
) -> CurrentPriceProvider:
    if settings.quote_provider != REQUIRED_CURRENT_PRICE_PROVIDER:
        raise ApiError(
            code=ErrorCode.QUOTE_PROVIDER_UNAVAILABLE,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="此測試 API 依賴 broker demo quote provider，無法在其他 provider 下使用",
            details={"requiredProvider": REQUIRED_CURRENT_PRICE_PROVIDER, "currentProvider": settings.quote_provider},
        )
    if not isinstance(quote_provider, CurrentPriceProvider):
        raise ApiError(
            code=ErrorCode.QUOTE_PROVIDER_UNAVAILABLE,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="目前 quote provider 不支援測試查價能力",
            details={"requiredCapability": "current_price", "currentProvider": settings.quote_provider},
        )
    return quote_provider


CurrentPriceProviderDep = Annotated[CurrentPriceProvider, Depends(get_current_price_provider)]


def get_trigger_intent_command(db: DatabaseDep) -> TriggerIntentCommand:
    return TriggerIntentCommand(db)


TriggerIntentCommandDep = Annotated[TriggerIntentCommand, Depends(get_trigger_intent_command)]


def get_quote_evaluator(session_service: TradingSessionServiceDep) -> QuoteEvaluator:
    return QuoteEvaluator(session_service)


QuoteEvaluatorDep = Annotated[QuoteEvaluator, Depends(get_quote_evaluator)]


def get_create_trade_intent_command(
    symbol_service: SymbolServiceDep,
    session_service: TradingSessionServiceDep,
    intent_repo: IntentRepoDep,
    quote_provider: QuoteProviderDep,
    evaluator: QuoteEvaluatorDep,
    db: DatabaseDep,
) -> CreateTradeIntentCommand:
    return CreateTradeIntentCommand(
        symbol_service,
        session_service,
        intent_repo,
        quote_provider,
        evaluator,
        db,
    )


CreateTradeIntentCommandDep = Annotated[CreateTradeIntentCommand, Depends(get_create_trade_intent_command)]


def get_cancel_trade_intent_command(
    intent_repo: IntentRepoDep,
    quote_provider: QuoteProviderDep,
    db: DatabaseDep,
) -> CancelTradeIntentCommand:
    return CancelTradeIntentCommand(intent_repo, quote_provider, db)


CancelTradeIntentCommandDep = Annotated[CancelTradeIntentCommand, Depends(get_cancel_trade_intent_command)]


def get_notification_repository(db: DatabaseDep) -> NotificationRepository:
    return NotificationRepository(db)


NotificationRepoDep = Annotated[NotificationRepository, Depends(get_notification_repository)]


def get_mark_notification_read_command(
    repo: NotificationRepoDep,
    db: DatabaseDep,
) -> MarkNotificationReadCommand:
    return MarkNotificationReadCommand(repo, db)


MarkNotificationReadCommandDep = Annotated[MarkNotificationReadCommand, Depends(get_mark_notification_read_command)]


def get_twap_plan_command(
    symbol_service: SymbolServiceDep,
    session_service: TradingSessionServiceDep,
) -> TwapPlanCommand:
    return TwapPlanCommand(symbol_service, session_service)


TwapPlanCommandDep = Annotated[TwapPlanCommand, Depends(get_twap_plan_command)]


def get_twap_confirm_command(
    symbol_service: SymbolServiceDep,
    session_service: TradingSessionServiceDep,
    intent_repo: IntentRepoDep,
    quote_provider: QuoteProviderDep,
    db: DatabaseDep,
) -> TwapConfirmCommand:
    return TwapConfirmCommand(symbol_service, session_service, intent_repo, quote_provider, db)


TwapConfirmCommandDep = Annotated[TwapConfirmCommand, Depends(get_twap_confirm_command)]


def get_twap_slice_worker_command(
    db: DatabaseDep,
    quote_provider: QuoteProviderDep,
    session_service: TradingSessionServiceDep,
) -> TwapSliceWorkerCommand:
    return TwapSliceWorkerCommand(db, quote_provider, session_service)


TwapSliceWorkerCommandDep = Annotated[TwapSliceWorkerCommand, Depends(get_twap_slice_worker_command)]


# --- L1 auth wiring ---
# All of these wrap the same request-scoped DB session (DatabaseDep is cached per
# request), so a command and its repos share one transaction.


def get_user_repository(db: DatabaseDep) -> UserRepository:
    return UserRepository(db)


UserRepoDep = Annotated[UserRepository, Depends(get_user_repository)]


def get_refresh_token_repository(db: DatabaseDep) -> RefreshTokenRepository:
    return RefreshTokenRepository(db)


RefreshTokenRepoDep = Annotated[RefreshTokenRepository, Depends(get_refresh_token_repository)]


def get_audit_writer(db: DatabaseDep) -> AuditEventWriter:
    return AuditEventWriter(db)


AuditWriterDep = Annotated[AuditEventWriter, Depends(get_audit_writer)]


def get_rate_limiter(db: DatabaseDep) -> RateLimiter:
    return RateLimiter(db)


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def get_password_hasher(settings: SettingsDep) -> PasswordHasher:
    return build_password_hasher(settings)


PasswordHasherDep = Annotated[PasswordHasher, Depends(get_password_hasher)]


def get_login_command(
    db: DatabaseDep,
    settings: SettingsDep,
    users: UserRepoDep,
    refresh_tokens: RefreshTokenRepoDep,
    rate_limiter: RateLimiterDep,
    audit: AuditWriterDep,
    hasher: PasswordHasherDep,
) -> LoginCommand:
    return LoginCommand(db, settings, users, refresh_tokens, rate_limiter, audit, hasher)


LoginCommandDep = Annotated[LoginCommand, Depends(get_login_command)]


def get_refresh_command(
    db: DatabaseDep,
    settings: SettingsDep,
    users: UserRepoDep,
    refresh_tokens: RefreshTokenRepoDep,
    audit: AuditWriterDep,
) -> RefreshCommand:
    return RefreshCommand(db, settings, users, refresh_tokens, audit)


RefreshCommandDep = Annotated[RefreshCommand, Depends(get_refresh_command)]


def get_logout_command(
    db: DatabaseDep,
    refresh_tokens: RefreshTokenRepoDep,
    audit: AuditWriterDep,
) -> LogoutCommand:
    return LogoutCommand(db, refresh_tokens, audit)


LogoutCommandDep = Annotated[LogoutCommand, Depends(get_logout_command)]


def get_invitation_repository(db: DatabaseDep) -> InvitationRepository:
    return InvitationRepository(db)


InvitationRepoDep = Annotated[InvitationRepository, Depends(get_invitation_repository)]


def get_mailer() -> Mailer:
    return LoggingMailer()


MailerDep = Annotated[Mailer, Depends(get_mailer)]


def get_create_user_command(
    db: DatabaseDep,
    users: UserRepoDep,
    invitations: InvitationRepoDep,
    audit: AuditWriterDep,
    mailer: MailerDep,
) -> CreateUserCommand:
    return CreateUserCommand(db, users, invitations, audit, mailer)


CreateUserCommandDep = Annotated[CreateUserCommand, Depends(get_create_user_command)]


def get_disable_user_command(
    db: DatabaseDep,
    users: UserRepoDep,
    intents: IntentRepoDep,
    refresh_tokens: RefreshTokenRepoDep,
    audit: AuditWriterDep,
) -> DisableUserCommand:
    return DisableUserCommand(db, users, intents, refresh_tokens, audit)


DisableUserCommandDep = Annotated[DisableUserCommand, Depends(get_disable_user_command)]


def get_resend_invitation_command(
    db: DatabaseDep,
    users: UserRepoDep,
    invitations: InvitationRepoDep,
    rate_limiter: RateLimiterDep,
    audit: AuditWriterDep,
    mailer: MailerDep,
) -> ResendInvitationCommand:
    return ResendInvitationCommand(db, users, invitations, rate_limiter, audit, mailer)


ResendInvitationCommandDep = Annotated[ResendInvitationCommand, Depends(get_resend_invitation_command)]


def get_setup_two_factor_command(
    db: DatabaseDep,
    settings: SettingsDep,
    users: UserRepoDep,
    audit: AuditWriterDep,
) -> SetupTwoFactorCommand:
    return SetupTwoFactorCommand(db, settings, users, audit)


SetupTwoFactorCommandDep = Annotated[SetupTwoFactorCommand, Depends(get_setup_two_factor_command)]


def get_verify_two_factor_command(
    db: DatabaseDep,
    settings: SettingsDep,
    users: UserRepoDep,
    refresh_tokens: RefreshTokenRepoDep,
    rate_limiter: RateLimiterDep,
    audit: AuditWriterDep,
) -> VerifyTwoFactorCommand:
    return VerifyTwoFactorCommand(db, settings, users, refresh_tokens, rate_limiter, audit)


VerifyTwoFactorCommandDep = Annotated[VerifyTwoFactorCommand, Depends(get_verify_two_factor_command)]


def get_accept_invitation_command(
    db: DatabaseDep,
    settings: SettingsDep,
    users: UserRepoDep,
    invitations: InvitationRepoDep,
    refresh_tokens: RefreshTokenRepoDep,
    audit: AuditWriterDep,
    hasher: PasswordHasherDep,
) -> AcceptInvitationCommand:
    return AcceptInvitationCommand(db, settings, users, invitations, refresh_tokens, audit, hasher)


AcceptInvitationCommandDep = Annotated[AcceptInvitationCommand, Depends(get_accept_invitation_command)]


def get_password_reset_repository(db: DatabaseDep) -> PasswordResetRepository:
    return PasswordResetRepository(db)


PasswordResetRepoDep = Annotated[PasswordResetRepository, Depends(get_password_reset_repository)]


def get_password_reset_request_command(
    db: DatabaseDep,
    users: UserRepoDep,
    password_resets: PasswordResetRepoDep,
    rate_limiter: RateLimiterDep,
    audit: AuditWriterDep,
    mailer: MailerDep,
) -> PasswordResetRequestCommand:
    return PasswordResetRequestCommand(db, users, password_resets, rate_limiter, audit, mailer)


PasswordResetRequestCommandDep = Annotated[PasswordResetRequestCommand, Depends(get_password_reset_request_command)]


def get_password_reset_confirm_command(
    db: DatabaseDep,
    users: UserRepoDep,
    password_resets: PasswordResetRepoDep,
    refresh_tokens: RefreshTokenRepoDep,
    audit: AuditWriterDep,
    hasher: PasswordHasherDep,
) -> PasswordResetConfirmCommand:
    return PasswordResetConfirmCommand(db, users, password_resets, refresh_tokens, audit, hasher)


PasswordResetConfirmCommandDep = Annotated[PasswordResetConfirmCommand, Depends(get_password_reset_confirm_command)]
