from app.api.symbol_errors import (
    SymbolNotTradableError,
    UnknownSymbolError,
    UnsupportedInstrumentError,
)
from app.db.models.core import Symbol
from app.repositories.symbol_repository import SymbolRepository

# V0.5 接受的 instrument_type / tradable_status 取值（與 DB CHECK constraint 對齊）
INSTRUMENT_STOCK = "stock"
INSTRUMENT_ETF = "etf"
TRADABLE = "tradable"
SUPPORTED_INSTRUMENT_TYPES: frozenset[str] = frozenset({INSTRUMENT_STOCK, INSTRUMENT_ETF})


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
        2. instrument_type 屬於 stock|etf → 否則 UNSUPPORTED_INSTRUMENT
        3. tradable_status == tradable → 否則 SYMBOL_NOT_TRADABLE
        """
        symbol_obj = self.find_by_symbol(symbol)

        if symbol_obj.instrument_type not in SUPPORTED_INSTRUMENT_TYPES:
            raise UnsupportedInstrumentError(symbol_obj.instrument_type)

        if symbol_obj.tradable_status != TRADABLE:
            raise SymbolNotTradableError(symbol, symbol_obj.tradable_status)

        return symbol_obj

    def lookup(self, q: str | None, limit: int = 20) -> list[Symbol]:
        return self._repo.search(q, limit)
