from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Symbol(TimestampMixin, Base):
    __tablename__ = "symbols"
    __table_args__ = (
        CheckConstraint("market IN ('TWSE', 'TPEx')", name="market"),
        CheckConstraint("instrument_type IN ('stock', 'etf')", name="instrument_type"),
        CheckConstraint(
            "tradable_status IN ('tradable', 'halted', 'unsupported')",
            name="tradable_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_type: Mapped[str] = mapped_column(Text, nullable=False)
    tradable_status: Mapped[str] = mapped_column(Text, nullable=False)


class TradeIntent(TimestampMixin, Base):
    __tablename__ = "trade_intents"
    __table_args__ = (
        CheckConstraint(
            "strategy IN ("
            "'buy_price_alert', 'sell_price_alert', "
            "'limit_buy_order', 'limit_sell_order', 'trailing_stop_alert', "
            "'market_order', 'market_buy_order', 'market_sell_order', 'twap_order'"
            ")",
            name="strategy",
        ),
        CheckConstraint("execution_mode = 'notify_only'", name="execution_mode"),
        CheckConstraint("time_in_force = 'day'", name="time_in_force"),
        CheckConstraint(
            "status IN ('scheduled', 'active', 'triggered', 'expired', 'cancelled', 'cancelled_by_account_disabled')",
            name="status",
        ),
        CheckConstraint(
            "trigger_reference_price_type IN ('ask', 'bid', 'last_fallback')",
            name="trigger_reference_price_type",
        ),
        CheckConstraint("quantity_lots > 0", name="quantity_lots"),
        CheckConstraint(
            "((strategy = 'trailing_stop_alert' AND target_price_original IS NULL "
            "AND target_price_effective IS NULL) OR "
            "(strategy IN ('market_order', 'market_buy_order', 'market_sell_order') AND target_price_original IS NULL "
            "AND target_price_effective IS NULL) OR "
            "(strategy = 'twap_order' AND target_price_original IS NULL "
            "AND target_price_effective IS NULL) OR "
            "(strategy NOT IN ("
            "'trailing_stop_alert', 'market_order', 'market_buy_order', "
            "'market_sell_order', 'twap_order'"
            ") AND target_price_original > 0 "
            "AND target_price_effective > 0))",
            name="target_price_presence",
        ),
        CheckConstraint(
            "transaction_mode IN ('single_notification', 'partial_fill_allowed')",
            name="transaction_mode",
        ),
        CheckConstraint("notification_mode = 'single'", name="notification_mode"),
        CheckConstraint("filled_quantity_lots >= 0", name="filled_quantity_lots"),
        CheckConstraint(
            "((strategy = 'trailing_stop_alert' AND trail_mode IN ('percentage', 'fixed_amount') "
            "AND trail_value IS NOT NULL) OR "
            "(strategy <> 'trailing_stop_alert' AND trail_mode IS NULL AND trail_value IS NULL "
            "AND baseline IS NULL AND dynamic_trigger_price IS NULL AND baseline_updated_at IS NULL))",
            name="trailing_fields_presence",
        ),
        CheckConstraint(
            "trail_value IS NULL OR "
            "(trail_mode = 'percentage' AND trail_value > 0 AND trail_value <= 10) OR "
            "(trail_mode = 'fixed_amount' AND trail_value > 0)",
            name="trail_value_range",
        ),
        CheckConstraint("baseline IS NULL OR baseline > 0", name="baseline_positive"),
        CheckConstraint("dynamic_trigger_price IS NULL OR dynamic_trigger_price > 0", name="dynamic_trigger_price"),
        CheckConstraint(
            "((strategy = 'twap_order' "
            "AND position_side IN ('long', 'short') "
            "AND twap_interval_seconds IS NOT NULL "
            "AND twap_end_time IS NOT NULL "
            "AND twap_start_at IS NOT NULL "
            "AND twap_end_at IS NOT NULL "
            "AND twap_available_slice_count IS NOT NULL "
            "AND twap_materialized_slice_count IS NOT NULL) OR "
            "(strategy <> 'twap_order' "
            "AND position_side IS NULL "
            "AND twap_interval_seconds IS NULL "
            "AND twap_end_time IS NULL "
            "AND twap_start_at IS NULL "
            "AND twap_end_at IS NULL "
            "AND twap_available_slice_count IS NULL "
            "AND twap_materialized_slice_count IS NULL))",
            name="twap_fields_presence",
        ),
        CheckConstraint(
            "twap_interval_seconds IS NULL OR (twap_interval_seconds >= 1 AND twap_interval_seconds <= 3600)",
            name="twap_interval_seconds",
        ),
        CheckConstraint(
            "twap_available_slice_count IS NULL OR twap_available_slice_count >= 2",
            name="twap_available_slice_count",
        ),
        CheckConstraint(
            "twap_materialized_slice_count IS NULL OR "
            "(twap_materialized_slice_count >= 2 AND twap_materialized_slice_count <= 200)",
            name="twap_materialized_slice_count",
        ),
        Index("ix_trade_intents_owner_status_trading_date", "owner_user_id", "status", "trading_date"),
        Index("ix_trade_intents_symbol_status_trading_date", "symbol", "status", "trading_date"),
        Index(
            "uq_trade_intents_active_duplicate",
            "owner_user_id",
            "symbol",
            "strategy",
            "target_price_effective",
            "trail_mode",
            "trail_value",
            "quantity_lots",
            "trading_date",
            unique=True,
            postgresql_where=text("status IN ('scheduled', 'active')"),
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "uq_trade_intents_active_twap_duplicate",
            "owner_user_id",
            "symbol",
            "position_side",
            "trading_date",
            unique=True,
            postgresql_where=text("strategy = 'twap_order' AND status IN ('scheduled', 'active')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    symbol: Mapped[str] = mapped_column(Text, ForeignKey("symbols.symbol"), nullable=False)
    strategy: Mapped[str] = mapped_column(Text, nullable=False)
    execution_mode: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_lots: Mapped[int] = mapped_column(Integer, nullable=False)
    target_price_original: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    target_price_effective: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    trigger_reference_price_type: Mapped[str] = mapped_column(Text, nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    time_in_force: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transaction_mode: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'single_notification'"),
    )
    notification_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'single'"))
    filled_quantity_lots: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_fill_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trail_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    trail_value: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    baseline: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    dynamic_trigger_price: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    baseline_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    position_side: Mapped[str | None] = mapped_column(Text, nullable=True)
    twap_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    twap_end_time: Mapped[time | None] = mapped_column(Time(), nullable=True)
    twap_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    twap_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    twap_available_slice_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    twap_materialized_slice_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TwapSlice(TimestampMixin, Base):
    __tablename__ = "twap_slices"
    __table_args__ = (
        CheckConstraint("planned_quantity_lots > 0", name="planned_quantity_lots"),
        CheckConstraint("status IN ('pending', 'notified', 'cancelled')", name="status"),
        CheckConstraint(
            "primary_reference_price IS NULL OR primary_reference_price > 0",
            name="primary_reference_price",
        ),
        CheckConstraint(
            "primary_reference_price_type IS NULL OR primary_reference_price_type IN ('ask', 'bid', 'last_fallback')",
            name="primary_reference_price_type",
        ),
        CheckConstraint(
            "price_followup_attempts >= 0 AND price_followup_attempts <= 3",
            name="price_followup_attempts",
        ),
        Index("uq_twap_slices_intent_sequence", "trade_intent_id", "sequence_no", unique=True),
        Index("ix_twap_slices_due", "status", "scheduled_at"),
        Index("ix_twap_slices_price_followup_due", "price_followup_required", "next_price_followup_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    trade_intent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trade_intents.id"),
        nullable=False,
    )
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    symbol: Mapped[str] = mapped_column(Text, ForeignKey("symbols.symbol"), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_quantity_lots: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    primary_notification_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("notifications.id"))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    primary_price_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    primary_reference_price: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    primary_reference_price_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_quote_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    price_followup_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    price_followup_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_price_followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    price_followup_notification_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notifications.id"),
    )
    price_followup_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TriggerEvent(Base):
    __tablename__ = "trigger_events"
    __table_args__ = (
        CheckConstraint(
            "trigger_reference_price_type IN ('ask', 'bid', 'last_fallback')",
            name="trigger_reference_price_type",
        ),
        CheckConstraint(
            "((trigger_reference_price_type = 'last_fallback' AND fallback_used = true) "
            "OR (trigger_reference_price_type IN ('ask', 'bid') AND fallback_used = false))",
            name="fallback_consistency",
        ),
        CheckConstraint("target_price_effective > 0", name="target_price_effective"),
        CheckConstraint("trigger_price > 0", name="trigger_price"),
        CheckConstraint("filled_quantity_lots >= 0", name="filled_quantity_lots"),
        CheckConstraint("baseline_at_trigger IS NULL OR baseline_at_trigger > 0", name="baseline_at_trigger"),
        CheckConstraint(
            "dynamic_trigger_price_at_trigger IS NULL OR dynamic_trigger_price_at_trigger > 0",
            name="dynamic_trigger_price_at_trigger",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    trade_intent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trade_intents.id"),
        nullable=False,
        unique=True,
    )
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    symbol: Mapped[str] = mapped_column(Text, ForeignKey("symbols.symbol"), nullable=False)
    quote_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target_price_effective: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    trigger_price: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    trigger_reference_price_type: Mapped[str] = mapped_column(Text, nullable=False)
    fallback_used: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    filled_quantity_lots: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    baseline_at_trigger: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    dynamic_trigger_price_at_trigger: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "type IN ("
            "'price_triggered', 'limit_order_triggered', 'trailing_stop_triggered', "
            "'market_order_triggered', 'twap_slice', 'twap_price_followup'"
            ")",
            name="type",
        ),
        CheckConstraint(
            "(type NOT IN ("
            "'price_triggered', 'limit_order_triggered', 'trailing_stop_triggered', "
            "'market_order_triggered', 'twap_slice', 'twap_price_followup'"
            ")) "
            "OR (trade_intent_id IS NOT NULL)",
            name="triggered_notification_intent",
        ),
        Index("ix_notifications_owner_created_at", "owner_user_id", text("created_at DESC")),
        Index("ix_notifications_owner_read_at", "owner_user_id", "read_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    trade_intent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("trade_intents.id"))
    type: Mapped[str] = mapped_column(Text, nullable=False)
    rendered_title: Mapped[str] = mapped_column(Text, nullable=False)
    rendered_body: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
