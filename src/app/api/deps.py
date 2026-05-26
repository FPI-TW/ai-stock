from collections.abc import Callable, Generator
from typing import Annotated

from fastapi import Depends, Request, status
from sqlalchemy.orm import Session

from app.api.errors import ApiError, ErrorCode
from app.commands.notification import MarkNotificationReadCommand
from app.commands.trade_intent import CancelTradeIntentCommand, CreateTradeIntentCommand
from app.commands.trigger_intent import TriggerIntentCommand
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


def get_current_user(settings: SettingsDep) -> RequestUser:
    return build_local_user(settings)


CurrentUserDep = Annotated[RequestUser, Depends(get_current_user)]


def get_intent_repository(db: DatabaseDep) -> IntentRepository:
    return IntentRepository(db)


IntentRepoDep = Annotated[IntentRepository, Depends(get_intent_repository)]


def get_trading_session_service() -> TradingSessionService:
    return TradingSessionService()


TradingSessionServiceDep = Annotated[TradingSessionService, Depends(get_trading_session_service)]


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


def get_current_price_provider(
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
