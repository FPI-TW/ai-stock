from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.intent import (
    BuyPriceAlertCreateRequest,
    IntentCreateRequest,
    LimitBuyOrderCreateRequest,
    TrailingStopAlertCreateRequest,
)

_adapter: TypeAdapter[IntentCreateRequest] = TypeAdapter(IntentCreateRequest)


def test_create_intent_union_dispatches_existing_price_alert() -> None:
    request = _adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "buy_price_alert",
            "quantityLots": 1,
            "targetPrice": "600",
        }
    )

    assert isinstance(request, BuyPriceAlertCreateRequest)
    assert request.target_price == "600"


def test_create_intent_union_dispatches_limit_order_with_mode_defaults() -> None:
    request = _adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "limit_buy_order",
            "quantityLots": 1,
            "targetPrice": "600",
        }
    )

    assert isinstance(request, LimitBuyOrderCreateRequest)
    assert request.transaction_mode == "single_notification"
    assert request.notification_mode == "single"


def test_create_intent_union_dispatches_trailing_stop_alert() -> None:
    request = _adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "trailing_stop_alert",
            "positionSide": "long",
            "quantityLots": 1,
            "trailMode": "percentage",
            "trailValue": "5.0",
        }
    )

    assert isinstance(request, TrailingStopAlertCreateRequest)
    assert request.trail_value == Decimal("5.0")


def test_price_alert_rejects_trailing_fields() -> None:
    with pytest.raises(ValidationError) as exc:
        _adapter.validate_python(
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


def test_trailing_stop_alert_rejects_target_price() -> None:
    with pytest.raises(ValidationError) as exc:
        _adapter.validate_python(
            {
                "symbol": "2330",
                "strategy": "trailing_stop_alert",
                "positionSide": "long",
                "quantityLots": 1,
                "trailMode": "percentage",
                "trailValue": "5.0",
                "targetPrice": "600",
            }
        )

    error = exc.value.errors()[0]
    assert error["loc"] == ("trailing_stop_alert", "targetPrice")
    assert error["type"] == "extra_forbidden"


def test_limit_order_rejects_per_fill_notification_mode() -> None:
    with pytest.raises(ValidationError) as exc:
        _adapter.validate_python(
            {
                "symbol": "2330",
                "strategy": "limit_buy_order",
                "quantityLots": 1,
                "targetPrice": "600",
                "notificationMode": "per_fill",
            }
        )

    error = exc.value.errors()[0]
    assert error["loc"] == ("limit_buy_order", "notificationMode")
    assert error["type"] == "literal_error"


def test_trailing_stop_percentage_rejects_value_above_fifty() -> None:
    with pytest.raises(ValidationError) as exc:
        _adapter.validate_python(
            {
                "symbol": "2330",
                "strategy": "trailing_stop_alert",
                "positionSide": "long",
                "quantityLots": 1,
                "trailMode": "percentage",
                "trailValue": "60",
            }
        )

    error = exc.value.errors()[0]
    assert error["loc"] == ("trailing_stop_alert", "trailValue")
    assert error["type"] == "value_error"


def test_trailing_stop_fixed_amount_allows_values_above_fifty() -> None:
    request = _adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "trailing_stop_alert",
            "positionSide": "short",
            "quantityLots": 1,
            "trailMode": "fixed_amount",
            "trailValue": "60",
        }
    )

    assert isinstance(request, TrailingStopAlertCreateRequest)
    assert request.trail_value == Decimal("60")
