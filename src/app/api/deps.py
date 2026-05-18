from collections.abc import Callable, Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import RequestUser, build_local_user
from app.db.session import check_database_connectivity, get_session_factory
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
