import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.price import InvalidTypeError, PriceRequest, PriceService, SecurityType
from app.domain.trade_intent import TradeIntentData
from app.domain.trading_session import TradingSessionService
from app.repositories.intent_repository import IntentRepository
from app.services.quote.base import QuoteProvider, QuoteProviderError
from app.services.quote.intent_reconciler import reconcile_after_terminal_transition, reconcile_on_create
from app.services.symbol import SymbolService

logger = logging.getLogger(__name__)

# V0.5 fixed values
_EXECUTION_MODE = "notify_only"
_TIME_IN_FORCE = "day"

# trigger price reference by strategy
_TRIGGER_REF: dict[str, str] = {
    "buy_price_alert": "ask",
    "sell_price_alert": "bid",
}


@dataclass(frozen=True)
class CreateTradeIntentInput:
    symbol: str
    strategy: str
    quantity_lots: int
    target_price: str  # kept as str to preserve decimal precision
    owner_user_id: UUID


@dataclass(frozen=True)
class CancelTradeIntentInput:
    intent_id: UUID
    owner_user_id: UUID


class CreateTradeIntentCommand:
    """Create a trade intent and reconcile its quote subscription atomically.

    Transaction shape: validate → insert (flushed, not committed) → reconcile
    subscription → commit. If reconcile raises (allowlist / quota), the entire
    transaction rolls back so the DB never contains an intent with no upstream
    quote subscription.
    """

    def __init__(
        self,
        symbol_service: SymbolService,
        session_service: TradingSessionService,
        intent_repo: IntentRepository,
        quote_provider: QuoteProvider,
        db: Session,
    ) -> None:
        self._symbol_service = symbol_service
        self._session_service = session_service
        self._intent_repo = intent_repo
        self._quote_provider = quote_provider
        self._db = db

    def execute(self, inp: CreateTradeIntentInput) -> TradeIntentData:
        try:
            # 1. validate symbol existence and tradability
            symbol_obj = self._symbol_service.get_tradable_symbol(inp.symbol)

            # 2. validate price and tick size via PriceService
            try:
                security_type = SecurityType(symbol_obj.instrument_type)
            except ValueError:
                raise InvalidTypeError(symbol_obj.instrument_type, "must be stock or etf") from None
            effective_price = PriceService.validate(
                PriceRequest(
                    type=security_type,
                    price=inp.target_price,
                    amount=inp.quantity_lots,
                )
            )

            # 3. determine trading_date and initial status
            now = self._session_service.now_taipei()
            trading_date = self._session_service.get_day_intent_trading_date(now)
            initial_status = self._session_service.get_initial_day_intent_status(now)

            # 4. persist (flush only — commit happens below after reconcile)
            trigger_ref = _TRIGGER_REF.get(inp.strategy)
            if trigger_ref is None:
                raise ValueError(f"Unsupported strategy: {inp.strategy}")

            intent = self._intent_repo.create(
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
            )

            # 5. reconcile subscription before commit — fail here rolls back the intent
            reconcile_on_create(self._quote_provider, intent.symbol)

            self._db.commit()
            return intent
        except Exception:
            self._db.rollback()
            raise


class CancelTradeIntentCommand:
    """Cancel an intent and release its quote subscription when nothing else needs it."""

    def __init__(
        self,
        intent_repo: IntentRepository,
        quote_provider: QuoteProvider,
        db: Session,
    ) -> None:
        self._intent_repo = intent_repo
        self._quote_provider = quote_provider
        self._db = db

    def execute(self, inp: CancelTradeIntentInput) -> TradeIntentData:
        try:
            intent = self._intent_repo.cancel(inp.intent_id, inp.owner_user_id)
            try:
                reconcile_after_terminal_transition(self._quote_provider, self._intent_repo, intent.symbol)
            except QuoteProviderError:
                # The cancel itself succeeded; a stale subscription leak will be
                # cleaned up on next startup reconcile. Don't fail the API call.
                logger.warning(
                    "unsubscribe failed during cancel reconcile",
                    extra={"symbol": intent.symbol, "intent_id": str(intent.id)},
                )
            self._db.commit()
            return intent
        except Exception:
            self._db.rollback()
            raise
