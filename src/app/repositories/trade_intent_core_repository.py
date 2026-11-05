"""新軌 repository（模式丙）：讀寫 trade_intent_core + 3 衛星表。

與 legacy `IntentRepository` 完全並行、互不引用，組出**同一個** `TradeIntentData`
（攤平），讓下游純邏輯 / DTO / evaluator 可直接共用。日後 legacy 退役時本模組不受影響。

本增量先實作非 TWAP 的寫/讀/取消最小迴圈；list/系統查詢/生命週期/TWAP 於後續增量補上。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, SessionTransaction, joinedload
from sqlalchemy.sql import ColumnElement, Select

from app.db.models.core import Symbol
from app.db.models.trade_intent_core import (
    TradeIntentCore,
    TradeIntentPriceParams,
    TradeIntentTrailingParams,
    TradeIntentTwapParams,
    TradeIntentTwapSlice,
)
from app.domain.price import SecurityType, format_price_str
from app.domain.trade_intent import (
    CANCELLABLE_STATUSES,
    MARKET_ORDER_STRATEGIES,
    TERMINAL_STATUSES,
    CancelNotAllowedError,
    DuplicateIntentError,
    IntentNotFoundError,
    InvalidCursorError,
    TradeIntentData,
    TwapSliceData,
)
from app.domain.twap import TWAP_STRATEGY, TwapDuplicateActivePlanError, TwapPlan

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


def _twap_slice_to_domain(row: TradeIntentTwapSlice) -> TwapSliceData:
    return TwapSliceData(
        id=row.id,
        trade_intent_id=row.trade_intent_id,
        owner_user_id=row.owner_user_id,
        symbol=row.symbol,
        sequence_no=row.sequence_no,
        scheduled_at=row.scheduled_at,
        planned_quantity_lots=row.planned_quantity_lots,
        status=row.status,
        primary_notification_id=row.primary_notification_id,
        notified_at=row.notified_at,
        primary_price_available=row.primary_price_available,
        primary_reference_price=row.primary_reference_price,
        primary_reference_price_type=row.primary_reference_price_type,
        primary_quote_time=row.primary_quote_time,
        price_followup_required=row.price_followup_required,
        price_followup_attempts=row.price_followup_attempts,
        next_price_followup_at=row.next_price_followup_at,
        price_followup_notification_id=row.price_followup_notification_id,
        price_followup_sent_at=row.price_followup_sent_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
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

    def create_twap(
        self,
        *,
        owner_user_id: UUID,
        symbol: str,
        twap_plan: TwapPlan,
        execution_mode: str,
        time_in_force: str,
        status: str,
        trigger_reference_price_type: str,
    ) -> UUID:
        """建立 TWAP 委託（核心 + twap 衛星 + 切片，同 transaction），回傳 id。

        去重鑑別值＝`position_side`，對應 partial unique index
        `uq_trade_intent_core_active_twap_duplicate`（不含 quantity_lots）→ 同標的同日
        long 與 short 可並存（已定案放行），同 side 重複才擋。
        """

        dedup_key = build_dedup_key(
            TWAP_STRATEGY,
            target_price_effective=None,
            trail_mode=None,
            trail_value=None,
            position_side=twap_plan.position_side,
        )
        duplicate = self._db.execute(
            select(TradeIntentCore.id).where(
                TradeIntentCore.owner_user_id == owner_user_id,
                TradeIntentCore.symbol == symbol,
                TradeIntentCore.strategy == TWAP_STRATEGY,
                TradeIntentCore.dedup_key == dedup_key,
                TradeIntentCore.trading_date == twap_plan.trading_date,
                TradeIntentCore.status.in_(CANCELLABLE_STATUSES),
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise TwapDuplicateActivePlanError(owner_user_id, symbol, twap_plan.position_side)

        intent_id = uuid4()
        self._db.add(
            TradeIntentCore(
                id=intent_id,
                owner_user_id=owner_user_id,
                symbol=symbol,
                strategy=TWAP_STRATEGY,
                execution_mode=execution_mode,
                quantity_lots=twap_plan.target_quantity_lots,
                trigger_reference_price_type=trigger_reference_price_type,
                trading_date=twap_plan.trading_date,
                time_in_force=time_in_force,
                status=status,
                transaction_mode="single_notification",
                notification_mode="single",
                dedup_key=dedup_key,
            )
        )
        self._db.add(
            TradeIntentTwapParams(
                trade_intent_id=intent_id,
                position_side=twap_plan.position_side,
                twap_interval_seconds=twap_plan.interval_seconds,
                twap_end_time=twap_plan.requested_end_time,
                twap_start_at=twap_plan.start_at,
                twap_end_at=twap_plan.end_at,
                twap_available_slice_count=twap_plan.available_slice_count,
                twap_materialized_slice_count=twap_plan.materialized_slice_count,
            )
        )
        for slice_plan in twap_plan.slices:
            self._db.add(
                TradeIntentTwapSlice(
                    id=uuid4(),
                    trade_intent_id=intent_id,
                    owner_user_id=owner_user_id,
                    symbol=symbol,
                    sequence_no=slice_plan.sequence_no,
                    scheduled_at=slice_plan.scheduled_at,
                    planned_quantity_lots=slice_plan.planned_quantity_lots,
                    status="pending",
                )
            )
        try:
            self._db.flush()
        except IntegrityError as exc:
            raise TwapDuplicateActivePlanError(owner_user_id, symbol, twap_plan.position_side) from exc
        return intent_id

    def list_twap_slices(self, intent_id: UUID, owner_user_id: UUID) -> list[TwapSliceData]:
        intent = self._db.execute(
            select(TradeIntentCore.owner_user_id).where(TradeIntentCore.id == intent_id)
        ).scalar_one_or_none()
        if intent is None or intent != owner_user_id:
            raise IntentNotFoundError(intent_id)
        rows = (
            self._db.execute(
                select(TradeIntentTwapSlice)
                .where(
                    TradeIntentTwapSlice.trade_intent_id == intent_id,
                    TradeIntentTwapSlice.owner_user_id == owner_user_id,
                )
                .order_by(TradeIntentTwapSlice.sequence_no.asc())
            )
            .scalars()
            .all()
        )
        return [_twap_slice_to_domain(row) for row in rows]

    def find_by_id(self, intent_id: UUID, owner_user_id: UUID) -> TradeIntentData:
        result = self._db.execute(_core_with_symbol_type().where(TradeIntentCore.id == intent_id)).one_or_none()
        if result is None:
            raise IntentNotFoundError(intent_id)
        core, instrument_type = result
        if core.owner_user_id != owner_user_id:
            raise IntentNotFoundError(intent_id)
        return _to_domain(core, SecurityType(instrument_type))

    def cancel(self, intent_id: UUID, owner_user_id: UUID) -> TradeIntentData:
        """status='cancelled' 並 flush（不 commit，caller 擁有交易）。

        冪等：已 cancelled 直接回現狀。TWAP 另連動取消未發出的切片與待補價。
        """

        result = self._db.execute(_core_with_symbol_type().where(TradeIntentCore.id == intent_id)).one_or_none()
        if result is None:
            raise IntentNotFoundError(intent_id)
        core, instrument_type = result
        if core.owner_user_id != owner_user_id:
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
        if core.strategy == TWAP_STRATEGY:
            self._cancel_pending_slices([intent_id], func.now())
            # 已通知、但還欠一則補價通知的切片：取消後不再補價（切片本身維持 notified）。
            self._db.execute(
                update(TradeIntentTwapSlice)
                .where(
                    TradeIntentTwapSlice.trade_intent_id == intent_id,
                    TradeIntentTwapSlice.price_followup_required.is_(True),
                    TradeIntentTwapSlice.price_followup_notification_id.is_(None),
                )
                .values(price_followup_required=False, next_price_followup_at=None, updated_at=func.now()),
                execution_options={"synchronize_session": False},
            )
        self._db.refresh(core)
        return _to_domain(core, SecurityType(instrument_type))

    def _cancel_pending_slices(self, intent_ids: list[UUID], now: datetime | ColumnElement[datetime]) -> None:
        """尚未發出的切片一律取消並停掉補價；取消與到期共用（兩者都代表計畫不再執行）。"""

        self._db.execute(
            update(TradeIntentTwapSlice)
            .where(
                TradeIntentTwapSlice.trade_intent_id.in_(intent_ids),
                TradeIntentTwapSlice.status == "pending",
            )
            .values(
                status="cancelled",
                price_followup_required=False,
                next_price_followup_at=None,
                updated_at=now,
            ),
            execution_options={"synchronize_session": False},
        )

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

    def active_or_scheduled_symbols(self) -> set[str]:
        """Distinct symbols across every non-terminal intent — seeds robot #2 的報價訂閱
        （main.py 啟動 reconcile 呼叫）。"""

        stmt = select(TradeIntentCore.symbol).where(TradeIntentCore.status.in_(CANCELLABLE_STATUSES)).distinct()
        return {row for (row,) in self._db.execute(stmt).all()}

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

    def count_active_or_scheduled_for_symbol(self, symbol: str) -> int:
        """跨 owner 統計某 symbol 的非終態委託——訂閱是 broker-wide，退訂只在**沒有任何**
        使用者還要這檔時才能發生（取消後的 reconcile 用）。"""

        stmt = (
            select(func.count())
            .select_from(TradeIntentCore)
            .where(
                TradeIntentCore.symbol == symbol,
                TradeIntentCore.status.in_(CANCELLABLE_STATUSES),
            )
        )
        return int(self._db.execute(stmt).scalar_one())

    def list_by_owner(
        self,
        owner_user_id: UUID,
        statuses: list[str] | None,
        trading_date: date | None,
        cursor: str | None,
        page_size: int,
    ) -> tuple[list[TradeIntentData], str | None]:
        """擁有者清單 + cursor 分頁（與 legacy `IntentRepository.list_by_owner` 等價）。

        排序規則沿用舊軌：只查終態時以 `updated_at desc` 為主（最近變動優先），否則以
        `trading_date asc, created_at desc` 排。cursor 是上一頁最後一筆的 id，anchor 讀回
        它的排序欄再組 keyset 條件——排序欄非唯一，故一律以 `id` 收尾打破平手。
        """

        effective_statuses = set(statuses) if statuses else None
        is_terminal_only = effective_statuses is not None and effective_statuses.issubset(TERMINAL_STATUSES)

        stmt = _core_with_symbol_type().where(TradeIntentCore.owner_user_id == owner_user_id)
        if statuses:
            stmt = stmt.where(TradeIntentCore.status.in_(statuses))
        if trading_date is not None:
            stmt = stmt.where(TradeIntentCore.trading_date == trading_date)

        if is_terminal_only:
            stmt = stmt.order_by(TradeIntentCore.updated_at.desc(), TradeIntentCore.id.asc())
        else:
            stmt = stmt.order_by(
                TradeIntentCore.trading_date.asc(),
                TradeIntentCore.created_at.desc(),
                TradeIntentCore.id.asc(),
            )

        if cursor:
            cursor_id = UUID(cursor)  # 格式已由 route 層驗過
            anchor = self._db.execute(
                select(TradeIntentCore).where(
                    TradeIntentCore.id == cursor_id,
                    TradeIntentCore.owner_user_id == owner_user_id,
                )
            ).scalar_one_or_none()
            if anchor is None:
                raise InvalidCursorError(cursor_id)
            if is_terminal_only:
                stmt = stmt.where(
                    or_(
                        TradeIntentCore.updated_at < anchor.updated_at,
                        and_(TradeIntentCore.updated_at == anchor.updated_at, TradeIntentCore.id > anchor.id),
                    )
                )
            else:
                stmt = stmt.where(
                    or_(
                        TradeIntentCore.trading_date > anchor.trading_date,
                        and_(
                            TradeIntentCore.trading_date == anchor.trading_date,
                            TradeIntentCore.created_at < anchor.created_at,
                        ),
                        and_(
                            TradeIntentCore.trading_date == anchor.trading_date,
                            TradeIntentCore.created_at == anchor.created_at,
                            TradeIntentCore.id > anchor.id,
                        ),
                    )
                )

        rows = list(self._db.execute(stmt.limit(page_size + 1)).all())
        has_more = len(rows) > page_size
        page = rows[:page_size]

        next_cursor = str(page[-1][0].id) if has_more and page else None
        return [_to_domain(core, SecurityType(instrument_type)) for core, instrument_type in page], next_cursor

    # ------------------------------------------------------------------
    # lifecycle / 帳號停用連動（caller：IntentLifecycleCommand、DisableUserCommand）
    # ------------------------------------------------------------------

    def system_activate_scheduled_day_intents(self, trading_date: date, now: datetime) -> int:
        """把當日 scheduled 單轉入 active 監控。不 commit。"""

        result = cast(
            CursorResult[Any],
            self._db.execute(
                update(TradeIntentCore)
                .where(
                    TradeIntentCore.status == "scheduled",
                    TradeIntentCore.trading_date == trading_date,
                )
                .values(status="active", updated_at=now)
            ),
        )
        return int(result.rowcount or 0)

    def system_expire_day_intents_through(self, cutoff_date: date, now: datetime) -> int:
        """讓已不可能再觸發的當沖單到期，並連動取消其未發出的 TWAP 切片。不 commit。"""

        expired_ids = list(
            self._db.execute(
                update(TradeIntentCore)
                .where(
                    TradeIntentCore.status.in_(CANCELLABLE_STATUSES),
                    TradeIntentCore.trading_date <= cutoff_date,
                )
                .values(status="expired", updated_at=now)
                .returning(TradeIntentCore.id)
            )
            .scalars()
            .all()
        )
        if expired_ids:
            self._cancel_pending_slices(expired_ids, now)
        return len(expired_ids)

    def cancel_active_for_owner(self, owner_user_id: UUID, *, status: str, now: datetime) -> int:
        """帳號停用連動：整批取消該擁有者的非終態委託，回傳筆數。不 commit。"""

        result = cast(
            CursorResult[Any],
            self._db.execute(
                update(TradeIntentCore)
                .where(
                    TradeIntentCore.owner_user_id == owner_user_id,
                    TradeIntentCore.status.in_(CANCELLABLE_STATUSES),
                )
                .values(status=status, cancelled_at=now, updated_at=now),
                execution_options={"synchronize_session": False},
            ),
        )
        return int(result.rowcount or 0)
