"""Handle Telegram text and callbacks for one shared group draft."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

from app.commands.trade_intent_core import CreateTradeIntentCommand, CreateTradeIntentInput
from app.core.config import Settings
from app.domain.price import PriceRequest, PriceService, SecurityType
from app.domain.symbol_errors import SymbolError
from app.domain.telegram_intent import (
    CAPABILITY_LABELS,
    CAPABILITY_TO_NOTIFY_ONLY_STRATEGY,
    CAPABILITY_TO_TRANSACTION_MODE,
    LIMIT_CAPABILITIES,
    MARKET_CAPABILITIES,
    CapabilityId,
    TelegramIntentDecision,
    TelegramIntentInteractionData,
    TelegramIntentLlmError,
)
from app.domain.trade_intent import DuplicateIntentError
from app.repositories.telegram_intent_repository import TelegramIntentInteractionRepository
from app.repositories.user_repository import UserRepository
from app.services.symbol import SymbolService
from app.services.telegram_intent_llm import TelegramIntentDecider

logger = logging.getLogger(__name__)

INTERACTION_TTL = timedelta(minutes=5)

LLM_FAILURE_REPLY = "暫時無法判斷需求，請稍後再試。"
CURRENT_DRAFT_REPLY = "目前有一筆尚未完成的提醒，請先補充它或按「取消」後再建立新的提醒。"
EXPIRED_REPLY = "此確認已過期，請重新輸入需求。"
CANCELLED_REPLY = "已取消。"
ALREADY_CONFIRMED_REPLY = "此確認已處理，提醒已建立。"
CONFIRMING_REPLY = "此確認正在處理，請稍候。"
FIELD_ERROR_REPLY = "欄位格式不符合規則，請重新輸入標的、張數與價格。"
UNSUPPORTED_REPLY = "目前只支援限價買進、限價賣出、市價買進、市價賣出。"
USER_PAGE_ERROR_REPLY = "無法建立提醒，請確認標的是可交易的台股或 ETF。"
DUPLICATE_INTENT_REPLY = "已有相同提醒，請到使用者頁面查看或取消後再建立。"


@dataclass(frozen=True, slots=True)
class TelegramReply:
    chat_id: str
    text: str
    reply_markup: dict[str, object] | None = None
    interaction_id: UUID | None = None
    bot_message_id: int | None = None


@dataclass(frozen=True, slots=True)
class HandleTelegramMessageInput:
    chat_id: str
    text: str


@dataclass(frozen=True, slots=True)
class HandleTelegramCallbackInput:
    chat_id: str
    data: str
    message_id: int | None = None


class TelegramIntentCommand:
    def __init__(
        self,
        *,
        settings: Settings,
        interactions: TelegramIntentInteractionRepository,
        users: UserRepository,
        symbol_service: SymbolService,
        decider: TelegramIntentDecider,
        create_intent: CreateTradeIntentCommand,
    ) -> None:
        self._settings = settings
        self._interactions = interactions
        self._users = users
        self._symbols = symbol_service
        self._decider = decider
        self._create_intent = create_intent

    def handle_message(self, inp: HandleTelegramMessageInput) -> TelegramReply | None:
        if not self._chat_is_allowed(inp.chat_id):
            return None
        owner_user_id = self._resolve_owner_user_id()
        if owner_user_id is None:
            return None

        now = datetime.now(UTC)
        pending = self._interactions.find_pending_for_chat(inp.chat_id, for_update=True)
        if pending is not None and pending.status == "confirming":
            return TelegramReply(
                chat_id=inp.chat_id,
                text=CONFIRMING_REPLY,
                reply_markup={"inline_keyboard": []},
                interaction_id=pending.id,
                bot_message_id=pending.bot_message_id,
            )
        if pending is not None and pending.expires_at <= now:
            self._interactions.mark_status(pending.id, "expired", chat_id=inp.chat_id)
            pending = None

        try:
            decision = self._decider.decide(
                message=inp.text,
                previous_capability_id=pending.capability_id if pending is not None else None,
                previous_payload=pending.payload if pending is not None else None,
            )
        except TelegramIntentLlmError as exc:
            # Do not log the Telegram message, model response, or credentials.
            # The decider error is intentionally reduced to a safe operational
            # category such as an HTTP status or schema field failure.
            logger.warning("telegram intent LLM classification failed: %s", exc)
            return TelegramReply(chat_id=inp.chat_id, text=LLM_FAILURE_REPLY)

        # Ordinary group chat is deliberately silent.  A pending draft is left
        # untouched so a casual message cannot erase a user's confirmation.
        if decision.decision == "unsupported":
            return None
        if decision.decision == "ambiguous":
            return self._current_draft_reply(inp.chat_id, pending) if pending is not None else None

        capability = decision.capability_id
        if capability not in CAPABILITY_TO_NOTIFY_ONLY_STRATEGY:
            return self._current_draft_reply(inp.chat_id, pending) if pending is not None else None

        if pending is not None and self._is_different_request(pending, decision):
            return self._current_draft_reply(inp.chat_id, pending)

        if decision.decision == "needs_clarification":
            return self._save_interaction(
                owner_user_id=owner_user_id,
                chat_id=inp.chat_id,
                pending=pending,
                decision=decision,
                capability=capability,
                kind="clarification",
                missing_fields=list(decision.missing_fields),
                now=now,
            )

        if decision.decision == "draft_intent":
            validation_error = self._validate_decision(decision)
            if validation_error is not None:
                return TelegramReply(chat_id=inp.chat_id, text=validation_error)
            return self._save_interaction(
                owner_user_id=owner_user_id,
                chat_id=inp.chat_id,
                pending=pending,
                decision=decision,
                capability=capability,
                kind="draft",
                missing_fields=[],
                now=now,
            )

        return None

    def handle_callback(self, inp: HandleTelegramCallbackInput) -> TelegramReply | None:
        if not self._chat_is_allowed(inp.chat_id):
            return None
        parsed = _parse_callback_data(inp.data)
        if parsed is None:
            return TelegramReply(chat_id=inp.chat_id, text=UNSUPPORTED_REPLY)
        action, interaction_id = parsed
        interaction = self._interactions.find_by_id(interaction_id, chat_id=inp.chat_id, for_update=True)
        if interaction is None:
            return TelegramReply(chat_id=inp.chat_id, text=EXPIRED_REPLY)
        # A real inline-button callback always identifies the bot message that
        # carried the button.  Require an exact match so a known interaction UUID
        # cannot be confirmed through a hand-crafted callback without its message.
        if interaction.bot_message_id is None or inp.message_id != interaction.bot_message_id:
            return None

        now = datetime.now(UTC)
        if interaction.status == "confirming":
            return TelegramReply(chat_id=inp.chat_id, text=CONFIRMING_REPLY, interaction_id=interaction.id)
        if interaction.status != "pending":
            if interaction.status == "confirmed":
                return TelegramReply(chat_id=inp.chat_id, text=ALREADY_CONFIRMED_REPLY)
            if interaction.status == "expired":
                return TelegramReply(chat_id=inp.chat_id, text=EXPIRED_REPLY)
            return TelegramReply(chat_id=inp.chat_id, text=CANCELLED_REPLY)
        if interaction.expires_at <= now:
            self._interactions.mark_status(interaction.id, "expired", chat_id=inp.chat_id)
            return TelegramReply(chat_id=inp.chat_id, text=EXPIRED_REPLY)

        if action == "cancel":
            if not self._interactions.mark_status(interaction.id, "cancelled", chat_id=inp.chat_id):
                return TelegramReply(chat_id=inp.chat_id, text=CANCELLED_REPLY)
            return TelegramReply(chat_id=inp.chat_id, text=CANCELLED_REPLY, interaction_id=interaction.id)

        if action != "confirm" or interaction.kind != "draft":
            return TelegramReply(chat_id=inp.chat_id, text=UNSUPPORTED_REPLY, interaction_id=interaction.id)

        try:
            create_input = _create_input_from_payload(interaction.payload, interaction.owner_user_id)
        except ValueError:
            # Do not claim a malformed persisted draft.  A valid draft can only
            # be produced through the strict LLM boundary, but keeping this
            # guard before the conditional claim prevents a corrupt row from
            # being stranded in ``confirming``.
            return TelegramReply(chat_id=inp.chat_id, text=FIELD_ERROR_REPLY, interaction_id=interaction.id)

        if not self._interactions.claim_confirming(interaction.id, chat_id=inp.chat_id):
            current = self._interactions.find_by_id(interaction.id, chat_id=inp.chat_id)
            if current is not None and current.status == "confirming":
                return TelegramReply(chat_id=inp.chat_id, text=CONFIRMING_REPLY, interaction_id=interaction.id)
            return TelegramReply(chat_id=inp.chat_id, text=ALREADY_CONFIRMED_REPLY, interaction_id=interaction.id)

        try:
            intent = self._create_intent.execute(create_input)
        except DuplicateIntentError:
            self._interactions.mark_status(interaction.id, "cancelled", chat_id=inp.chat_id)
            return TelegramReply(chat_id=inp.chat_id, text=DUPLICATE_INTENT_REPLY, interaction_id=interaction.id)
        except Exception:
            # The core command rolls its own transaction back.  Keep the draft
            # pending so the user can retry after a transient broker/DB failure.
            return TelegramReply(chat_id=inp.chat_id, text=USER_PAGE_ERROR_REPLY, interaction_id=interaction.id)

        if not self._interactions.mark_confirmed(
            interaction.id,
            chat_id=inp.chat_id,
            confirmed_at=now,
            trade_intent_id=intent.id,
        ):
            # A repeated callback can arrive after another request won the
            # conditional transition.  The create command has an acknowledged
            # crash window, but ordinary repeats do not create a second row.
            current = self._interactions.find_by_id(interaction.id, chat_id=inp.chat_id)
            if current is not None and current.status == "confirmed":
                return TelegramReply(chat_id=inp.chat_id, text=ALREADY_CONFIRMED_REPLY, interaction_id=interaction.id)
            return TelegramReply(chat_id=inp.chat_id, text=ALREADY_CONFIRMED_REPLY, interaction_id=interaction.id)
        return TelegramReply(
            chat_id=inp.chat_id,
            text=_created_reply(interaction.payload),
            interaction_id=interaction.id,
            bot_message_id=interaction.bot_message_id,
        )

    def _chat_is_allowed(self, chat_id: str) -> bool:
        configured = (self._settings.telegram_chat_id or "").strip()
        return bool(configured) and chat_id == configured

    def _resolve_owner_user_id(self) -> UUID | None:
        user = self._users.get_by_email(self._settings.telegram_owner_email)
        if user is None or user.status != "active":
            return None
        return user.id

    def _is_different_request(self, pending: TelegramIntentInteractionData, decision: TelegramIntentDecision) -> bool:
        if decision.capability_id != pending.capability_id:
            return True
        previous_symbol = pending.payload.get("symbol")
        if isinstance(previous_symbol, str) and decision.symbol is not None and previous_symbol != decision.symbol:
            return True

        # An incomplete clarification is intentionally mergeable: later text
        # supplies its missing values.  Once a complete draft exists, however,
        # changing quantity or price is a distinct trade request and must not
        # silently overwrite the confirmation currently shown to the group.
        if pending.kind != "draft":
            return False
        if decision.decision != "draft_intent":
            return True
        previous_quantity = pending.payload.get("quantityLots")
        if decision.quantity_lots is not None and decision.quantity_lots != previous_quantity:
            return True
        previous_price = pending.payload.get("targetPrice")
        if decision.target_price is not None:
            try:
                if Decimal(str(previous_price)) != decision.target_price:
                    return True
            except InvalidOperation:
                return True
        return False

    def _save_interaction(
        self,
        *,
        owner_user_id: UUID,
        chat_id: str,
        pending: TelegramIntentInteractionData | None,
        decision: TelegramIntentDecision,
        capability: CapabilityId,
        kind: str,
        missing_fields: list[str],
        now: datetime,
    ) -> TelegramReply:
        payload = _payload_from_decision(decision, capability, previous=pending.payload if pending else None)
        strategy = CAPABILITY_TO_NOTIFY_ONLY_STRATEGY[capability]
        expires_at = now + INTERACTION_TTL
        attempts = (pending.clarification_attempt_count + 1) if pending is not None else 1
        interaction = None
        if pending is not None:
            interaction = self._interactions.update_pending(
                pending.id,
                chat_id=chat_id,
                kind=kind,  # type: ignore[arg-type]
                capability_id=capability,
                strategy=strategy,
                payload=payload,
                missing_fields=missing_fields,
                clarification_attempt_count=attempts,
                expires_at=expires_at,
            )
        if interaction is None:
            interaction = self._interactions.create(
                owner_user_id=owner_user_id,
                telegram_chat_id=chat_id,
                kind=kind,  # type: ignore[arg-type]
                capability_id=capability,
                strategy=strategy,
                payload=payload,
                missing_fields=missing_fields,
                clarification_attempt_count=attempts,
                expires_at=expires_at,
            )
        if kind == "clarification":
            text = _clarification_reply(tuple(missing_fields))
            markup = _cancel_markup(interaction.id)
        else:
            text = _confirmation_text(payload)
            markup = _confirmation_markup(interaction.id)
        return TelegramReply(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            interaction_id=interaction.id,
            bot_message_id=interaction.bot_message_id,
        )

    def _current_draft_reply(self, chat_id: str, pending: TelegramIntentInteractionData) -> TelegramReply:
        if pending.kind == "draft":
            text = f"{CURRENT_DRAFT_REPLY}\n\n{_confirmation_text(pending.payload)}"
            markup = _confirmation_markup(pending.id)
        else:
            text = f"{CURRENT_DRAFT_REPLY}\n\n{_clarification_reply(tuple(pending.missing_fields))}"
            markup = _cancel_markup(pending.id)
        return TelegramReply(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            interaction_id=pending.id,
            bot_message_id=pending.bot_message_id,
        )

    def _validate_decision(self, decision: TelegramIntentDecision) -> str | None:
        capability = decision.capability_id
        if capability not in CAPABILITY_TO_NOTIFY_ONLY_STRATEGY:
            return UNSUPPORTED_REPLY
        if not isinstance(decision.symbol, str) or not decision.symbol.isdigit():
            return FIELD_ERROR_REPLY
        if not isinstance(decision.quantity_lots, int) or isinstance(decision.quantity_lots, bool):
            return FIELD_ERROR_REPLY
        if decision.quantity_lots <= 0:
            return FIELD_ERROR_REPLY
        if capability in LIMIT_CAPABILITIES and decision.target_price is None:
            return FIELD_ERROR_REPLY
        if capability in MARKET_CAPABILITIES and decision.target_price is not None:
            return FIELD_ERROR_REPLY
        try:
            symbol_obj = self._symbols.get_tradable_symbol(decision.symbol)
            security_type = SecurityType(symbol_obj.instrument_type)
            if capability in LIMIT_CAPABILITIES and decision.target_price is not None:
                PriceService.validate(
                    PriceRequest(
                        type=security_type,
                        price=str(decision.target_price),
                        amount=decision.quantity_lots,
                    )
                )
        except (SymbolError, ValueError):
            return USER_PAGE_ERROR_REPLY
        return None


def _payload_from_decision(
    decision: TelegramIntentDecision,
    capability: CapabilityId,
    *,
    previous: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = dict(previous or {})
    payload.update(
        {
            "capabilityId": capability,
            "strategy": CAPABILITY_TO_NOTIFY_ONLY_STRATEGY[capability],
            "typeLabel": CAPABILITY_LABELS[capability],
        }
    )
    if decision.symbol is not None:
        payload["symbol"] = decision.symbol
    if decision.quantity_lots is not None:
        payload["quantityLots"] = decision.quantity_lots
    if decision.target_price is not None:
        payload["targetPrice"] = str(decision.target_price)
    elif capability in MARKET_CAPABILITIES:
        payload.pop("targetPrice", None)
    return payload


def _confirmation_text(payload: dict[str, object]) -> str:
    return "\n".join(
        [
            "請確認建立提醒",
            f"類型：{payload['typeLabel']}",
            f"標的：{payload['symbol']}",
            f"張數：{payload['quantityLots']} 張",
            f"價格：{payload.get('targetPrice', '市價')} 元" if "targetPrice" in payload else "價格：市價",
            "有效期間：當日有效",
            "",
            "確認後會建立通知型交易意圖，不會送出券商委託。",
        ]
    )


def _created_reply(payload: dict[str, object]) -> str:
    return "\n".join(
        [
            "已建立提醒。",
            f"類型：{payload['typeLabel']}",
            f"標的：{payload['symbol']}",
            f"張數：{payload['quantityLots']} 張",
            f"價格：{payload.get('targetPrice', '市價')} 元" if "targetPrice" in payload else "價格：市價",
            "有效期間：當日有效",
        ]
    )


def _clarification_reply(missing_fields: tuple[str, ...]) -> str:
    fields = set(missing_fields)
    if "quantityLots" in fields and "targetPrice" in fields:
        return "請提供張數與價格，例如：0050 1 張 180 元。"
    if "quantityLots" in fields:
        return "請提供張數，例如：0050 1 張。"
    if "targetPrice" in fields:
        return "請提供限價價格，例如：0050 180 元。"
    if "symbol" in fields:
        return "請提供標的，例如：0050。"
    return "請補充標的、張數與價格。"


def _cancel_markup(interaction_id: UUID) -> dict[str, object]:
    return {"inline_keyboard": [[{"text": "取消", "callback_data": f"tg_intent:cancel:{interaction_id}"}]]}


def _confirmation_markup(interaction_id: UUID) -> dict[str, object]:
    return {
        "inline_keyboard": [
            [
                {"text": "確認建立", "callback_data": f"tg_intent:confirm:{interaction_id}"},
                {"text": "取消", "callback_data": f"tg_intent:cancel:{interaction_id}"},
            ]
        ]
    }


def _parse_callback_data(data: str) -> tuple[str, UUID] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "tg_intent" or parts[1] not in {"confirm", "cancel"}:
        return None
    try:
        return parts[1], UUID(parts[2])
    except ValueError:
        return None


def _create_input_from_payload(payload: dict[str, object], owner_user_id: UUID) -> CreateTradeIntentInput:
    capability = payload.get("capabilityId")
    strategy = payload.get("strategy")
    symbol = payload.get("symbol")
    quantity_lots = payload.get("quantityLots")
    target_price = payload.get("targetPrice")
    if capability not in CAPABILITY_TO_NOTIFY_ONLY_STRATEGY:
        raise ValueError("invalid telegram capability")
    expected_strategy = CAPABILITY_TO_NOTIFY_ONLY_STRATEGY[capability]
    if strategy != expected_strategy or not isinstance(symbol, str):
        raise ValueError("invalid telegram intent payload")
    if not isinstance(quantity_lots, int) or isinstance(quantity_lots, bool) or quantity_lots <= 0:
        raise ValueError("invalid telegram quantity")
    if capability in LIMIT_CAPABILITIES and not isinstance(target_price, str):
        raise ValueError("limit intent requires target price")
    if capability in MARKET_CAPABILITIES and target_price is not None:
        raise ValueError("market intent cannot contain target price")
    return CreateTradeIntentInput(
        symbol=symbol,
        strategy=expected_strategy,
        quantity_lots=quantity_lots,
        target_price=target_price if isinstance(target_price, str) else None,
        transaction_mode=CAPABILITY_TO_TRANSACTION_MODE[capability],
        owner_user_id=owner_user_id,
    )
