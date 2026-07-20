"""新交易委託核心表 + 每策略衛星參數表（模式丙：雙軌完全獨立）。

這是與 legacy `trade_intents` 並行的「新垂直切片」：新 endpoint 寫這裡、專屬
dispatcher（robot #2）評估這裡的列。本模組**不引用任何 legacy order 表**，因此
日後 legacy 切片可整組刪除而不影響此處。

去重＝方案 A：`trade_intent_core.dedup_key` 存 app 算好的策略鑑別值（price→價格、
trailing→`mode:value`、twap→position_side、market→空字串），讓「同一天有效委託不可
重複」的唯一索引維持單表索引，而策略參數放在衛星表。
"""

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.core import TimestampMixin


class TradeIntentCore(TimestampMixin, Base):
    __tablename__ = "trade_intent_core"
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
            "transaction_mode IN ('single_notification', 'partial_fill_allowed')",
            name="transaction_mode",
        ),
        CheckConstraint("notification_mode = 'single'", name="notification_mode"),
        CheckConstraint("filled_quantity_lots >= 0", name="filled_quantity_lots"),
        Index(
            "ix_trade_intent_core_owner_status_trading_date",
            "owner_user_id",
            "status",
            "trading_date",
        ),
        Index(
            "ix_trade_intent_core_symbol_status_trading_date",
            "symbol",
            "status",
            "trading_date",
        ),
        # system_list_active_symbols 走 `DISTINCT symbol WHERE status='active'`；上面兩個
        # composite index 首欄非 status、兩個 partial unique index 謂詞含 strategy 條件皆不被
        # 蘊含 → 否則 seq scan，成本隨終態列（triggered/expired/cancelled）永久累積無上界。
        # 此 partial index 只涵蓋 live 列，謂詞被 status='active' 蘊含 → 成本有界。
        Index(
            "ix_trade_intent_core_active_symbols",
            "symbol",
            postgresql_where=text("status IN ('scheduled', 'active')"),
        ),
        # 非 TWAP 去重：dedup_key 取代原本橫跨 target_price_effective / trail_mode / trail_value
        # 的多欄索引（語意等價，改看單一鑑別欄）。
        Index(
            "uq_trade_intent_core_active_duplicate",
            "owner_user_id",
            "symbol",
            "strategy",
            "dedup_key",
            "quantity_lots",
            "trading_date",
            unique=True,
            postgresql_where=text("strategy <> 'twap_order' AND status IN ('scheduled', 'active')"),
        ),
        # TWAP 去重：維持原形狀（不含 quantity_lots），dedup_key 存 position_side。
        Index(
            "uq_trade_intent_core_active_twap_duplicate",
            "owner_user_id",
            "symbol",
            "dedup_key",
            "trading_date",
            unique=True,
            postgresql_where=text("strategy = 'twap_order' AND status IN ('scheduled', 'active')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(Text, ForeignKey("symbols.symbol"), nullable=False)
    strategy: Mapped[str] = mapped_column(Text, nullable=False)
    execution_mode: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_lots: Mapped[int] = mapped_column(Integer, nullable=False)
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
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)

    price_params: Mapped["TradeIntentPriceParams | None"] = relationship(
        back_populates="intent",
        uselist=False,
    )
    trailing_params: Mapped["TradeIntentTrailingParams | None"] = relationship(
        back_populates="intent",
        uselist=False,
    )
    twap_params: Mapped["TradeIntentTwapParams | None"] = relationship(
        back_populates="intent",
        uselist=False,
    )


class TradeIntentPriceParams(Base):
    """到價提醒 / 限價單的目標價（buy/sell_price_alert + limit_buy/sell_order 共用）。"""

    __tablename__ = "trade_intent_price_params"
    __table_args__ = (
        CheckConstraint("target_price_original > 0", name="target_price_original"),
        CheckConstraint("target_price_effective > 0", name="target_price_effective"),
    )

    trade_intent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trade_intent_core.id", name="fk_trade_intent_price_params_intent"),
        primary_key=True,
    )
    target_price_original: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    target_price_effective: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)

    intent: Mapped[TradeIntentCore] = relationship(back_populates="price_params")


