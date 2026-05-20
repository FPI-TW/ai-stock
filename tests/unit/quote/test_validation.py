from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.domain.trading_session import TradingSessionService
from app.services.quote.base import QuoteSnapshot
from app.services.quote.validation import QuoteValidationError, QuoteValidator

TAIPEI = ZoneInfo("Asia/Taipei")

# A Monday in May 2026 — guaranteed weekday and inside regular session 09:00-13:30.
_IN_SESSION = datetime(2026, 5, 11, 10, 30, tzinfo=TAIPEI)
_OUT_OF_SESSION = datetime(2026, 5, 11, 8, 30, tzinfo=TAIPEI)


def _make(
    *,
    bid: str | None = "590.0000",
    ask: str | None = "591.0000",
    last: str | None = "590.5000",
    quote_time: datetime = _IN_SESSION,
) -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol="2330",
        bid_price=Decimal(bid) if bid is not None else None,
        ask_price=Decimal(ask) if ask is not None else None,
        last_price=Decimal(last) if last is not None else None,
        quote_time=quote_time,
        received_at=datetime.now(tz=ZoneInfo("UTC")),
    )


@pytest.fixture
def validator() -> QuoteValidator:
    # Fixed clock — keeps validation deterministic regardless of when CI runs.
    return QuoteValidator(TradingSessionService(clock=lambda: _IN_SESSION))


def test_validation_accepts_normal_quote(validator: QuoteValidator) -> None:
    result = validator.validate(_make())
    assert result.fallback_used is False


def test_validation_rejects_crossed_quote(validator: QuoteValidator) -> None:
    with pytest.raises(QuoteValidationError, match="crossed"):
        validator.validate(_make(bid="592.0000", ask="591.0000"))


def test_validation_rejects_zero_price(validator: QuoteValidator) -> None:
    with pytest.raises(QuoteValidationError, match="bid_price must be > 0"):
        validator.validate(_make(bid="0", ask="591.0000", last="590"))


def test_validation_rejects_negative_price(validator: QuoteValidator) -> None:
    with pytest.raises(QuoteValidationError, match="last_price must be > 0"):
        validator.validate(_make(last="-1"))


def test_validation_rejects_all_missing(validator: QuoteValidator) -> None:
    with pytest.raises(QuoteValidationError, match="bid, ask, and last all missing"):
        validator.validate(_make(bid=None, ask=None, last=None))


def test_validation_marks_last_only_as_fallback(validator: QuoteValidator) -> None:
    result = validator.validate(_make(bid=None, ask=None, last="590.5000"))
    assert result.fallback_used is True


def test_validation_marks_missing_bid_as_fallback(validator: QuoteValidator) -> None:
    result = validator.validate(_make(bid=None, ask="591.0000", last="590.5000"))
    assert result.fallback_used is True


def test_validation_rejects_quote_outside_session(validator: QuoteValidator) -> None:
    with pytest.raises(QuoteValidationError, match="quote_time outside regular session"):
        validator.validate(_make(quote_time=_OUT_OF_SESSION))


def test_validation_rejects_when_now_outside_session() -> None:
    out_of_session_validator = QuoteValidator(TradingSessionService(clock=lambda: _OUT_OF_SESSION))
    with pytest.raises(QuoteValidationError, match="now outside regular session"):
        out_of_session_validator.validate(_make())
