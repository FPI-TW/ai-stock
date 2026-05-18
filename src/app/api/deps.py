from collections.abc import Callable, Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import RequestUser, build_local_user
from app.db.session import check_database_connectivity, get_session_factory
from app.domain.quote import QuoteValidator
from app.domain.trading_session import TradingSessionService
from app.repositories.symbol_repository import SymbolRepository
from app.services.quote import (
    DevelopmentQuoteProvider,
    DevQuoteIngestService,
    InMemoryQuoteStore,
    get_dev_quote_store,
)
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


def get_quote_store() -> InMemoryQuoteStore:
    return get_dev_quote_store()


QuoteStoreDep = Annotated[InMemoryQuoteStore, Depends(get_quote_store)]


def get_quote_provider(store: QuoteStoreDep) -> DevelopmentQuoteProvider:
    return DevelopmentQuoteProvider(store)


QuoteProviderDep = Annotated[DevelopmentQuoteProvider, Depends(get_quote_provider)]


def get_trading_session_service() -> TradingSessionService:
    return TradingSessionService()


TradingSessionServiceDep = Annotated[TradingSessionService, Depends(get_trading_session_service)]


def get_quote_validator(session: TradingSessionServiceDep) -> QuoteValidator:
    return QuoteValidator(session)


QuoteValidatorDep = Annotated[QuoteValidator, Depends(get_quote_validator)]


def get_dev_quote_ingest_service(
    symbol_service: SymbolServiceDep,
    validator: QuoteValidatorDep,
    provider: QuoteProviderDep,
) -> DevQuoteIngestService:
    return DevQuoteIngestService(symbol_service, validator, provider)


DevQuoteIngestServiceDep = Annotated[DevQuoteIngestService, Depends(get_dev_quote_ingest_service)]
