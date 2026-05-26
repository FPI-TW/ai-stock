from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services.quote.base import QuoteUnavailableError
from app.services.quote.shioaji_demo.client import ShioajiClient

TAIPEI = ZoneInfo("Asia/Taipei")


class FakeContracts:
    def __init__(self, contract: object) -> None:
        self.Stocks = {"2330": contract}


class FakeApi:
    def __init__(self, snapshots: list[object]) -> None:
        self.contract = object()
        self.Contracts = FakeContracts(self.contract)
        self.snapshots_payload = snapshots
        self.requested_contracts: list[object] | None = None

    def snapshots(self, contracts: list[object]) -> list[object]:
        self.requested_contracts = contracts
        return self.snapshots_payload


def _ts_ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def test_get_stock_snapshot_normalizes_shioaji_snapshot() -> None:
    quote_time_utc = datetime(2026, 5, 26, 2, 30, tzinfo=UTC)
    api = FakeApi(
        [
            SimpleNamespace(
                code="2330",
                buy_price=590.0,
                sell_price=591.0,
                close=590.5,
                ts=_ts_ns(quote_time_utc),
            )
        ]
    )
    client = ShioajiClient(api_key="dummy", secret_key="dummy", simulation=False)
    client._api = api

    snapshot = client.get_stock_snapshot("2330")

    assert api.requested_contracts == [api.contract]
    assert snapshot.symbol == "2330"
    assert snapshot.bid_price == Decimal("590.0")
    assert snapshot.ask_price == Decimal("591.0")
    assert snapshot.last_price == Decimal("590.5")
    assert snapshot.quote_time == datetime(2026, 5, 26, 10, 30, tzinfo=TAIPEI)
    assert snapshot.received_at.tzinfo is not None


def test_get_stock_snapshot_raises_when_shioaji_returns_no_snapshot() -> None:
    client = ShioajiClient(api_key="dummy", secret_key="dummy", simulation=False)
    client._api = FakeApi([])

    with pytest.raises(QuoteUnavailableError):
        client.get_stock_snapshot("2330")
