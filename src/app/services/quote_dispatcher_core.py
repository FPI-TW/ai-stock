"""新軌 Quote → evaluation dispatcher（robot #2，模式丙 PR2 chunk3）。

與 legacy `QuoteEvaluationDispatcher` 並行：掃 `trade_intent_core` 的 active 單、用
**共用** evaluator 判到價、呼叫 `persist_core_trigger` 寫新軌觸發下游。註冊為報價來源的
第二個 listener（main.py `add_quote_listener`）；兩台各掃各表、一張單只在一張表 →
無重複觸發。

與 legacy dispatcher 的差異：
- 綁新 repo / persist，不 import legacy trigger 路徑。
- **不呼叫生命週期**（新軌 activate/expire 延後到後續增量；盤中建立的單已直接落 active）。
- 無 `SELECT FOR UPDATE`：competing trigger 靠 `persist_core_trigger` 的
  `WHERE status='active'` 條件更新（rowcount 0 → 跳過）+ `trade_intent_triggers`
  的 `trade_intent_id` UNIQUE 當最終 backstop。
"""

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.commands.intent_lifecycle import IntentLifecycleCommand
from app.commands.trigger_intent_core import (
    CoreIntentNotActiveError,
    TriggerCoreInput,
    apply_trailing_baseline_update,
    persist_core_trigger,
)
from app.domain.quote_evaluation import QuoteEvaluator
from app.domain.trading_session import TradingSessionService
from app.domain.trigger_event import DuplicateTriggerError, quote_snapshot_to_jsonb
from app.repositories.trade_intent_core_repository import TradeIntentCoreRepository
from app.services.kill_switch import KillSwitchProvider
from app.services.quote.base import QuoteSnapshot

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


class TradeIntentCoreDispatcher:
    """robot #2：對新軌 `trade_intent_core` 的報價→觸發 dispatcher。

    無狀態（只持有 thread-safe singletons）；每次 `dispatch` 開短命 session。所有
    例外吞掉並記錄，broker 執行緒不會看到失敗。
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        evaluator: QuoteEvaluator,
        session_service: TradingSessionService,
        kill_switch: KillSwitchProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._evaluator = evaluator
        self._session_service = session_service
        self._kill_switch = kill_switch

    def dispatch(self, snapshot: QuoteSnapshot) -> None:
        try:
            self._dispatch_inner(snapshot)
        except Exception:
            logger.exception("core quote dispatch failed on %s", snapshot.symbol)

    def _dispatch_inner(self, snapshot: QuoteSnapshot) -> None:
        if self._kill_switch is not None and self._kill_switch.is_halted():
            return
        now = self._session_service.now_taipei()
        with self._session_factory() as db:
            repo = TradeIntentCoreRepository(db)
            # 生命週期必須掛在這裡（與舊軌 dispatcher 同）：scheduled 單只有被啟用成 active
            # 才會進 system_list_active_by_symbols。若只靠 API 路徑跑，沒人打 API 的日子
            # 開盤後單子會一直停在 scheduled、整天不觸發。
            IntentLifecycleCommand(repo, self._session_service).run()
            intents = repo.system_list_active_by_symbols([snapshot.symbol])
            if not intents:
                return

            has_pending_writes = False
            for intent in intents:
                result = self._evaluator.evaluate(snapshot, intent, now)
                if result.baseline_updated_at is not None:
                    intent = apply_trailing_baseline_update(repo, intent, result)
                    has_pending_writes = True
                if not result.should_trigger:
                    continue
                if result.trigger_price is None or result.trigger_reference_price_type is None:
                    raise RuntimeError(f"Evaluator returned should_trigger=True but trigger fields are None: {result}")
                try:
                    with repo.begin_nested():
                        persist_core_trigger(
                            db,
                            intent,
                            TriggerCoreInput(
                                trigger_price=result.trigger_price,
                                trigger_reference_price_type=result.trigger_reference_price_type,
                                fallback_used=result.fallback_used,
                                quote_snapshot=quote_snapshot_to_jsonb(snapshot),
                                quote_time=snapshot.quote_time,
                            ),
                        )
                    has_pending_writes = True
                except (CoreIntentNotActiveError, DuplicateTriggerError) as exc:
                    # 與列出 actives 之間的競態、或他執行緒已觸發 → 安靜跳過。其餘整合性錯誤
                    # （persist 不會吞的 IntegrityError）往上拋給 dispatch 的 logger.exception。
                    logger.warning("core dispatch trigger skipped for intent %s: %s", intent.id, exc)
            if has_pending_writes:
                repo.commit()
