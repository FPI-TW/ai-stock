"""Unit tests for command-layer constants in `app.commands.trade_intent`.

Most of the command surface is exercised end-to-end via API / integration
tests; this file isolates pure data mappings whose meaning the spec calls
out explicitly (so a silent regression on the dict would otherwise only
surface as a wrong column in trigger_events).
"""

from app.commands.trade_intent import _TRAILING_TRIGGER_REF, _TRIGGER_REF


def test_trailing_position_side_to_trigger_reference_mapping() -> None:
    """Spec §測試要求 line 369: ``trailing_stop_alert`` derives ``order_side``
    from ``position_side`` (long→sell, short→buy). In our codebase the
    schema column is ``trigger_reference_price_type`` (spec §110 decision to
    not promote ``order_side`` to a column); the long→bid / short→ask
    mapping is the same semantic — long trailing exits via SELL into bid,
    short trailing exits via BUY from ask.
    """
    assert _TRAILING_TRIGGER_REF == {"long": "bid", "short": "ask"}


def test_non_trailing_strategy_trigger_reference_mapping() -> None:
    """Sanity guard so a future enum addition can't silently shadow buy/sell."""
    assert _TRIGGER_REF == {
        "buy_price_alert": "ask",
        "sell_price_alert": "bid",
    }
