"""Trigger transaction command — BE-V0.5-09.

Atomically transitions an `active` intent to `triggered` while writing the
trigger snapshot and a `price_triggered` notification, all in a single DB
transaction.

Concurrency safety (spec §6 trigger / cancel race):
- `SELECT ... FOR UPDATE` holds the intent row until commit.
- Python-level status guard catches the common case early.
- The intent `UPDATE` carries a `WHERE status='active'` clause as a defense
  in depth in case the lock semantics are ever weakened.
- `trigger_events.trade_intent_id UNIQUE` is the final backstop — if it ever
  fires we surface `DuplicateTriggerError` so callers can distinguish
  contention from real bugs.

Two callers share this module:

- `TriggerIntentCommand` — self-contained transaction for dispatcher /
  dev-evaluate paths where the intent already exists and may be raced.
- `CreateTradeIntentCommand` — uses the lower-level `persist_trigger` helper
  to fold an immediate trigger into the create transaction. The just-flushed
  intent row is invisible to other sessions until commit, so no lock is
  needed.

V1 upgrade seam (spec §16): the inline `notifications` insert here will be
replaced by an outbox row + worker. Keep notification rendering routed
through `render_price_triggered` so swapping the persistence path does not
ripple back into the evaluator.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.core import Notification as NotificationRow
from app.db.models.core import TradeIntent
from app.db.models.core import TriggerEvent as TriggerEventRow
from app.domain.notification import NotificationData
from app.domain.trade_intent import IntentNotFoundError
from app.domain.trigger_event import DuplicateTriggerError, TriggerError, TriggerEventData
from app.services.notification_template import (
    render_limit_order_triggered,
    render_price_triggered,
)

# Strategy → notification type. Centralised so persist_trigger only branches
# in one place when a new strategy lands.
_NOTIFICATION_TYPE_BY_STRATEGY: dict[str, str] = {
    "buy_price_alert": "price_triggered",
    "sell_price_alert": "price_triggered",
    "limit_buy_order": "limit_order_triggered",
    "limit_sell_order": "limit_order_triggered",
}


class IntentNotActiveError(TriggerError):
    def __init__(self, intent_id: UUID, current_status: str) -> None:
        self.intent_id = intent_id
        self.current_status = current_status
        super().__init__(f"Intent {intent_id} cannot be triggered: status is {current_status!r}")


@dataclass(frozen=True)
class TriggerIntentInput:
    intent_id: UUID
    trigger_price: Decimal
    trigger_reference_price_type: str
    fallback_used: bool
    quote_snapshot: dict[str, Any]
    quote_time: datetime


@dataclass(frozen=True)
class TriggerIntentOutput:
    trigger_event: TriggerEventData
    notification: NotificationData


def _trigger_event_to_domain(row: TriggerEventRow) -> TriggerEventData:
    return TriggerEventData(
        id=row.id,
        trade_intent_id=row.trade_intent_id,
        owner_user_id=row.owner_user_id,
        symbol=row.symbol,
        quote_snapshot=row.quote_snapshot,
        target_price_effective=row.target_price_effective,
        trigger_price=row.trigger_price,
        trigger_reference_price_type=row.trigger_reference_price_type,
        fallback_used=row.fallback_used,
        filled_quantity_lots=row.filled_quantity_lots,
        triggered_at=row.triggered_at,
        created_at=row.created_at,
    )


def _notification_to_domain(row: NotificationRow) -> NotificationData:
    return NotificationData(
        id=row.id,
        owner_user_id=row.owner_user_id,
        type=row.type,
        rendered_title=row.rendered_title,
        rendered_body=row.rendered_body,
        created_at=row.created_at,
        updated_at=row.updated_at,
        trade_intent_id=row.trade_intent_id,
        read_at=row.read_at,
    )


def persist_trigger(
    db: Session,
    intent: TradeIntent,
    inp: TriggerIntentInput,
) -> tuple[TriggerEventRow, NotificationRow]:
    """Write trigger_event + notification + intent status update — no commit.

    Pure persistence helper shared by `TriggerIntentCommand` (with lock, for
    existing intents) and `CreateTradeIntentCommand` (no lock, for the
    just-flushed intent in the same transaction). Caller decides when to
    commit and how to translate `IntegrityError`.

    `triggered_at` left unset on both rows / UPDATE values — the DB fills it
    via `func.now()` (server_default on trigger_events, explicit on the
    UPDATE). In a single PostgreSQL transaction every NOW() call returns the
    transaction-start timestamp, so the two columns end up with identical
    values without us tracking them in Python.
    """

    notification_type = _NOTIFICATION_TYPE_BY_STRATEGY.get(intent.strategy)
    if notification_type is None:
        raise ValueError(f"Unsupported strategy for trigger: {intent.strategy!r}")

    # V0.5 treats every trigger as a full fill — broker integration / partial
    # fill accumulation arrives in V2. Computing this once keeps the trigger
    # event, intent update, and notification body in lockstep.
    filled_quantity_lots = intent.quantity_lots

    if notification_type == "limit_order_triggered":
        title, body = render_limit_order_triggered(
            symbol=intent.symbol,
            strategy=intent.strategy,
            target_price=intent.target_price_effective,
            trigger_price=inp.trigger_price,
            quote_time=inp.quote_time,
            quantity_lots=intent.quantity_lots,
            filled_quantity_lots=filled_quantity_lots,
        )
    else:
        title, body = render_price_triggered(
            symbol=intent.symbol,
            strategy=intent.strategy,
            target_price=intent.target_price_effective,
            trigger_price=inp.trigger_price,
            quote_time=inp.quote_time,
        )

    trigger_row = TriggerEventRow(
        id=uuid4(),
        trade_intent_id=intent.id,
        owner_user_id=intent.owner_user_id,
        symbol=intent.symbol,
        quote_snapshot=inp.quote_snapshot,
        target_price_effective=intent.target_price_effective,
        trigger_price=inp.trigger_price,
        trigger_reference_price_type=inp.trigger_reference_price_type,
        fallback_used=inp.fallback_used,
        filled_quantity_lots=filled_quantity_lots,
    )
    notification_row = NotificationRow(
        id=uuid4(),
        owner_user_id=intent.owner_user_id,
        trade_intent_id=intent.id,
        type=notification_type,
        rendered_title=title,
        rendered_body=body,
    )

    db.add(trigger_row)
    # `Session.execute(update_stmt)` returns a `CursorResult` at runtime, but
    # SQLAlchemy's stubs widen the return type to `Result`, which lacks
    # `.rowcount`. Cast to narrow rather than chase the wider API.
    result = cast(
        CursorResult[Any],
        db.execute(
            update(TradeIntent)
            .where(TradeIntent.id == intent.id, TradeIntent.status == "active")
            .values(
                status="triggered",
                triggered_at=func.now(),
                updated_at=func.now(),
                filled_quantity_lots=filled_quantity_lots,
                last_fill_at=func.now(),
            ),
        ),
    )
    # Defense-in-depth: if rowcount is 0 the status guard was bypassed
    # (lock skipped, status changed underneath, or some future caller
    # forgot the SELECT FOR UPDATE). Raise so the caller rolls back the
    # already-staged trigger_event / notification rather than letting them
    # commit against an intent whose status is no longer active.
    if result.rowcount == 0:
        raise IntentNotActiveError(intent.id, "stale")
    db.add(notification_row)
    return trigger_row, notification_row


class TriggerIntentCommand:
    def __init__(self, db: Session) -> None:
        self._db = db

    def execute(self, inp: TriggerIntentInput) -> TriggerIntentOutput:
        intent = self._db.execute(
            select(TradeIntent).where(TradeIntent.id == inp.intent_id).with_for_update()
        ).scalar_one_or_none()
        if intent is None:
            raise IntentNotFoundError(inp.intent_id)
        if intent.status != "active":
            raise IntentNotActiveError(inp.intent_id, intent.status)

        # Wrap the whole write sequence: autoflush on `execute(update(...))`
        # can surface the UNIQUE violation before commit, so a commit-only
        # try/except would miss it.
        try:
            trigger_row, notification_row = persist_trigger(self._db, intent, inp)
            self._db.commit()
            self._db.refresh(trigger_row)
            self._db.refresh(notification_row)
        except IntegrityError as exc:
            self._db.rollback()
            raise DuplicateTriggerError(inp.intent_id) from exc
        except IntentNotActiveError:
            # Defense-in-depth: persist_trigger detected rowcount==0 on the
            # status-guarded UPDATE. Roll back the staged trigger_event /
            # notification before re-raising so the session is left clean.
            self._db.rollback()
            raise

        return TriggerIntentOutput(
            trigger_event=_trigger_event_to_domain(trigger_row),
            notification=_notification_to_domain(notification_row),
        )
