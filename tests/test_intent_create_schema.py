"""Schema-layer contract tests for POST /trade-intents request body.

V0.5 階段 `IntentCreateRequest` union 開放 buy/sell_price_alert 與
trailing_stop_alert(後者 command 路徑由後續 PR 接;route 層暫攔截回 501)。
LimitBuy/Sell 子 schema 定義保留供 BE-V0.5-15 接手時納入 union。
本檔驗證:
- union 對未開放 strategy(目前剩 limit_*)顯式拒絕(避免 schema 通過後 command 邊界以 500 收尾)
- 已納入 union 的 strategy dispatch 到對應子 schema
- 個別子 schema 各自欄位約束 (extra=forbid / Literal / 值域)
"""

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


def test_create_intent_union_rejects_limit_order_strategy() -> None:
    """V0.5 階段 union 不含 limit_*_order;BE-V0.5-15 接手後再開放。"""
    with pytest.raises(ValidationError) as exc:
        _union_adapter.validate_python(
            {
                "symbol": "2330",
                "strategy": "limit_buy_order",
                "quantityLots": 1,
                "targetPrice": "600",
            }
        )

    error = exc.value.errors()[0]
    assert error["type"] == "union_tag_invalid"
    assert error["loc"] == ()


def test_create_intent_union_dispatches_trailing_stop_strategy() -> None:
    """BE-V0.5-16 把 trailing_stop_alert 加進 union;Pydantic 驗證通過後
    route 層攔截回 501。本 test 只驗 union dispatch,501 行為在
    tests/api/test_trade_intents.py 驗。"""
    request = _union_adapter.validate_python(
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
    assert request.position_side == "long"
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
