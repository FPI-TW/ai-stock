"""Schema-layer contract tests for POST /trade-intents request body.

BE-V0.5-15 將 limit_buy_order / limit_sell_order 加入 union;TrailingStop 仍
保留至 BE-V0.5-16。本檔驗證:
- union 對四個已開放 strategy 正確 dispatch 到子 schema
- 子 schema 的 Literal / extra=forbid 規則由 Pydantic 自動產生 VALIDATION_ERROR,
  不需要 strategy-specific error code
"""

from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.intent import (
    BuyPriceAlertCreateRequest,
    IntentCreateRequest,
    LimitBuyOrderCreateRequest,
    LimitSellOrderCreateRequest,
    SellPriceAlertCreateRequest,
    TrailingStopAlertCreateRequest,
)

_union_adapter: TypeAdapter[IntentCreateRequest] = TypeAdapter(IntentCreateRequest)
_limit_buy_adapter = TypeAdapter(LimitBuyOrderCreateRequest)
_trailing_adapter = TypeAdapter(TrailingStopAlertCreateRequest)


def test_create_intent_union_dispatches_buy_price_alert() -> None:
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


def test_create_intent_union_dispatches_sell_price_alert() -> None:
    request = _union_adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "sell_price_alert",
            "quantityLots": 1,
            "targetPrice": "650",
        }
    )

    assert isinstance(request, SellPriceAlertCreateRequest)


def test_create_intent_union_dispatches_limit_buy_order() -> None:
    request = _union_adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "limit_buy_order",
            "quantityLots": 1,
            "targetPrice": "600",
            "transactionMode": "partial_fill_allowed",
        }
    )

    assert isinstance(request, LimitBuyOrderCreateRequest)
    assert request.transaction_mode == "partial_fill_allowed"
    assert request.notification_mode == "single"


def test_create_intent_union_dispatches_limit_sell_order() -> None:
    request = _union_adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "limit_sell_order",
            "quantityLots": 1,
            "targetPrice": "650",
        }
    )

    assert isinstance(request, LimitSellOrderCreateRequest)
    assert request.transaction_mode == "single_notification"
    assert request.notification_mode == "single"


def test_create_intent_union_rejects_limit_order_missing_target_price() -> None:
    with pytest.raises(ValidationError) as exc:
        _union_adapter.validate_python(
            {
                "symbol": "2330",
                "strategy": "limit_buy_order",
                "quantityLots": 1,
            }
        )

    errors = exc.value.errors()
    assert any(err["loc"][-1] == "targetPrice" for err in errors)


def test_create_intent_union_rejects_limit_order_per_fill() -> None:
    with pytest.raises(ValidationError) as exc:
        _union_adapter.validate_python(
            {
                "symbol": "2330",
                "strategy": "limit_buy_order",
                "quantityLots": 1,
                "targetPrice": "600",
                "notificationMode": "per_fill",
            }
        )

    errors = exc.value.errors()
    matching = [err for err in errors if err["loc"][-1] == "notificationMode"]
    assert matching, errors
    assert matching[0]["type"] == "literal_error"


def test_create_intent_union_rejects_buy_alert_with_transaction_mode() -> None:
    """buy_price_alert 子 schema 不含 transactionMode,extra=forbid 擋下."""
    with pytest.raises(ValidationError) as exc:
        _union_adapter.validate_python(
            {
                "symbol": "2330",
                "strategy": "buy_price_alert",
                "quantityLots": 1,
                "targetPrice": "600",
                "transactionMode": "single_notification",
            }
        )

    errors = exc.value.errors()
    matching = [err for err in errors if err["loc"][-1] == "transactionMode"]
    assert matching, errors
    assert matching[0]["type"] == "extra_forbidden"


def test_create_intent_union_rejects_trailing_stop_strategy() -> None:
    """V0.5 階段 union 不含 trailing_stop_alert;BE-V0.5-16 接手後再開放。"""
    with pytest.raises(ValidationError) as exc:
        _union_adapter.validate_python(
            {
                "symbol": "2330",
                "strategy": "trailing_stop_alert",
                "positionSide": "long",
                "quantityLots": 1,
                "trailMode": "percentage",
                "trailValue": "5.0",
            }
        )

    error = exc.value.errors()[0]
    assert error["type"] == "union_tag_invalid"
    assert error["loc"] == ()


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


def test_trailing_stop_subschema_accepts_long_percentage() -> None:
    request = _trailing_adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "trailing_stop_alert",
            "positionSide": "long",
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
                "positionSide": "long",
                "quantityLots": 1,
                "trailMode": "percentage",
                "trailValue": "5.0",
                "targetPrice": "600",
            }
        )

    error = exc.value.errors()[0]
    assert error["loc"] == ("targetPrice",)
    assert error["type"] == "extra_forbidden"


def test_trailing_stop_subschema_percentage_rejects_value_above_fifty() -> None:
    with pytest.raises(ValidationError) as exc:
        _trailing_adapter.validate_python(
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
    assert error["loc"] == ("trailValue",)
    assert error["type"] == "value_error"


def test_trailing_stop_subschema_fixed_amount_allows_values_above_fifty() -> None:
    request = _trailing_adapter.validate_python(
        {
            "symbol": "2330",
            "strategy": "trailing_stop_alert",
            "positionSide": "short",
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
