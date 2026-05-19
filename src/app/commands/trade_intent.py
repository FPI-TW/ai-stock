from dataclasses import dataclass
from uuid import UUID

from app.domain.price import InvalidTypeError, PriceRequest, PriceService, SecurityType
from app.domain.trade_intent import TradeIntentData
from app.domain.trading_session import TradingSessionService
from app.repositories.intent_repository import IntentRepository
from app.services.symbol import SymbolService

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
    def __init__(
        self,
        symbol_service: SymbolService,
        session_service: TradingSessionService,
        intent_repo: IntentRepository,
    ) -> None:
        self._symbol_service = symbol_service
        self._session_service = session_service
        self._intent_repo = intent_repo

    def execute(self, inp: CreateTradeIntentInput) -> TradeIntentData:
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

        # 4. persist
        trigger_ref = _TRIGGER_REF.get(inp.strategy)
        if trigger_ref is None:
            raise ValueError(f"Unsupported strategy: {inp.strategy}")

        return self._intent_repo.create(
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


class CancelTradeIntentCommand:
    def __init__(self, intent_repo: IntentRepository) -> None:
        self._intent_repo = intent_repo

    def execute(self, inp: CancelTradeIntentInput) -> TradeIntentData:
        return self._intent_repo.cancel(inp.intent_id, inp.owner_user_id)