class TradeIntentTrailingParams(Base):
    """移動停利參數（trailing_stop_alert）；baseline 系列為執行期狀態，初始可為空。"""

    __tablename__ = "trade_intent_trailing_params"
    __table_args__ = (
        CheckConstraint("trail_mode IN ('percentage', 'fixed_amount')", name="trail_mode"),
        CheckConstraint(
            "(trail_mode = 'percentage' AND trail_value > 0 AND trail_value <= 10) OR "
            "(trail_mode = 'fixed_amount' AND trail_value > 0)",
            name="trail_value_range",
        ),
        CheckConstraint("baseline IS NULL OR baseline > 0", name="baseline_positive"),
        CheckConstraint(
            "dynamic_trigger_price IS NULL OR dynamic_trigger_price > 0",
            name="dynamic_trigger_price",
        ),
    )

    trade_intent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trade_intent_core.id", name="fk_trade_intent_trailing_params_intent"),
        primary_key=True,
    )
    trail_mode: Mapped[str] = mapped_column(Text, nullable=False)
    trail_value: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    baseline: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    dynamic_trigger_price: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    baseline_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    intent: Mapped[TradeIntentCore] = relationship(back_populates="trailing_params")


class TradeIntentTwapParams(Base):
    """TWAP 計畫參數（twap_order）；slices 一對多子表另建（FK→trade_intent_core.id）。"""

    __tablename__ = "trade_intent_twap_params"
    __table_args__ = (
        CheckConstraint("position_side IN ('long', 'short')", name="position_side"),
        CheckConstraint(
            "twap_interval_seconds >= 1 AND twap_interval_seconds <= 3600",
            name="twap_interval_seconds",
        ),
        CheckConstraint("twap_available_slice_count >= 2", name="twap_available_slice_count"),
        CheckConstraint(
            "twap_materialized_slice_count >= 2 AND twap_materialized_slice_count <= 200",
            name="twap_materialized_slice_count",
        ),
    )

    trade_intent_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trade_intent_core.id", name="fk_trade_intent_twap_params_intent"),
        primary_key=True,
    )
    position_side: Mapped[str] = mapped_column(Text, nullable=False)
    twap_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    twap_end_time: Mapped[time] = mapped_column(Time(), nullable=False)
    twap_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    twap_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    twap_available_slice_count: Mapped[int] = mapped_column(Integer, nullable=False)
    twap_materialized_slice_count: Mapped[int] = mapped_column(Integer, nullable=False)

    intent: Mapped[TradeIntentCore] = relationship(back_populates="twap_params")


class TradeIntentTrigger(Base):
    """新軌觸發事件（對應 legacy `trigger_events`，FK→trade_intent_core）。

    委託內部的觸發稽核，屬新軌、隨新軌一起退役（與 `notifications` 不同——後者是
    跨功能的使用者收件匣，共用一張表，見 core.Notification 的 trade_intent_core_id）。
    表名用 `trade_intent_triggers`（非 `..._core_trigger_events`）以免衍生約束名超過
    PG 63 字上限。
    """

    __tablename__ = "trade_intent_triggers"
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
        ForeignKey("trade_intent_core.id", name="fk_trade_intent_triggers_intent"),
        nullable=False,
        unique=True,
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_trade_intent_triggers_owner"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(
        Text,
        ForeignKey("symbols.symbol", name="fk_trade_intent_triggers_symbol"),
        nullable=False,
    )
    quote_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    target_price_effective: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    trigger_price: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    trigger_reference_price_type: Mapped[str] = mapped_column(Text, nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    filled_quantity_lots: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    baseline_at_trigger: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    dynamic_trigger_price_at_trigger: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), nullable=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
