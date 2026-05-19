from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, status
from fastapi.testclient import TestClient

from app.domain.price import InvalidAmountError, InvalidPriceError, InvalidTickSizeError, InvalidTypeError
from app.domain.quote_errors import (
    QuoteCrossedError,
    QuoteInsufficientPricesError,
    QuoteNonPositivePriceError,
    QuoteOutOfSessionError,
)
from app.main import create_app


def _client_with_route(path: str, raises: Exception) -> TestClient:
    router = APIRouter()

    @router.get(path)
    def endpoint() -> None:
        raise raises  # type: ignore[misc]

    app = create_app()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_exception_uses_internal_error_envelope() -> None:
    router = APIRouter()

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("sensitive failure")

    app = create_app()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/boom", headers={"X-Request-Id": "req-boom"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "發生未預期錯誤",
            "details": {},
            "requestId": "req-boom",
        }
    }


def test_invalid_tick_size_returns_422() -> None:
    exc = InvalidTickSizeError("10.01", "not a valid tick multiple", Decimal("10.00"), Decimal("10.05"))
    client = _client_with_route("/tick", exc)
    response = client.get("/tick", headers={"X-Request-Id": "req-tick"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "INVALID_TICK_SIZE"
    assert body["error"]["message"] == "價格不符合升降單位規定"
    assert body["error"]["details"] == {
        "value": "10.01",
        "nearest_lower": "10.00",
        "nearest_upper": "10.05",
    }
    assert body["error"]["requestId"] == "req-tick"


def test_invalid_price_returns_422() -> None:
    client = _client_with_route("/price", InvalidPriceError("abc", "cannot be parsed as a number"))
    response = client.get("/price", headers={"X-Request-Id": "req-price"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "INVALID_PRICE"
    assert body["error"]["message"] == "價格格式不合法"
    assert body["error"]["details"] == {"value": "abc"}
    assert body["error"]["requestId"] == "req-price"


def test_invalid_amount_returns_422() -> None:
    client = _client_with_route("/amount", InvalidAmountError(0, "must be greater than zero"))
    response = client.get("/amount", headers={"X-Request-Id": "req-amount"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "INVALID_AMOUNT"
    assert body["error"]["message"] == "數量不合法"
    assert body["error"]["details"] == {"value": "0"}
    assert body["error"]["requestId"] == "req-amount"


def test_invalid_type_returns_422() -> None:
    client = _client_with_route("/type", InvalidTypeError("bond", "must be stock or etf"))
    response = client.get("/type", headers={"X-Request-Id": "req-type"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "INVALID_TYPE"
    assert body["error"]["message"] == "證券類型不合法"
    assert body["error"]["details"] == {"value": "bond"}
    assert body["error"]["requestId"] == "req-type"


def test_invalid_tick_size_not_caught_by_invalid_price_handler() -> None:
    # InvalidTickSizeError 是 InvalidPriceError 子類別，確認 handler 選到正確的那個
    exc = InvalidTickSizeError("50.05", "not a valid tick multiple", Decimal("50.0"), Decimal("50.1"))
    client = _client_with_route("/tick2", exc)
    response = client.get("/tick2")

    assert response.json()["error"]["code"] == "INVALID_TICK_SIZE"


def test_quote_out_of_session_returns_422() -> None:
    quote_time = datetime(2026, 5, 23, 10, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    client = _client_with_route("/quote-session", QuoteOutOfSessionError(quote_time=quote_time))
    response = client.get("/quote-session", headers={"X-Request-Id": "req-q-session"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "QUOTE_OUT_OF_SESSION"
    assert body["error"]["message"] == "Quote 時間不在交易時段內"
    assert body["error"]["details"] == {"quote_time": quote_time.isoformat()}
    assert body["error"]["requestId"] == "req-q-session"


def test_quote_crossed_returns_422() -> None:
    client = _client_with_route(
        "/quote-crossed",
        QuoteCrossedError(bid=Decimal("601.00"), ask=Decimal("600.00")),
    )
    response = client.get("/quote-crossed", headers={"X-Request-Id": "req-q-crossed"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "QUOTE_CROSSED"
    assert body["error"]["message"] == "Quote bid 大於 ask"
    assert body["error"]["details"] == {"bid": "601.00", "ask": "600.00"}
    assert body["error"]["requestId"] == "req-q-crossed"


def test_quote_non_positive_price_returns_422() -> None:
    client = _client_with_route(
        "/quote-nonpos",
        QuoteNonPositivePriceError(field="bid", value=Decimal("-0.01")),
    )
    response = client.get("/quote-nonpos", headers={"X-Request-Id": "req-q-nonpos"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "QUOTE_NON_POSITIVE_PRICE"
    assert body["error"]["message"] == "Quote 價格必須大於 0"
    assert body["error"]["details"] == {"field": "bid", "value": "-0.01"}
    assert body["error"]["requestId"] == "req-q-nonpos"


def test_quote_insufficient_prices_returns_422() -> None:
    client = _client_with_route(
        "/quote-insufficient",
        QuoteInsufficientPricesError(symbol="2330"),
    )
    response = client.get("/quote-insufficient", headers={"X-Request-Id": "req-q-insufficient"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "QUOTE_INSUFFICIENT_PRICES"
    assert body["error"]["message"] == "Quote 需至少提供 bid/ask/last 其中一項"
    assert body["error"]["details"] == {"symbol": "2330"}
    assert body["error"]["requestId"] == "req-q-insufficient"
