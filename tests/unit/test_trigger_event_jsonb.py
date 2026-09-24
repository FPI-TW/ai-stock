"""`quote_snapshot_to_jsonb` 的稽核序列化契約。

純函式，不碰 DB。兩個時間都必須進 JSONB：evaluator 是用 `last_trade_time` 決定
`last_fallback` 觸發成不成立的（BUG-013），稽核紀錄少了它就無法重建當時的判斷。
"""

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.domain.trigger_event import quote_snapshot_to_jsonb
from app.services.quote.base import QuoteSnapshot

TAIPEI = ZoneInfo("Asia/Taipei")
FRAME_TIME = datetime(2026, 5, 11, 10, 0, tzinfo=TAIPEI)
TRADE_TIME = datetime(2026, 5, 11, 9, 30, tzinfo=TAIPEI)


def _snapshot(*, last_trade_time: datetime | None, last_price: Decimal | None) -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol="2330",
        bid_price=Decimal("99.0000"),
        ask_price=Decimal("99.5000"),
        last_price=last_price,
        quote_time=FRAME_TIME,
        last_trade_time=last_trade_time,
        received_at=datetime(2026, 5, 11, 2, 0, tzinfo=UTC),
    )


def test_both_times_are_serialised_in_taipei() -> None:
    payload = quote_snapshot_to_jsonb(_snapshot(last_trade_time=TRADE_TIME, last_price=Decimal("99.2000")))

    assert payload["quote_time"] == "2026-05-11T10:00:00+08:00"
    assert payload["last_trade_time"] == "2026-05-11T09:30:00+08:00"
    assert payload["last_price"] == "99.2000"


def test_book_only_frame_records_a_null_trade_time_not_a_missing_key() -> None:
    # Before the first match of the day a thin stock has a book but no trade.
    # The key must exist and be null, so an auditor can tell "no trade" apart
    # from "written before this key existed".
    payload = quote_snapshot_to_jsonb(_snapshot(last_trade_time=None, last_price=None))

    assert payload["last_trade_time"] is None
    assert payload["quote_time"] == "2026-05-11T10:00:00+08:00"
    assert payload["last_price"] is None
