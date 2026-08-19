from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.commands.telegram_intent import (
    HandleTelegramCallbackInput,
    HandleTelegramMessageInput,
    TelegramIntentCommand,
)
from app.commands.trade_intent_core import CreateTradeIntentInput
from app.core.config import Settings
from app.domain.telegram_intent import TelegramIntentDecision, TelegramIntentInteractionData

OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _settings() -> Settings:
    return Settings(
        LOCAL_USER_ID=OWNER_ID,
        QUOTE_PROVIDER="in_memory",
        TELEGRAM_CHAT_ID="configured-group",
        TELEGRAM_OWNER_EMAIL="admin@tingfong.com",
    )


def _interaction(*, kind: str = "draft", capability: str = "limit_buy") -> TelegramIntentInteractionData:
    return TelegramIntentInteractionData(
        id=uuid4(),
        owner_user_id=OWNER_ID,
        telegram_chat_id="configured-group",
        kind=kind,  # type: ignore[arg-type]
        capability_id=capability,  # type: ignore[arg-type]
        strategy=f"{capability}_order",
        payload={
            "capabilityId": capability,
            "strategy": f"{capability}_order",
            "typeLabel": "限價買進提醒",
            "symbol": "0050",
            "quantityLots": 1,
            "targetPrice": "180",
        },
        missing_fields=[],
        clarification_attempt_count=0,
        status="pending",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        bot_message_id=321,
    )


class _Interactions:
    def __init__(self, pending: TelegramIntentInteractionData | None) -> None:
        self.pending = pending
        self.claimed = False
        self.confirmed = False

    def find_pending_for_chat(self, chat_id: str, *, for_update: bool = False) -> TelegramIntentInteractionData | None:
        return self.pending if self.pending is not None and self.pending.telegram_chat_id == chat_id else None

    def find_by_id(
        self,
        interaction_id: UUID,
        *,
        chat_id: str | None = None,
        for_update: bool = False,
    ) -> TelegramIntentInteractionData | None:
        if self.pending is None or self.pending.id != interaction_id:
            return None
        if chat_id is not None and chat_id != self.pending.telegram_chat_id:
            return None
        return self.pending

    def create(self, **kwargs: object) -> TelegramIntentInteractionData:
        self.pending = TelegramIntentInteractionData(
            id=uuid4(),
            owner_user_id=cast(UUID, kwargs["owner_user_id"]),
            telegram_chat_id=cast(str, kwargs["telegram_chat_id"]),
            kind=cast(str, kwargs["kind"]),  # type: ignore[arg-type]
            capability_id=cast(str, kwargs["capability_id"]),  # type: ignore[arg-type]
            strategy=cast(str, kwargs["strategy"]),
            payload=cast(dict[str, object], kwargs["payload"]),
            missing_fields=cast(list[str], kwargs["missing_fields"]),
            clarification_attempt_count=cast(int, kwargs["clarification_attempt_count"]),
            status="pending",
            expires_at=cast(datetime, kwargs["expires_at"]),
        )
        assert self.pending is not None
        return self.pending

    def update_pending(self, interaction_id: UUID, **kwargs: object) -> TelegramIntentInteractionData | None:
        if self.pending is None or self.pending.id != interaction_id or self.pending.status != "pending":
            return None
        self.pending = replace(
            self.pending,
            kind=cast(str, kwargs["kind"]),  # type: ignore[arg-type]
            capability_id=cast(str, kwargs["capability_id"]),  # type: ignore[arg-type]
            strategy=cast(str, kwargs["strategy"]),
            payload=cast(dict[str, object], kwargs["payload"]),
            missing_fields=cast(list[str], kwargs["missing_fields"]),
            clarification_attempt_count=cast(int, kwargs["clarification_attempt_count"]),
            expires_at=cast(datetime, kwargs["expires_at"]),
        )
        assert self.pending is not None
        return self.pending

    def claim_confirming(self, interaction_id: UUID, *, chat_id: str) -> bool:
        if self.pending is None:
            return False
        if self.pending.status != "pending":
            return False
        self.claimed = True
        self.pending = replace(self.pending, status="confirming")
        return True

    def mark_confirmed(self, interaction_id: UUID, **kwargs: object) -> bool:
        assert self.pending is not None
        self.confirmed = True
        self.pending = replace(
            self.pending,
            status="confirmed",
            created_trade_intent_id=cast(UUID, kwargs["trade_intent_id"]),
        )
        return True

    def mark_status(self, interaction_id: UUID, status: str, *, chat_id: str | None = None) -> bool:
        assert self.pending is not None
        self.pending = replace(self.pending, status=status)  # type: ignore[arg-type]
        return True


