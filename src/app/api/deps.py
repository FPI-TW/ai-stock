from collections.abc import Callable, Generator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request, status
from sqlalchemy.orm import Session

from app.api.errors import ApiError, ErrorCode
from app.commands.notification import MarkNotificationReadCommand
from app.commands.trade_intent import CancelTradeIntentCommand, CreateTradeIntentCommand
from app.commands.trigger_intent import TriggerIntentCommand
from app.commands.twap import TwapConfirmCommand, TwapPlanCommand, TwapSliceWorkerCommand
from app.core.config import REQUIRED_CURRENT_PRICE_PROVIDER, Settings, get_settings
from app.core.security import RequestUser, build_local_user
from app.db.session import check_database_connectivity, get_session_factory
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trading_session import TradingSessionService
from app.repositories.intent_repository import IntentRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.symbol_repository import SymbolRepository
from app.services.quote.base import QuoteProvider
from app.services.quote.current_price import CurrentPriceProvider
from app.services.quote_lookup import QuoteLookupService
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


def get_current_user(
    settings: SettingsDep,
    x_local_user_id: Annotated[UUID | None, Header(alias="X-Local-User-Id")] = None,
) -> RequestUser:
    """Resolve the request's owner.

    Production (``LOCAL_MODE=false``): always the configured ``LOCAL_USER_ID``;
    the ``X-Local-User-Id`` header is silently ignored so a public deployment
    cannot impersonate users by sending the header.

    LOCAL_MODE: if the ``X-Local-User-Id`` header is present (and parses as
    a UUID — FastAPI auto-returns 422 otherwise), use it as the request
    owner. Used by the ``/test`` page to demo multi-user owner scoping
    without any auth infrastructure.
    """
    if settings.local_mode and x_local_user_id is not None:
        return RequestUser(user_id=x_local_user_id, role="local")
    return build_local_user(settings)


CurrentUserDep = Annotated[RequestUser, Depends(get_current_user)]


def get_intent_repository(db: DatabaseDep) -> IntentRepository:
    return IntentRepository(db)


IntentRepoDep = Annotated[IntentRepository, Depends(get_intent_repository)]


def get_trading_session_service(request: Request) -> TradingSessionService:
    """Return the process-wide ``TradingSessionService`` stored on app.state.

    ``create_app()`` instantiates it once so the API path, lifespan
    dispatcher, and ``/dev/set-clock`` override all share the same clock.
    Falls back to a fresh instance if no app.state entry exists (some unit
    tests build a bare ``FastAPI`` without ``create_app``).
    """
    svc = getattr(request.app.state, "session_service", None)
    if svc is None:
        svc = TradingSessionService()
        request.app.state.session_service = svc
    return svc


TradingSessionServiceDep = Annotated[TradingSessionService, Depends(get_trading_session_service)]


def get_quote_provider(request: Request) -> QuoteProvider:
    return request.app.state.quote_provider


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


def get_quote_lookup_service(
    symbols: SymbolServiceDep,
    provider: QuoteProviderDep,
) -> QuoteLookupService:
    return QuoteLookupService(symbols, provider)


QuoteLookupServiceDep = Annotated[QuoteLookupService, Depends(get_quote_lookup_service)]
