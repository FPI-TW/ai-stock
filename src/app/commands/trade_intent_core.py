"""新軌建單 / 取消 command（模式丙最小版）。

與 legacy `commands/trade_intent.py` 並行、不互相 import：寫入新的
`trade_intent_core` + 衛星表。類別沿用與舊版相同的名稱（差在所屬模組），
讓未來 cutover 只是把 endpoint 的 import 從 `trade_intent` 換成
`trade_intent_core`，退役時只刪舊模組、新模組零改動。

PR3（cutover）已補上訂閱 reconcile 與建單即觸發，兩者都是「接過去不讓既有功能退化」的
必要件。仍**不含** TWAP（`create_twap` / slice 連動）→ PR4。
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.commands.trigger_intent_core import (
    TriggerCoreInput,
    apply_trailing_baseline_update,
    persist_core_trigger,
)
from app.domain.price import InvalidTypeError, PriceRequest, PriceService, SecurityType
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trade_intent import (
    MARKET_ORDER_STRATEGIES,
    SymbolIntentLimitExceededError,
    TradeIntentData,
    UserIntentLimitExceededError,
    derive_order_side,
)
from app.domain.trading_session import TradingSessionService
from app.domain.trigger_event import quote_snapshot_to_jsonb
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from app.services.kill_switch import KillSwitchProvider
from app.services.quote.base import QuoteProvider, QuoteProviderError, QuoteSnapshot
from app.services.quote.current_price import CurrentPriceProvider
from app.services.quote.intent_reconciler import reconcile_after_terminal_transition, reconcile_on_create
from app.services.symbol import SymbolService

logger = logging.getLogger(__name__)

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
    """驗證 + §15 限額 + 寫入新表 + 訂閱 + 建單即觸發，單一 transaction。

    交易形狀（與舊軌同）：
    1. 驗證 → `repo.create` flush（不 commit）。已 flush 的列只有本 session 看得到，
       dispatcher / 其他讀者在 commit 前看不見。
    2. `reconcile_on_create` 訂閱報價。失敗會連同委託一起回滾（仍在同一交易內）。
    3. 若落 `active` 且當下報價已達標（§15），inline 寫 trigger / notification 並把
       status 推到 triggered——摺進同一交易，讓 dispatcher 不可能觀察到
       「已 active 但尚未觸發」的空窗。
    4. commit：委託（+ 觸發稽核 + 通知）一次落地。
    5. commit 後 `find_by_id` materialise server 端時間戳與最終 status。

    報價取不到不擋建單（§15）：委託以 active 落地，之後由 robot #2 補觸發。
    """

    def __init__(
        self,
        symbol_service: SymbolService,
        session_service: TradingSessionService,
        intent_repo: TradeIntentCoreRepository,
        quote_provider: QuoteProvider,
        evaluator: QuoteEvaluator,
        db: Session,
        kill_switch: KillSwitchProvider | None = None,
        limits: IntentLimits | None = None,
    ) -> None:
        self._symbol_service = symbol_service
        self._session_service = session_service
        self._intent_repo = intent_repo
        self._quote_provider = quote_provider
        self._evaluator = evaluator
        self._db = db
        self._kill_switch = kill_switch
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

            # 6. commit 前先訂閱：訂閱失敗（allowlist / 額度）連同委託一起回滾，
            #    避免留下一張永遠收不到報價的單。
            reconcile_on_create(self._quote_provider, inp.symbol)

            # 7. 建單即達標則就地觸發（§15）。scheduled 單跳過，等隔日啟用路徑。
            #    kill switch 開啟時連 inline 觸發一起停（不只 dispatcher）：委託照樣以
            #    active 落地，但不產 TriggerEvent、不發通知（spec §18）。
            halted = self._kill_switch is not None and self._kill_switch.is_halted()
            if initial_status == "active" and not halted:
                self._apply_inline_trigger_if_quote_met(intent_id, inp.owner_user_id, inp.symbol, inp.strategy, now)

            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        # 8. commit 後 materialise（created_at / updated_at 等 server 值，以及步驟 7 若有
        #    觸發的 triggered_at / status）
        return self._intent_repo.find_by_id(intent_id, inp.owner_user_id)

    def _apply_inline_trigger_if_quote_met(
        self,
        intent_id: UUID,
        owner_user_id: UUID,
        symbol: str,
        strategy: str,
        now: datetime,
    ) -> None:
        """取當下報價，達標就在本交易內寫觸發下游。

        剛 flush 的列尚不可被其他 session 看見，故不需 `SELECT FOR UPDATE`——這也是
        `persist_core_trigger` 的 `WHERE status='active'` 在此不可能落空的原因。
        報價取不到不擋建單：記 log 後照樣以 active commit。
        """

        quote = self._fetch_quote_for_create(intent_id, symbol, strategy)
        if quote is None:
            return

        # 從 session 讀回剛寫入的核心 + 衛星，組成 evaluator / persist 要的攤平 data。
        # 列已在本交易內，SELECT 不會多跑一趟 broker。
        intent = self._intent_repo.find_by_id(intent_id, owner_user_id)

        result = self._evaluator.evaluate(quote, intent, now)
        if result.baseline_updated_at is not None:
            intent = apply_trailing_baseline_update(self._intent_repo, intent, result)
        if not result.should_trigger:
            return

        # evaluator 保證 should_trigger=True 時這兩欄有值；用顯式檢查而非 assert，
        # 讓不變量在 `python -O` 下依然成立。
        if result.trigger_price is None or result.trigger_reference_price_type is None:
            raise RuntimeError(f"Evaluator returned should_trigger=True but trigger fields are None: {result}")

        persist_core_trigger(
            self._db,
            intent,
            TriggerCoreInput(
                trigger_price=result.trigger_price,
                trigger_reference_price_type=result.trigger_reference_price_type,
                fallback_used=result.fallback_used,
                quote_snapshot=quote_snapshot_to_jsonb(quote),
                quote_time=quote.quote_time,
            ),
        )

    def _fetch_quote_for_create(self, intent_id: UUID, symbol: str, strategy: str) -> QuoteSnapshot | None:
        """建單當下的報價；取不到回 None（非阻斷）。

        市價單先試 REST 現價（若 provider 支援）再退回串流快取：剛訂閱的 symbol 可能還沒推過
        任何一筆串流報價，而市價單本該立刻成交，等下一 tick 的體感是「按了沒反應」。條件單
        不走這條——它們本來就在等條件成立，多打一次 REST 沒有價值（與舊軌同）。
        """

        if strategy in MARKET_ORDER_STRATEGIES and isinstance(self._quote_provider, CurrentPriceProvider):
            try:
                return self._quote_provider.get_current_price(symbol)
            except QuoteProviderError:
                pass
        try:
            quotes = self._quote_provider.get_quotes([symbol])
        # 廣義 QuoteProviderError 而非只有 QuoteUnavailableError：本方法的契約是「取不到報價
        # 不擋建單」，任何 provider 端失敗都該讓委託以 active 落地、交給 robot #2 下次補觸發。
        # 收窄成 Unavailable 會讓別種 provider 錯誤把整筆建單回滾（舊軌市價單路徑即為廣義 catch）。
        # 訂閱失敗仍會擋建單——那是上面 reconcile_on_create 的事，與這裡無關。
        except QuoteProviderError as exc:
            logger.warning("create: quote unavailable for %s, intent %s stays active: %s", symbol, intent_id, exc)
            return None
        if not quotes:
            logger.warning("create: quote unavailable for %s, intent %s stays active", symbol, intent_id)
            return None
        return quotes[0]


class CancelTradeIntentCommand:
    """取消委託並在沒有別的單需要該 symbol 時退訂。

    交易形狀：cancel-flush → commit（Phase A，必成）；接著盡力而為的訂閱清理（Phase B）。
    Phase B 失敗只會留下一個殘餘訂閱，下次啟動 reconcile 會掃掉——DB 寫入已落地，
    API 仍必須回報取消成功。
    """

    def __init__(
        self,
        intent_repo: TradeIntentCoreRepository,
        quote_provider: QuoteProvider,
        db: Session,
    ) -> None:
        self._intent_repo = intent_repo
        self._quote_provider = quote_provider
        self._db = db

    def execute(self, inp: CancelTradeIntentInput) -> TradeIntentData:
        try:
            intent = self._intent_repo.cancel(inp.intent_id, inp.owner_user_id)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        # Phase B：盡力而為的退訂。catch 廣義 Exception（涵蓋殘量查詢的 SQLAlchemyError
        # 與 provider 退訂路徑可能拋的任何東西），確保取消 API 不會因清理失敗而 500。
        try:
            reconcile_after_terminal_transition(self._quote_provider, self._intent_repo, intent.symbol)
        except Exception:
            logger.warning(
                "post-cancel reconcile failed; leaving cleanup to startup reconciler",
                extra={"symbol": intent.symbol, "intent_id": str(intent.id)},
                exc_info=True,
            )
        return intent
