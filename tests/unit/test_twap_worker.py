from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.commands.twap import TwapSliceWorkerCommand
from app.db.models.core import Notification, TradeIntent, TwapSlice
from app.domain.trading_session import TradingSessionService
from app.services.quote.base import QuoteListener, QuoteSnapshot, QuoteUnavailableError

TAIPEI = ZoneInfo("Asia/Taipei")
UTC = ZoneInfo("UTC")
OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 5, 28, 10, 0, 0, tzinfo=TAIPEI)
QUOTE_TIME = datetime(2026, 5, 28, 9, 59, 58, tzinfo=TAIPEI)


class FakeResult:
    def __init__(
        self,
        *,
        rows: list[tuple[TwapSlice, TradeIntent]] | None = None,
        scalar: int | None = None,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def tuples(self) -> "FakeResult":
        return self

    def all(self) -> list[tuple[TwapSlice, TradeIntent]]:
        return self._rows

    def scalar_one(self) -> int:
        if self._scalar is None:
            raise AssertionError("scalar_one called on row result")
        return self._scalar


class FakeSession:
    def __init__(self, results: list[FakeResult]) -> None:
        self.results = results
        self.added_notifications: list[Notification] = []
        self.flushed_count = 0
        self.committed = False
        self.rolled_back = False

    def execute(self, _stmt: object) -> FakeResult:
        if not self.results:
            raise AssertionError("unexpected execute")
        return self.results.pop(0)

    def add(self, row: object) -> None:
        if isinstance(row, Notification):
            self.added_notifications.append(row)

    def flush(self) -> None:
        self.flushed_count += 1

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class FakeQuoteProvider:
    def __init__(self, snapshot: QuoteSnapshot | None) -> None:
        self._snapshot = snapshot

    def get_quotes(self, symbols: list[str]) -> list[QuoteSnapshot]:
        if self._snapshot is None:
            raise QuoteUnavailableError(symbols[0])
        return [self._snapshot]

    def subscribe(self, _symbol: str) -> None:
        return None

    def unsubscribe(self, _symbol: str) -> None:
        return None

    def active_subscriptions(self) -> set[str]:
        return set()

    def startup(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def add_quote_listener(self, _listener: QuoteListener) -> None:
        return None

    def remove_quote_listener(self, _listener: QuoteListener) -> None:
        return None


def _snapshot(*, bid: str | None = "589", ask: str | None = "591", last: str | None = "590") -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol="2330",
        bid_price=Decimal(bid) if bid is not None else None,
        ask_price=Decimal(ask) if ask is not None else None,
        last_price=Decimal(last) if last is not None else None,
        quote_time=QUOTE_TIME,
        received_at=QUOTE_TIME.astimezone(UTC),
    )


def _intent(*, status: str = "active", position_side: str = "long") -> TradeIntent:
    return TradeIntent(
        id=uuid4(),
        owner_user_id=OWNER_ID,
        symbol="2330",
        strategy="twap_order",
        execution_mode="notify_only",
        quantity_lots=2,
        target_price_original=None,
        target_price_effective=None,
        trigger_reference_price_type="last_fallback",
        trading_date=date(2026, 5, 28),
        time_in_force="day",
        status=status,
        transaction_mode="single_notification",
        notification_mode="single",
        position_side=position_side,
        twap_interval_seconds=60,
        twap_start_at=NOW,
        twap_end_at=NOW + timedelta(minutes=1),
        twap_available_slice_count=2,
        twap_materialized_slice_count=2,
    )


def _slice(
    intent: TradeIntent,
    *,
    sequence_no: int = 2,
    status: str = "pending",
    attempts: int = 0,
) -> TwapSlice:
    return TwapSlice(
        id=uuid4(),
        trade_intent_id=intent.id,
        owner_user_id=OWNER_ID,
        symbol="2330",
        sequence_no=sequence_no,
        scheduled_at=NOW,
        planned_quantity_lots=1,
        status=status,
        price_followup_required=True,
        price_followup_attempts=attempts,
        next_price_followup_at=NOW,
    )


def _worker(fake_db: FakeSession, provider: FakeQuoteProvider) -> TwapSliceWorkerCommand:
    return TwapSliceWorkerCommand(
        db=cast(Session, fake_db),
        quote_provider=provider,
        session_service=TradingSessionService(clock=lambda: NOW),
    )


def test_process_due_slice_with_price_creates_primary_notification_and_completes_intent() -> None:
    intent = _intent(status="active", position_side="long")
    slice_row = _slice(intent, status="pending")
    fake_db = FakeSession([FakeResult(rows=[(slice_row, intent)]), FakeResult(scalar=0)])

    output = _worker(fake_db, FakeQuoteProvider(_snapshot())).process_due_slices()

    assert output.processed_count == 1
    [notification] = fake_db.added_notifications
    assert notification.type == "twap_slice"
    assert notification.trade_intent_id == intent.id
    assert "參考價：591.00（ask）" in notification.rendered_body
    assert slice_row.status == "notified"
    assert slice_row.primary_price_available is True
    assert slice_row.primary_reference_price == Decimal("591")
    assert slice_row.primary_reference_price_type == "ask"
    assert slice_row.price_followup_required is False
    assert slice_row.next_price_followup_at is None
    assert intent.status == "triggered"
    assert intent.triggered_at == NOW
    assert fake_db.committed is True


def test_process_due_slice_without_price_sends_primary_notification_and_schedules_followup() -> None:
    intent = _intent(status="scheduled", position_side="short")
    slice_row = _slice(intent, status="pending")
    fake_db = FakeSession([FakeResult(rows=[(slice_row, intent)]), FakeResult(scalar=1)])

    output = _worker(fake_db, FakeQuoteProvider(None)).process_due_slices()

    assert output.processed_count == 1
    [notification] = fake_db.added_notifications
    assert notification.type == "twap_slice"
    assert "目前行情暫不可用，請自行確認市價。" in notification.rendered_body
    assert slice_row.status == "notified"
    assert slice_row.primary_price_available is False
    assert slice_row.primary_reference_price is None
    assert slice_row.price_followup_required is True
    assert slice_row.next_price_followup_at == NOW + timedelta(seconds=10)
    assert intent.status == "active"
    assert intent.triggered_at is None
    assert fake_db.committed is True


def test_process_price_followup_success_creates_followup_notification_and_clears_retry_state() -> None:
    intent = _intent(position_side="short")
    slice_row = _slice(intent, status="notified", attempts=1)
    fake_db = FakeSession([FakeResult(rows=[(slice_row, intent)])])

    output = _worker(fake_db, FakeQuoteProvider(_snapshot())).process_price_followups()

    assert output.processed_count == 1
    [notification] = fake_db.added_notifications
    assert notification.type == "twap_price_followup"
    assert "第 2/2 筆參考價：589.00（bid）" in notification.rendered_body
    assert "補發時間：2026-05-28 10:00:00" in notification.rendered_body
    assert slice_row.price_followup_attempts == 2
    assert slice_row.price_followup_required is False
    assert slice_row.next_price_followup_at is None
    assert slice_row.price_followup_notification_id == notification.id
    assert slice_row.price_followup_sent_at == NOW
    assert fake_db.committed is True


def test_process_price_followup_failure_retries_then_stops_at_third_attempt() -> None:
    intent = _intent(position_side="long")
    retry_slice = _slice(intent, status="notified", attempts=1)
    stop_slice = _slice(intent, status="notified", attempts=2)
    fake_db = FakeSession([FakeResult(rows=[(retry_slice, intent), (stop_slice, intent)])])

    output = _worker(fake_db, FakeQuoteProvider(None)).process_price_followups()

    assert output.processed_count == 2
    assert fake_db.added_notifications == []
    assert retry_slice.price_followup_attempts == 2
    assert retry_slice.price_followup_required is True
    assert retry_slice.next_price_followup_at == NOW + timedelta(seconds=10)
    assert stop_slice.price_followup_attempts == 3
    assert stop_slice.price_followup_required is False
    assert stop_slice.next_price_followup_at is None
    assert fake_db.committed is True
