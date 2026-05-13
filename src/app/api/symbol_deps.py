"""Symbol feature 專用的 FastAPI dependencies。

main 上的 app.api.deps 只暴露 health checker，這裡補上 request-scoped
DB session 與 SymbolService 注入。
"""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.symbol_session import get_session_factory
from app.repositories.symbol_repository import SymbolRepository
from app.services.symbol import SymbolService


def get_db() -> Generator[Session]:
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


DatabaseDep = Annotated[Session, Depends(get_db)]


def get_symbol_service(db: DatabaseDep) -> SymbolService:
    return SymbolService(SymbolRepository(db))


SymbolServiceDep = Annotated[SymbolService, Depends(get_symbol_service)]
