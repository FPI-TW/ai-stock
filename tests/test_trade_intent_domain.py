import pytest

from app.domain.trade_intent import derive_order_side


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        ("buy_price_alert", "buy"),
        ("sell_price_alert", "sell"),
        ("limit_buy_order", "buy"),
        ("limit_sell_order", "sell"),
        ("trailing_stop_alert", "sell"),
    ],
)
def test_derive_order_side(strategy: str, expected: str) -> None:
    assert derive_order_side(strategy) == expected


def test_derive_order_side_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError):
        derive_order_side("take_profit_alert")
