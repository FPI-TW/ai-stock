"""新軌建單 / 取消 command（模式丙最小版）。

與 legacy `commands/trade_intent.py` 並行、不互相 import：寫入新的
`trade_intent_core` + 衛星表。類別沿用與舊版相同的名稱（差在所屬模組），
讓未來 cutover 只是把 endpoint 的 import 從 `trade_intent` 換成
`trade_intent_core`，退役時只刪舊模組、新模組零改動。

**刻意不含**（見 T1 工單附錄，各自延後到對應 infra 增量）：
- 訂閱 reconcile（`reconcile_on_create` / 取消的 Phase B）→ 新表訂閱接線增量。
- 建單即達標的 inline 觸發（`persist_trigger`）→ 需新 trigger_events/notifications 表，
  與 robot #2 的觸發 persist 同一套，於 robot #2 增量一起做、再回填。
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.price import InvalidTypeError, PriceRequest, PriceService, SecurityType
from app.domain.trade_intent import (
    MARKET_ORDER_STRATEGIES,
    SymbolIntentLimitExceededError,
    TradeIntentData,
    UserIntentLimitExceededError,
    derive_order_side,
)
from app.domain.trading_session import TradingSessionService
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from app.services.symbol import SymbolService

# V0.5 fixed values（與舊軌一致；新軌自帶一份以保持與舊軌零耦合）
_EXECUTION_MODE = "notify_only"
_TIME_IN_FORCE = "day"


@dataclass(frozen=True, slots=True)
class IntentLimits:
    """§15 per-owner creation caps（注入式，cutover 時由 dep 提供）。"""

    per_user: int
    per_symbol: int


@dataclass(frozen=True)
class CreateTradeIntentInput:
    symbol: str
    strategy: str
    quantity_lots: int
    owner_user_id: UUID
    target_price: str | None = None  # str 以保留小數精度
    transaction_mode: str = "single_notification"
    notification_mode: str = "single"
    trail_mode: str | None = None
    trail_value: Decimal | None = None
    request_id: str | None = None  # §16 correlation id


@dataclass(frozen=True)
class CancelTradeIntentInput:
    intent_id: UUID
    owner_user_id: UUID
    request_id: str | None = None


class CreateTradeIntentCommand:
    """驗證 + §15 限額 + 寫入新表，單一 transaction。不觸發、不訂閱（見模組 docstring）。"""

    def __init__(
        self,
        symbol_service: SymbolService,
        session_service: TradingSessionService,
        intent_repo: TradeIntentCoreRepository,
        db: Session,
        limits: IntentLimits | None = None,
    ) -> None:
        self._symbol_service = symbol_service
        self._session_service = session_service
        self._intent_repo = intent_repo
        self._db = db
        self._limits = limits

    def execute(self, inp: CreateTradeIntentInput) -> TradeIntentData:
        try:
            # 1. 驗證 symbol 存在且可交易
            symbol_obj = self._symbol_service.get_tradable_symbol(inp.symbol)

            # 2. 驗證價格 / trailing 設定
            try:
                security_type = SecurityType(symbol_obj.instrument_type)
            except ValueError:
                raise InvalidTypeError(symbol_obj.instrument_type, "must be stock or etf") from None
            effective_price: Decimal | None = None
            trail_value: Decimal | None = None
            if inp.strategy == "trailing_stop_alert":
                if inp.trail_mode is None or inp.trail_value is None:
                    raise ValueError("trailing_stop_alert requires trail_mode and trail_value")
                trail_value = inp.trail_value
                if inp.trail_mode == "fixed_amount":
                    PriceService.validate_fixed_amount_tick(security_type, inp.trail_value)
            elif inp.strategy in MARKET_ORDER_STRATEGIES:
                effective_price = None
            else:
                if inp.target_price is None:
                    raise ValueError(f"{inp.strategy} requires target_price")
                effective_price = PriceService.validate(
                    PriceRequest(type=security_type, price=inp.target_price, amount=inp.quantity_lots)
                )

            # 3. trading_date / 初始 status
            now = self._session_service.now_taipei()
            trading_date = self._session_service.get_day_intent_trading_date(now)
            initial_status = self._session_service.get_initial_day_intent_status(now)

            # 觸發參考價：buy→ask、sell→bid，直接由共用 side helper 導出，不另手抄對照表
            # （新策略加進 BUY/SELL side-set 時自動涵蓋；未知策略沿用同一 ValueError）。
            trigger_ref = "ask" if derive_order_side(inp.strategy) == "buy" else "bid"

            # 4. §15 建立上限（user cap 先、symbol cap 後）；limits=None → 跳過（測試 wiring）
            if self._limits is not None:
                user_count = self._intent_repo.count_active_or_scheduled_for_user(inp.owner_user_id)
                if user_count >= self._limits.per_user:
                    raise UserIntentLimitExceededError(limit=self._limits.per_user, current=user_count)
                symbol_count = self._intent_repo.count_active_or_scheduled_for_user_symbol(
                    inp.owner_user_id, inp.symbol
                )
                if symbol_count >= self._limits.per_symbol:
                    raise SymbolIntentLimitExceededError(
                        symbol=inp.symbol, limit=self._limits.per_symbol, current=symbol_count
                    )

            # 5. 寫入（核心 + 衛星，同 tx）
            intent_id = self._intent_repo.create(
                owner_user_id=inp.owner_user_id,
                symbol=inp.symbol,
                strategy=inp.strategy,
                quantity_lots=inp.quantity_lots,
                target_price_original=effective_price,
                target_price_effective=effective_price,
                trigger_reference_price_type=trigger_ref,
                trading_date=trading_date,
                time_in_force=_TIME_IN_FORCE,
                execution_mode=_EXECUTION_MODE,
                status=initial_status,
                transaction_mode=inp.transaction_mode,
                notification_mode=inp.notification_mode,
                trail_mode=inp.trail_mode,
                trail_value=trail_value,
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        # 6. commit 後 materialise（created_at / updated_at 等 server 值）
        return self._intent_repo.find_by_id(intent_id, inp.owner_user_id)


class CancelTradeIntentCommand:
    """取消委託：repo.cancel + commit。訂閱清理（Phase B）延後到新表訂閱接線增量。"""

    def __init__(self, intent_repo: TradeIntentCoreRepository, db: Session) -> None:
        self._intent_repo = intent_repo
        self._db = db

    def execute(self, inp: CancelTradeIntentInput) -> TradeIntentData:
        try:
            intent = self._intent_repo.cancel(inp.intent_id, inp.owner_user_id)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return intent
