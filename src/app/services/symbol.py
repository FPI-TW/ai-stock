from app.api.symbol_errors import SymbolNotTradableError, UnknownSymbolError
from app.db.models.core import Symbol
from app.repositories.symbol_repository import SymbolRepository

TRADABLE = "tradable"


class SymbolService:
    def __init__(self, repo: SymbolRepository) -> None:
        self._repo = repo

    def find_by_symbol(self, symbol: str) -> Symbol:
        """只檢查存在性，給 lookup 端點使用。Unknown 時拋 UNKNOWN_SYMBOL。"""
        symbol_obj = self._repo.find_by_symbol(symbol)
        if symbol_obj is None:
            raise UnknownSymbolError(symbol)
        return symbol_obj

    def get_tradable_symbol(self, symbol: str) -> Symbol:
        """
        驗證順序：
        1. 存在 → 否則 UNKNOWN_SYMBOL
        2. tradable_status == tradable → 否則 SYMBOL_NOT_TRADABLE

        instrument_type 的合法值由 DB CHECK constraint 強制保證（stock | etf），
        Service 層不重複防護。
        """
        symbol_obj = self.find_by_symbol(symbol)

        if symbol_obj.tradable_status != TRADABLE:
            raise SymbolNotTradableError(symbol, symbol_obj.tradable_status)

        return symbol_obj

    def lookup(self, q: str | None, limit: int = 20) -> list[Symbol]:
        return self._repo.search(q, limit)
