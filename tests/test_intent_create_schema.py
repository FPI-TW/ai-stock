"""Schema-layer contract tests for POST /trade-intents request body."""

from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.intent import (
    BuyPriceAlertCreateRequest,
    IntentCreateRequest,
    LimitBuyOrderCreateRequest,
    TrailingStopAlertCreateRequest,
)

_union_adapter: TypeAdapter[IntentCreateRequest] = TypeAdapter(IntentCreateRequest)
_limit_buy_adapter = TypeAdapter(LimitBuyOrderCreateRequest)
_trailing_adapter = TypeAdapter(TrailingStopAlertCreateRequest)


def test_create_intent_union_dispatches_existing_price_alert() -> None:
    request = _union_adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "buy_price_alert",
            "quantityLots": 1,
            "targetPrice": "600",
        }
    )

    assert isinstance(request, BuyPriceAlertCreateRequest)
    assert request.target_price == "600"


def test_create_intent_union_dispatches_limit_order_strategy() -> None:
    request = _union_adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "limit_buy_order",
            "quantityLots": 1,
            "targetPrice": "600",
        }
    )

    assert isinstance(request, LimitBuyOrderCreateRequest)
    assert request.transaction_mode == "single_notification"


def test_create_intent_union_dispatches_trailing_stop_strategy() -> None:
    request = _union_adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "trailing_stop_alert",
            "quantityLots": 1,
            "trailMode": "percentage",
            "trailValue": "5.0",
        }
    )

    assert isinstance(request, TrailingStopAlertCreateRequest)
    assert request.trail_value == Decimal("5.0")


def test_limit_buy_order_subschema_accepts_mode_defaults() -> None:
    request = _limit_buy_adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "limit_buy_order",
            "quantityLots": 1,
            "targetPrice": "600",
        }
    )

    assert request.transaction_mode == "single_notification"
    assert request.notification_mode == "single"


def test_limit_buy_order_subschema_rejects_per_fill_notification_mode() -> None:
    with pytest.raises(ValidationError) as exc:
        _limit_buy_adapter.validate_python(
            {
                "symbol": "2330",
                "strategy": "limit_buy_order",
                "quantityLots": 1,
                "targetPrice": "600",
                "notificationMode": "per_fill",
            }
        )

    error = exc.value.errors()[0]
    assert error["loc"] == ("notificationMode",)
    assert error["type"] == "literal_error"


def test_trailing_stop_subschema_accepts_percentage() -> None:
    request = _trailing_adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "trailing_stop_alert",
            "quantityLots": 1,
            "trailMode": "percentage",
            "trailValue": "5.0",
        }
    )

    assert request.trail_value == Decimal("5.0")


def test_trailing_stop_subschema_rejects_target_price() -> None:
    with pytest.raises(ValidationError) as exc:
        _trailing_adapter.validate_python(
            {
                "symbol": "2330",
                "strategy": "trailing_stop_alert",
                "quantityLots": 1,
                "trailMode": "percentage",
                "trailValue": "5.0",
                "targetPrice": "600",
            }
        )

    error = exc.value.errors()[0]
    assert error["loc"] == ("targetPrice",)
    assert error["type"] == "extra_forbidden"


def test_trailing_stop_subschema_percentage_rejects_value_above_ten() -> None:
    with pytest.raises(ValidationError) as exc:
        _trailing_adapter.validate_python(
            {
                "symbol": "2330",
                "strategy": "trailing_stop_alert",
                "quantityLots": 1,
                "trailMode": "percentage",
                "trailValue": "12",
            }
        )

    error = exc.value.errors()[0]
    assert error["loc"] == ("trailValue",)
    assert error["type"] == "value_error"


def test_trailing_stop_subschema_fixed_amount_allows_values_above_percentage_limit() -> None:
    request = _trailing_adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "trailing_stop_alert",
            "quantityLots": 1,
            "trailMode": "fixed_amount",
            "trailValue": "60",
        }
    )

    assert request.trail_value == Decimal("60")


def test_price_alert_rejects_trailing_fields() -> None:
    """既有 buy_price_alert 多帶新 strategy 欄位由 extra=forbid 擋下 (regression)。"""
    with pytest.raises(ValidationError) as exc:
        _union_adapter.validate_python(
            {
                "symbol": "2330",
                "strategy": "buy_price_alert",
                "quantityLots": 1,
                "targetPrice": "600",
                "trailValue": "5.0",
            }
        )

    error = exc.value.errors()[0]
    assert error["loc"] == ("buy_price_alert", "trailValue")
    assert error["type"] == "extra_forbidden"
