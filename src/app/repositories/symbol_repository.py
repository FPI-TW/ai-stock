from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models.core import Symbol

MAX_SEARCH_LIMIT = 50


class SymbolRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def find_by_symbol(self, symbol: str) -> Symbol | None:
        # symbol 保持原始字串，不做任何型別轉換
        return self.db.execute(select(Symbol).where(Symbol.symbol == symbol)).scalar_one_or_none()

    def search(self, q: str | None, limit: int) -> list[Symbol]:
        effective_limit = min(limit, MAX_SEARCH_LIMIT)
        stmt = select(Symbol)
        if q:
            stmt = stmt.where(
                or_(
                    Symbol.symbol.like(f"{q}%"),
                    Symbol.display_name.contains(q),
                )
            )
        return list(self.db.execute(stmt.limit(effective_limit)).scalars().all())
