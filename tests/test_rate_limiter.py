from app.core.rate_limiter import _refill


def test_refill_clamps_to_capacity() -> None:
    # 100s of refill at 1/s would overflow a capacity-10 bucket; clamp to 10.
    assert _refill(10.0, 100.0, 10.0, 1.0) == 10.0


def test_refill_accumulates_linearly() -> None:
    assert _refill(0.0, 5.0, 10.0, 1.0) == 5.0


def test_refill_handles_partial_rate() -> None:
    assert _refill(2.0, 3.0, 10.0, 0.5) == 3.5


def test_refill_ignores_negative_elapsed() -> None:
    # Clock skew must never drain or add tokens.
    assert _refill(3.0, -5.0, 10.0, 1.0) == 3.0
