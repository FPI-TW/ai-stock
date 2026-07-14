"""新軌 repository（模式丙）：讀寫 trade_intent_core + 3 衛星表。

與 legacy `IntentRepository` 完全並行、互不引用，組出**同一個** `TradeIntentData`
（攤平），讓下游純邏輯 / DTO / evaluator 可直接共用。日後 legacy 退役時本模組不受影響。

本增量先實作非 TWAP 的寫/讀/取消最小迴圈；list/系統查詢/生命週期/TWAP 於後續增量補上。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, SessionTransaction, joinedload
from sqlalchemy.sql import Select

from app.db.models.core import Symbol
from app.db.models.trade_intent_core import (
    TradeIntentCore,
    TradeIntentPriceParams,
    TradeIntentTrailingParams,
)
from app.domain.price import SecurityType, format_price_str
from app.domain.trade_intent import (
    CANCELLABLE_STATUSES,
    MARKET_ORDER_STRATEGIES,
    CancelNotAllowedError,
    DuplicateIntentError,
    IntentNotFoundError,
    TradeIntentData,
)
from app.domain.twap import TWAP_STRATEGY

TRAILING_STRATEGY = "trailing_stop_alert"


def build_dedup_key(
    strategy: str,
    *,
    target_price_effective: Decimal | None,
    trail_mode: str | None,
    trail_value: Decimal | None,
    position_side: str | None,
) -> str:
    """把某策略「判斷重複」用的鑑別值濃縮成標準化 text key。

    新核心表只留單一 `dedup_key` 欄，讓 active-duplicate 唯一索引維持單表，而策略
    參數放衛星表。**數值相等的價格必須映射到同一 key**，故價格走 `format_price_str`
    （去尾零、補到 ≥2 位）而非 `str()`，避免 `600` 與 `600.0000` 被當成不同。
    """
    if strategy == TRAILING_STRATEGY:
        value = format_price_str(trail_value) if trail_value is not None else ""
        return f"{trail_mode}:{value}"
    if strategy == TWAP_STRATEGY:
        return position_side or ""
    if target_price_effective is not None:
        return format_price_str(target_price_effective)
    return ""


def _to_domain(core: TradeIntentCore, security_type: SecurityType) -> TradeIntentData:
    """從核心 + 衛星 row 組回攤平的 TradeIntentData（與 legacy repo 同形狀）。"""

    price = core.price_params
    trailing = core.trailing_params
    twap = core.twap_params
    return TradeIntentData(
        id=core.id,
        owner_user_id=core.owner_user_id,
        symbol=core.symbol,
        strategy=core.strategy,
        execution_mode=core.execution_mode,
        quantity_lots=core.quantity_lots,
        target_price_original=price.target_price_original if price else None,
        target_price_effective=price.target_price_effective if price else None,
        trigger_reference_price_type=core.trigger_reference_price_type,
        trading_date=core.trading_date,
        time_in_force=core.time_in_force,
        status=core.status,
        created_at=core.created_at,
        updated_at=core.updated_at,
        cancelled_at=core.cancelled_at,
        triggered_at=core.triggered_at,
        transaction_mode=core.transaction_mode,
        notification_mode=core.notification_mode,
        filled_quantity_lots=core.filled_quantity_lots,
        last_fill_at=core.last_fill_at,
        trail_mode=trailing.trail_mode if trailing else None,
        trail_value=trailing.trail_value if trailing else None,
        baseline=trailing.baseline if trailing else None,
        dynamic_trigger_price=trailing.dynamic_trigger_price if trailing else None,
        baseline_updated_at=trailing.baseline_updated_at if trailing else None,
        security_type=security_type,
        position_side=twap.position_side if twap else None,
        twap_interval_seconds=twap.twap_interval_seconds if twap else None,
        twap_end_time=twap.twap_end_time if twap else None,
        twap_start_at=twap.twap_start_at if twap else None,
        twap_end_at=twap.twap_end_at if twap else None,
        twap_available_slice_count=twap.twap_available_slice_count if twap else None,
        twap_materialized_slice_count=twap.twap_materialized_slice_count if twap else None,
    )


def _core_with_symbol_type() -> Select[tuple[TradeIntentCore, str]]:
    """核心 + symbol 型別，並以 joinedload 一次載入 3 張 1:1 衛星，讓 _to_domain 不再逐一
    lazy load。三者皆 uselist=False（衛星 PK=FK），LEFT JOIN 每列至多對到 1 筆 → 無 row
    explosion、單一查詢即可（robot 熱路徑每 tick 從 4 條收斂回 1 條），故不需 .unique()。"""

    return (
        select(TradeIntentCore, Symbol.instrument_type)
        .join(Symbol, TradeIntentCore.symbol == Symbol.symbol)
        .options(
            joinedload(TradeIntentCore.price_params),
            joinedload(TradeIntentCore.trailing_params),
            joinedload(TradeIntentCore.twap_params),
        )
    )


class TradeIntentCoreRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def commit(self) -> None:
        """Commit pending writes. Command paths usually own their own boundary;
        system/dev evaluator paths batch writes from one quote snapshot here."""

        self._db.commit()

    def begin_nested(self) -> SessionTransaction:
        """Open a savepoint on the repository session."""

        return self._db.begin_nested()

    def create(
        self,
        owner_user_id: UUID,
        symbol: str,
        strategy: str,
        quantity_lots: int,
        target_price_original: Decimal | None,
        target_price_effective: Decimal | None,
        trigger_reference_price_type: str,
        trading_date: date,
        time_in_force: str,
        execution_mode: str,
        status: str,
        transaction_mode: str = "single_notification",
        notification_mode: str = "single",
        trail_mode: str | None = None,
        trail_value: Decimal | None = None,
        baseline: Decimal | None = None,
        dynamic_trigger_price: Decimal | None = None,
        baseline_updated_at: datetime | None = None,
    ) -> UUID:
        """建立非 TWAP 委託（核心 + 對應衛星，同 transaction），回傳 id。

        與 legacy `IntentRepository.create` 相同的交易語意：不 commit、不 refresh，
        由 caller 擁有交易邊界；`IntegrityError` 轉 `DuplicateIntentError`。
        """

        # 本方法只建非 TWAP 委託；TWAP 走獨立 create_twap（PR4）。若讓 TWAP 落到這裡，會以
        # dedup_key='' 且無 twap 衛星入庫成壞列。目前不可達（command 先擋），仍顯式兌現契約。
        if strategy == TWAP_STRATEGY:
            raise ValueError("create() 不處理 TWAP；請用 create_twap")

        # 拆衛星表後，舊軌 target_price_presence / trailing_fields_presence 兩條跨欄 CHECK
        # 已無單表對應。create() 是新軌唯一寫入口，於此把「策略↔參數」一致性擋回來，避免
        # 錯配參數被靜默丟棄、或寫出無衛星列且 dedup_key='' 的壞列。
        # ponytail: app 層守衛涵蓋所有 Python caller；若日後要防 raw SQL 寫入，再上
        # core UNIQUE(id, strategy) + 衛星複合 FK(trade_intent_id, strategy)+CHECK 的 DB 層版本。
        is_trailing = strategy == TRAILING_STRATEGY
        trailing_params = (trail_mode, trail_value, baseline, dynamic_trigger_price, baseline_updated_at)
        if is_trailing:
            if trail_mode is None or trail_value is None:
                raise ValueError("trailing_stop_alert requires trail_mode and trail_value")
        elif any(param is not None for param in trailing_params):
            raise ValueError(f"{strategy} must not carry trailing params")

        # TWAP 已於上方擋掉，此處只餘 trailing / price / market。價格衛星兩欄皆 NOT NULL，
        # 故 original/effective 對稱檢查：只驗 effective 會讓 original=None 撞衛星 NOT NULL，
        # 再被下方 flush 的 except IntegrityError 誤轉成 DuplicateIntentError（誤導性錯誤）。
        requires_price = not is_trailing and strategy not in MARKET_ORDER_STRATEGIES
        price_params = (target_price_original, target_price_effective)
        if requires_price:
            if any(param is None for param in price_params):
                raise ValueError(f"{strategy} requires a target price")
        elif any(param is not None for param in price_params):
            raise ValueError(f"{strategy} must not carry a target price")

        dedup_key = build_dedup_key(
            strategy,
            target_price_effective=target_price_effective,
            trail_mode=trail_mode,
            trail_value=trail_value,
            position_side=None,
        )
        duplicate = self._db.execute(
            select(TradeIntentCore.id).where(
                TradeIntentCore.owner_user_id == owner_user_id,
                TradeIntentCore.symbol == symbol,
                TradeIntentCore.strategy == strategy,
                TradeIntentCore.dedup_key == dedup_key,
                TradeIntentCore.quantity_lots == quantity_lots,
                TradeIntentCore.trading_date == trading_date,
                TradeIntentCore.status.in_(CANCELLABLE_STATUSES),
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise DuplicateIntentError(owner_user_id, symbol, strategy)

        intent_id = uuid4()
        self._db.add(
            TradeIntentCore(
                id=intent_id,
                owner_user_id=owner_user_id,
                symbol=symbol,
                strategy=strategy,
                execution_mode=execution_mode,
                quantity_lots=quantity_lots,
                trigger_reference_price_type=trigger_reference_price_type,
                trading_date=trading_date,
                time_in_force=time_in_force,
                status=status,
                transaction_mode=transaction_mode,
                notification_mode=notification_mode,
                dedup_key=dedup_key,
            )
        )
        if target_price_effective is not None:
            # 到價提醒 / 限價單：寫價格衛星（market 單兩價皆 None → 不配）
            self._db.add(
                TradeIntentPriceParams(
                    trade_intent_id=intent_id,
                    target_price_original=target_price_original,
                    target_price_effective=target_price_effective,
                )
            )
        if strategy == TRAILING_STRATEGY:
            self._db.add(
                TradeIntentTrailingParams(
                    trade_intent_id=intent_id,
                    trail_mode=trail_mode,
                    trail_value=trail_value,
                    baseline=baseline,
                    dynamic_trigger_price=dynamic_trigger_price,
                    baseline_updated_at=baseline_updated_at,
                )
            )
        try:
            self._db.flush()
        except IntegrityError as exc:
            raise DuplicateIntentError(owner_user_id, symbol, strategy) from exc
        return intent_id

    def find_by_id(self, intent_id: UUID, owner_user_id: UUID) -> TradeIntentData:
        result = self._db.execute(_core_with_symbol_type().where(TradeIntentCore.id == intent_id)).one_or_none()
        if result is None:
            raise IntentNotFoundError(intent_id)
        core, instrument_type = result
        if core is None or core.owner_user_id != owner_user_id:
            raise IntentNotFoundError(intent_id)
        return _to_domain(core, SecurityType(instrument_type))

    def cancel(self, intent_id: UUID, owner_user_id: UUID) -> TradeIntentData:
        """status='cancelled' 並 flush（不 commit，caller 擁有交易）。

        冪等：已 cancelled 直接回現狀。TWAP slice 連動取消於 TWAP 增量補上。
        """

        result = self._db.execute(_core_with_symbol_type().where(TradeIntentCore.id == intent_id)).one_or_none()
        if result is None:
            raise IntentNotFoundError(intent_id)
        core, instrument_type = result
        if core is None or core.owner_user_id != owner_user_id:
            raise IntentNotFoundError(intent_id)
        if core.status == "cancelled":
            return _to_domain(core, SecurityType(instrument_type))
        if core.status not in CANCELLABLE_STATUSES:
            raise CancelNotAllowedError(intent_id, core.status)

        self._db.execute(
            update(TradeIntentCore)
            .where(TradeIntentCore.id == intent_id)
            .values(status="cancelled", updated_at=func.now(), cancelled_at=func.now()),
            execution_options={"synchronize_session": False},
        )
        self._db.refresh(core)
        return _to_domain(core, SecurityType(instrument_type))

    # ------------------------------------------------------------------
    # trailing baseline (robot #2 evaluator writes back here)
    # ------------------------------------------------------------------

    def system_update_trailing_baseline(
        self,
        intent_id: UUID,
        baseline: Decimal | None,
        dynamic_trigger_price: Decimal | None,
        baseline_updated_at: datetime | None,
    ) -> None:
        values: dict[str, object | None] = {
            "baseline": baseline,
            "dynamic_trigger_price": dynamic_trigger_price,
        }
        if baseline_updated_at is not None:
            values["baseline_updated_at"] = baseline_updated_at
        result = cast(
            CursorResult[Any],
            self._db.execute(
                update(TradeIntentTrailingParams)
                .where(TradeIntentTrailingParams.trade_intent_id == intent_id)
                .values(**values)
            ),
        )
        # 拆表後這個 UPDATE 對「非 trailing / 不存在」的 intent 只是匹配 0 列，不會像舊軌
        # 單表那樣撞 CHECK。若靜默返回並照樣 bump updated_at，caller bug 會被遮蔽 → fail loud。
        if result.rowcount == 0:
            raise ValueError(f"no trailing params row for intent {intent_id}")
        self._db.execute(update(TradeIntentCore).where(TradeIntentCore.id == intent_id).values(updated_at=func.now()))

    # ------------------------------------------------------------------
    # reads — 只保留「下一步（robot #2 + §15 限額）馬上會呼叫」的查詢。
    # 其餘舊 repo 方法（list_by_owner / 生命週期 / 訂閱 / 帳號停用 cascade）
    # 隨各自 consumer 增量補回，見 TASK_TRACKER「方法 ↔ 增量」對應表。
    # ------------------------------------------------------------------

    def system_list_active_symbols(self) -> list[str]:
        """Distinct symbols with at least one active intent (owner-agnostic; robot #2 path)."""

        rows = (
            self._db.execute(select(TradeIntentCore.symbol).where(TradeIntentCore.status == "active").distinct())
            .scalars()
            .all()
        )
        return list(rows)

    def system_list_active_by_symbols(self, symbols: list[str]) -> list[TradeIntentData]:
        """All active intents whose symbol is in the list (owner-agnostic; robot #2 path)."""

        if not symbols:
            return []
        rows = self._db.execute(
            _core_with_symbol_type().where(
                TradeIntentCore.status == "active",
                TradeIntentCore.symbol.in_(symbols),
            )
        ).all()
        return [_to_domain(core, SecurityType(instrument_type)) for core, instrument_type in rows]

    def count_active_or_scheduled_for_user(self, owner_user_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(TradeIntentCore)
            .where(
                TradeIntentCore.owner_user_id == owner_user_id,
                TradeIntentCore.status.in_(CANCELLABLE_STATUSES),
            )
        )
        return int(self._db.execute(stmt).scalar_one())

    def count_active_or_scheduled_for_user_symbol(self, owner_user_id: UUID, symbol: str) -> int:
        stmt = (
            select(func.count())
            .select_from(TradeIntentCore)
            .where(
                TradeIntentCore.owner_user_id == owner_user_id,
                TradeIntentCore.symbol == symbol,
                TradeIntentCore.status.in_(CANCELLABLE_STATUSES),
            )
        )
        return int(self._db.execute(stmt).scalar_one())
