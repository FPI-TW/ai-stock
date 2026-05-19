from collections.abc import Callable, Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.commands.trade_intent import CancelTradeIntentCommand, CreateTradeIntentCommand
from app.core.config import Settings, get_settings
from app.core.security import RequestUser, build_local_user
from app.db.session import check_database_connectivity, get_session_factory
from app.domain.trading_session import TradingSessionService
from app.repositories.intent_repository import IntentRepository
from app.repositories.symbol_repository import SymbolRepository
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


def get_create_trade_intent_command(
    symbol_service: SymbolServiceDep,
    session_service: TradingSessionServiceDep,
    intent_repo: IntentRepoDep,
) -> CreateTradeIntentCommand:
    return CreateTradeIntentCommand(symbol_service, session_service, intent_repo)


CreateTradeIntentCommandDep = Annotated[CreateTradeIntentCommand, Depends(get_create_trade_intent_command)]


def get_cancel_trade_intent_command(intent_repo: IntentRepoDep) -> CancelTradeIntentCommand:
    return CancelTradeIntentCommand(intent_repo)


CancelTradeIntentCommandDep = Annotated[CancelTradeIntentCommand, Depends(get_cancel_trade_intent_command)]
