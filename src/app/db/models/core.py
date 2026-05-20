from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, func, text
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
        CheckConstraint("strategy IN ('buy_price_alert', 'sell_price_alert')", name="strategy"),
        CheckConstraint("execution_mode = 'notify_only'", name="execution_mode"),
        CheckConstraint("time_in_force = 'day'", name="time_in_force"),
        CheckConstraint(
            "status IN ('scheduled', 'active', 'triggered', 'cancelled')",
            name="status",
        ),
        CheckConstraint(
            "trigger_reference_price_type IN ('ask', 'bid', 'last_fallback')",
            name="trigger_reference_price_type",
        ),
        CheckConstraint("quantity_lots > 0", name="quantity_lots"),
        CheckConstraint("target_price_original > 0", name="target_price_original"),
        CheckConstraint("target_price_effective > 0", name="target_price_effective"),
        Index("ix_trade_intents_owner_status_trading_date", "owner_user_id", "status", "trading_date"),
        Index("ix_trade_intents_symbol_status_trading_date", "symbol", "status", "trading_date"),
        Index(
            "uq_trade_intents_active_duplicate",
            "owner_user_id",
            "symbol",
            "strategy",
            "target_price_effective",
            "quantity_lots",
            "trading_date",
            unique=True,
            postgresql_where=text("status IN ('scheduled', 'active')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    symbol: Mapped[str] = mapped_column(Text, ForeignKey("symbols.symbol"), nullable=False)
    strategy: Mapped[str] = mapped_column(Text, nullable=False)
    execution_mode: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_lots: Mapped[int] = mapped_column(Integer, nullable=False)
    target_price_original: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    target_price_effective: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    trigger_reference_price_type: Mapped[str] = mapped_column(Text, nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    time_in_force: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("type IN ('price_triggered')", name="type"),
        CheckConstraint("(type <> 'price_triggered') OR (trade_intent_id IS NOT NULL)", name="price_triggered_intent"),
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
