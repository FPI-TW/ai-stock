from app.db.models.core import Symbol
from app.domain.symbol_errors import SymbolNotTradableError, UnknownSymbolError
from app.repositories.symbol_repository import SymbolRepository

TRADABLE = "tradable"


class SymbolService:
    def __init__(self, repo: SymbolRepository) -> None:
        self._repo = repo

    def get_by_symbol(self, symbol: str) -> Symbol:
        """回傳必然存在的 Symbol；不存在時 raise UnknownSymbolError。"""
        symbol_obj = self._repo.find_by_symbol(symbol)
        if symbol_obj is None:
            raise UnknownSymbolError(symbol)
        return symbol_obj

    def get_tradable_symbol(self, symbol: str) -> Symbol:
        """
        驗證順序：
        1. 存在 → 否則 raise UnknownSymbolError
        2. tradable_status == tradable → 否則 raise SymbolNotTradableError

        instrument_type 的合法值由 DB CHECK constraint 強制保證（stock | etf），
        Service 層不重複防護。
        """
        symbol_obj = self.get_by_symbol(symbol)

        if symbol_obj.tradable_status != TRADABLE:
            raise SymbolNotTradableError(symbol, symbol_obj.tradable_status)

        return symbol_obj

    def lookup(self, q: str | None, limit: int = 20) -> list[Symbol]:
        return self._repo.search(q, limit)
