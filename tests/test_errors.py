from decimal import Decimal

from fastapi import APIRouter, status
from fastapi.testclient import TestClient

from app.domain.price import InvalidAmountError, InvalidPriceError, InvalidTickSizeError, InvalidTypeError
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


def test_sanitize_validation_errors_drops_everything_input_derived() -> None:
    """Review finding: SecretStr length errors carried `ctx.actual_length` and
    "..., not 300" in `msg`; `value_error` carries the exception object itself."""
    from typing import Any

    from app.api.errors import sanitize_validation_errors

    raw: list[dict[str, Any]] = [
        {
            "type": "too_long",
            "loc": ("body", "password"),
            "msg": "Value should have at most 256 items after validation, not 300",
            "input": "p" * 300,
            "ctx": {"field_type": "Value", "max_length": 256, "actual_length": 300},
            "url": "https://errors.pydantic.dev/2.12/v/too_long",
        },
        {
            "type": "value_error",
            "loc": ("body", "trailValue"),
            "msg": "Value error, trailValue supports at most 4 decimal places (got 1.23456)",
            "input": "1.23456",
            "ctx": {"error": ValueError("got 1.23456")},
        },
        {
            "type": "uuid_parsing",
            "loc": ("path", "user_id"),
            "msg": "Input should be a valid UUID, invalid character: found `Z` at 1",
            "input": "Z9876",
        },
        {
            "type": "string_too_long",
            "loc": ("body", "email"),
            "msg": "String should have at most 8 characters",
            "input": "x" * 20,
            "ctx": {"max_length": 8},
        },
    ]

    out = sanitize_validation_errors(raw)

    assert out == [
        {
            "type": "too_long",
            "loc": ["body", "password"],
            "msg": "Value should have at most 256 items",
            "ctx": {"max_length": 256},
        },
        {"type": "value_error", "loc": ["body", "trailValue"], "msg": "Invalid value", "ctx": {}},
        {"type": "uuid_parsing", "loc": ["path", "user_id"], "msg": "Invalid value", "ctx": {}},
        {
            "type": "string_too_long",
            "loc": ["body", "email"],
            "msg": "String should have at most 8 characters",
            "ctx": {"max_length": 8},
        },
    ]
    import json

    json.dumps(out)  # always serialisable, whatever pydantic put in ctx
