"""Unit tests for build_dedup_key — the new-slice duplicate discriminator.

去重正確性的核心、也是風險最高處（價格標準化）。重點：數值相等的價格必須得到
同一個 key，否則去重會默默漏擋。"""

from decimal import Decimal

from app.repositories.trade_intent_core_repository import build_dedup_key


def _price_key(value: Decimal) -> str:
    return build_dedup_key(
        "buy_price_alert",
        target_price_effective=value,
        trail_mode=None,
        trail_value=None,
        position_side=None,
    )


def test_price_strategies_use_formatted_target_price() -> None:
    assert _price_key(Decimal("600")) == "600.00"
    assert _price_key(Decimal("585.5")) == "585.50"
    assert _price_key(Decimal("585.55")) == "585.55"


def test_numerically_equal_prices_map_to_same_key() -> None:
    # 最關鍵：600 / 600.0000 / 600.00 視為同一張單，key 必須一致（否則去重漏擋）
    assert _price_key(Decimal("600")) == _price_key(Decimal("600.0000"))
    assert _price_key(Decimal("600.0000")) == _price_key(Decimal("600.00"))
    assert _price_key(Decimal("585.50")) == _price_key(Decimal("585.5"))


def test_limit_orders_also_keyed_on_price() -> None:
    key = build_dedup_key(
        "limit_buy_order",
        target_price_effective=Decimal("123.4500"),
        trail_mode=None,
        trail_value=None,
        position_side=None,
    )
    assert key == "123.45"


def test_market_orders_have_empty_key() -> None:
    for strategy in ("market_order", "market_buy_order", "market_sell_order"):
        assert (
            build_dedup_key(
                strategy,
                target_price_effective=None,
                trail_mode=None,
                trail_value=None,
                position_side=None,
            )
            == ""
        )


def test_trailing_keyed_on_mode_and_value() -> None:
    assert (
        build_dedup_key(
            "trailing_stop_alert",
            target_price_effective=None,
            trail_mode="percentage",
            trail_value=Decimal("5"),
            position_side=None,
        )
        == "percentage:5.00"
    )
    assert (
        build_dedup_key(
            "trailing_stop_alert",
            target_price_effective=None,
            trail_mode="fixed_amount",
            trail_value=Decimal("1.5"),
            position_side=None,
        )
        == "fixed_amount:1.50"
    )


def test_trailing_same_value_different_scale_same_key() -> None:
    a = build_dedup_key(
        "trailing_stop_alert",
        target_price_effective=None,
        trail_mode="percentage",
        trail_value=Decimal("5"),
        position_side=None,
    )
    b = build_dedup_key(
        "trailing_stop_alert",
        target_price_effective=None,
        trail_mode="percentage",
        trail_value=Decimal("5.0000"),
        position_side=None,
    )
    assert a == b


def test_twap_keyed_on_position_side() -> None:
    for side in ("long", "short"):
        assert (
            build_dedup_key(
                "twap_order",
                target_price_effective=None,
                trail_mode=None,
                trail_value=None,
                position_side=side,
            )
            == side
        )
