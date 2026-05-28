"""Assembles `QuoteRead` records from the QuoteProvider snapshot cache.

Pure synchronous logic: validate the request shape, look up symbol metadata
through SymbolService (raises UnknownSymbolError for caller to map to 404),
then ask the QuoteProvider for snapshots. Missing snapshots become
`stale=True` rows instead of failing the whole batch — matches the spec's
batch read semantics.
"""

from app.api.errors import ApiError, ErrorCode
from app.db.models.core import Symbol
from app.schemas.quote import QuoteRead
from app.services.quote.base import QuoteProvider, QuoteSnapshot, QuoteUnavailableError
from app.services.symbol import SymbolService

MAX_SYMBOLS = 50


class QuoteLookupService:
    def __init__(self, symbols: SymbolService, provider: QuoteProvider) -> None:
        self._symbols = symbols
        self._provider = provider

    def lookup(self, raw_symbols: str | None) -> list[QuoteRead]:
        codes = self._parse(raw_symbols)
        meta = {code: self._symbols.get_by_symbol(code) for code in codes}
        snapshots = self._fetch_snapshots(codes)
        return [self._to_read(code, meta[code], snapshots.get(code)) for code in codes]

    @staticmethod
    def _parse(raw: str | None) -> list[str]:
        if raw is None or not raw.strip():
            raise ApiError(ErrorCode.MISSING_SYMBOLS, status_code=400)
        codes = [c.strip() for c in raw.split(",") if c.strip()]
        if not codes:
            raise ApiError(ErrorCode.MISSING_SYMBOLS, status_code=400)
        if len(codes) > MAX_SYMBOLS:
            raise ApiError(
                ErrorCode.TOO_MANY_SYMBOLS,
                status_code=400,
                details={"limit": MAX_SYMBOLS, "received": len(codes)},
            )
        return codes

    def _fetch_snapshots(self, codes: list[str]) -> dict[str, QuoteSnapshot | None]:
        snapshots: dict[str, QuoteSnapshot | None] = {}
        for code in codes:
            try:
                [snap] = self._provider.get_quotes([code])
            except QuoteUnavailableError:
                snapshots[code] = None
            else:
                snapshots[code] = snap
        return snapshots

    @staticmethod
    def _to_read(code: str, meta: Symbol, snapshot: QuoteSnapshot | None) -> QuoteRead:
        if snapshot is None:
            return QuoteRead(symbol=code, display_name=meta.display_name, stale=True)
        return QuoteRead(
            symbol=code,
            display_name=meta.display_name,
            ask_price=snapshot.ask_price,
            bid_price=snapshot.bid_price,
            last_price=snapshot.last_price,
            quote_time=snapshot.quote_time,
            stale=False,
        )