class _Decider:
    def __init__(self, decision: TelegramIntentDecision) -> None:
        self.decision = decision

    def decide(self, **kwargs: object) -> TelegramIntentDecision:
        return self.decision


class _Users:
    def get_by_email(self, email: str) -> SimpleNamespace:
        return SimpleNamespace(id=OWNER_ID, status="active")


class _Symbols:
    def get_tradable_symbol(self, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(instrument_type="stock")


class _Create:
    def __init__(self, interactions: _Interactions) -> None:
        self.interactions = interactions
        self.input: object | None = None

    def execute(self, inp: object) -> SimpleNamespace:
        assert self.interactions.claimed is True
        self.input = inp
        return SimpleNamespace(id=uuid4())


def _command(
    interactions: _Interactions,
    decision: TelegramIntentDecision,
    create: _Create | None = None,
) -> TelegramIntentCommand:
    return TelegramIntentCommand(
        settings=_settings(),
        interactions=interactions,  # type: ignore[arg-type]
        users=_Users(),  # type: ignore[arg-type]
        symbol_service=_Symbols(),  # type: ignore[arg-type]
        decider=_Decider(decision),  # type: ignore[arg-type]
        create_intent=create or _Create(interactions),  # type: ignore[arg-type]
    )


def test_different_request_preserves_complete_draft_controls() -> None:
    interactions = _Interactions(_interaction())
    command = _command(interactions, TelegramIntentDecision(decision="ambiguous"))

    reply = command.handle_message(HandleTelegramMessageInput(chat_id="configured-group", text="new request"))

    assert reply is not None
    assert "確認建立" in reply.text
    assert reply.reply_markup is not None
    buttons = cast(list[list[dict[str, object]]], reply.reply_markup["inline_keyboard"])[0]
    assert any(str(button["callback_data"]).startswith("tg_intent:confirm:") for button in buttons)


@pytest.mark.parametrize(
    "decision",
    [
        TelegramIntentDecision(
            decision="draft_intent",
            capability_id="limit_buy",
            symbol="0050",
            quantity_lots=2,
            target_price=Decimal("180"),
        ),
        TelegramIntentDecision(
            decision="draft_intent",
            capability_id="limit_buy",
            symbol="0050",
            quantity_lots=1,
            target_price=Decimal("181"),
        ),
    ],
)
def test_complete_draft_is_not_overwritten_by_changed_quantity_or_price(
    decision: TelegramIntentDecision,
) -> None:
    pending = _interaction()
    interactions = _Interactions(pending)
    command = _command(interactions, decision)

    reply = command.handle_message(HandleTelegramMessageInput(chat_id="configured-group", text="new request"))

    assert reply is not None
    assert interactions.pending == pending
    assert reply.interaction_id == pending.id
    assert "確認建立" in reply.text


def test_non_trading_chat_is_silent_and_leaves_active_draft_unchanged() -> None:
    pending = _interaction()
    interactions = _Interactions(pending)
    command = _command(interactions, TelegramIntentDecision(decision="unsupported"))

    reply = command.handle_message(HandleTelegramMessageInput(chat_id="configured-group", text="今天天氣很好"))

    assert reply is None
    assert interactions.pending == pending


def test_supported_incomplete_request_creates_cancel_only_clarification() -> None:
    interactions = _Interactions(None)
    command = _command(
        interactions,
        TelegramIntentDecision(
            decision="needs_clarification",
            capability_id="limit_buy",
            symbol="0050",
            missing_fields=("quantityLots", "targetPrice"),
        ),
    )

    reply = command.handle_message(HandleTelegramMessageInput(chat_id="configured-group", text="買 0050"))

    assert reply is not None
    assert reply.interaction_id is not None
    assert reply.reply_markup == {
        "inline_keyboard": [[{"text": "取消", "callback_data": f"tg_intent:cancel:{reply.interaction_id}"}]]
    }
    assert interactions.pending is not None
    assert interactions.pending.kind == "clarification"
    assert interactions.pending.status == "pending"


def test_same_request_supplement_upgrades_clarification_and_reuses_message() -> None:
    pending = replace(
        _interaction(kind="clarification"),
        payload={
            "capabilityId": "limit_buy",
            "strategy": "limit_buy_order",
            "typeLabel": "限價買進提醒",
            "symbol": "0050",
        },
        missing_fields=["quantityLots", "targetPrice"],
    )
    interactions = _Interactions(pending)
    command = _command(
        interactions,
        TelegramIntentDecision(
            decision="draft_intent",
            capability_id="limit_buy",
            symbol="0050",
            quantity_lots=1,
            target_price=Decimal("180"),
        ),
    )

    reply = command.handle_message(HandleTelegramMessageInput(chat_id="configured-group", text="1 張 180 元"))

    assert reply is not None
    assert reply.interaction_id == pending.id
    assert reply.bot_message_id == pending.bot_message_id
    assert reply.reply_markup is not None
    assert "確認建立" in str(reply.reply_markup)
    assert interactions.pending is not None
    assert interactions.pending.kind == "draft"
    assert interactions.pending.missing_fields == []


def test_expired_draft_is_marked_expired_and_does_not_block_new_message() -> None:
    pending = replace(_interaction(), expires_at=datetime.now(UTC) - timedelta(seconds=1))
    interactions = _Interactions(pending)
    command = _command(interactions, TelegramIntentDecision(decision="unsupported"))

    reply = command.handle_message(HandleTelegramMessageInput(chat_id="configured-group", text="hello"))

    assert reply is None
    assert interactions.pending is not None
    assert interactions.pending.status == "expired"


def test_cancel_transitions_pending_without_creating_core_intent() -> None:
    pending = _interaction()
    interactions = _Interactions(pending)
    create = _Create(interactions)
    command = _command(interactions, TelegramIntentDecision(decision="unsupported"), create)

    reply = command.handle_callback(
        HandleTelegramCallbackInput(
            chat_id="configured-group",
            data=f"tg_intent:cancel:{pending.id}",
            message_id=pending.bot_message_id,
        )
    )

    assert reply is not None
    assert reply.text == "已取消。"
    assert interactions.pending is not None
    assert interactions.pending.status == "cancelled"
    assert create.input is None


def test_confirming_message_reuses_existing_bot_message() -> None:
    pending = replace(_interaction(), status="confirming")
    interactions = _Interactions(pending)
    command = _command(interactions, TelegramIntentDecision(decision="unsupported"))

    reply = command.handle_message(HandleTelegramMessageInput(chat_id="configured-group", text="hello"))

    assert reply is not None
    assert reply.bot_message_id == 321
    assert reply.reply_markup == {"inline_keyboard": []}


@pytest.mark.parametrize("message_id", [None, 999])
def test_callback_without_matching_message_is_ignored(message_id: int | None) -> None:
    interactions = _Interactions(_interaction())
    create = _Create(interactions)
    command = _command(interactions, TelegramIntentDecision(decision="unsupported"), create)
    assert interactions.pending is not None
    pending_id = interactions.pending.id

    reply = command.handle_callback(
        HandleTelegramCallbackInput(
            chat_id="configured-group",
            data=f"tg_intent:confirm:{pending_id}",
            message_id=message_id,
        )
    )

    assert reply is None
    assert create.input is None
    assert interactions.pending is not None
    assert interactions.pending.status == "pending"


def test_confirming_callback_does_not_create_again() -> None:
    pending = replace(_interaction(), status="confirming")
    interactions = _Interactions(pending)
    create = _Create(interactions)
    command = _command(interactions, TelegramIntentDecision(decision="unsupported"), create)

    reply = command.handle_callback(
        HandleTelegramCallbackInput(
            chat_id="configured-group",
            data=f"tg_intent:confirm:{pending.id}",
            message_id=pending.bot_message_id,
        )
    )

    assert reply is not None
    assert "處理" in reply.text
    assert create.input is None


def test_repeated_confirmed_callback_does_not_create_again() -> None:
    pending = replace(_interaction(), status="confirmed", created_trade_intent_id=uuid4())
    interactions = _Interactions(pending)
    create = _Create(interactions)
    command = _command(interactions, TelegramIntentDecision(decision="unsupported"), create)

    reply = command.handle_callback(
        HandleTelegramCallbackInput(
            chat_id="configured-group",
            data=f"tg_intent:confirm:{pending.id}",
            message_id=pending.bot_message_id,
        )
    )

    assert reply is not None
    assert "已處理" in reply.text
    assert create.input is None


@pytest.mark.parametrize(
    ("capability", "strategy", "transaction_mode", "target_price"),
    [
        ("limit_buy", "limit_buy_order", "single_notification", "180"),
        ("limit_sell", "limit_sell_order", "single_notification", "180"),
        ("market_buy", "market_buy_order", "partial_fill_allowed", None),
        ("market_sell", "market_sell_order", "partial_fill_allowed", None),
    ],
)
def test_confirm_maps_each_supported_capability_exactly(
    capability: str,
    strategy: str,
    transaction_mode: str,
    target_price: str | None,
) -> None:
    payload: dict[str, object] = {
        "capabilityId": capability,
        "strategy": strategy,
        "typeLabel": "測試提醒",
        "symbol": "0050",
        "quantityLots": 1,
    }
    if target_price is not None:
        payload["targetPrice"] = target_price
    pending = replace(
        _interaction(capability=capability),
        strategy=strategy,
        payload=payload,
    )
    interactions = _Interactions(pending)
    create = _Create(interactions)
    command = _command(interactions, TelegramIntentDecision(decision="unsupported"), create)

    reply = command.handle_callback(
        HandleTelegramCallbackInput(
            chat_id="configured-group",
            data=f"tg_intent:confirm:{pending.id}",
            message_id=pending.bot_message_id,
        )
    )

    assert reply is not None
    created_input = cast(CreateTradeIntentInput, create.input)
    assert created_input.strategy == strategy
    assert created_input.transaction_mode == transaction_mode
    assert created_input.target_price == target_price


def test_confirm_claims_before_core_command_and_maps_market_mode() -> None:
    pending = _interaction(capability="market_buy")
    pending = replace(
        pending,
        strategy="market_buy_order",
        payload={
            "capabilityId": "market_buy",
            "strategy": "market_buy_order",
            "typeLabel": "市價買進提醒",
            "symbol": "0050",
            "quantityLots": 2,
        },
    )
    interactions = _Interactions(pending)
    create = _Create(interactions)
    command = _command(interactions, TelegramIntentDecision(decision="unsupported"), create)

    reply = command.handle_callback(
        HandleTelegramCallbackInput(
            chat_id="configured-group",
            data=f"tg_intent:confirm:{pending.id}",
            message_id=pending.bot_message_id,
        )
    )

    assert reply is not None
    assert interactions.claimed is True
    assert interactions.confirmed is True
    assert create.input is not None
    created_input = cast(CreateTradeIntentInput, create.input)
    assert created_input.transaction_mode == "partial_fill_allowed"
    assert created_input.target_price is None
