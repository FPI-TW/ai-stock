"""Integration tests — PriceService exception handling through the full HTTP stack."""

from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Body
from fastapi.testclient import TestClient

from app.domain.price import PriceRequest, PriceService
from app.main import create_app


def _make_client() -> TestClient:
    router = APIRouter()

    @router.post("/price/validate")
    def validate_price(body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        req = PriceRequest(
            type=body.get("type", ""),
            price=body.get("price", ""),
            amount=body.get("amount", 0),
        )
        price = PriceService.validate(req)
        return {"data": {"type": str(req.type), "price": str(price), "amount": req.amount}}

    app = create_app()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def client() -> TestClient:
    return _make_client()


class TestInvalidTick:
    def test_stock_invalid_tick_returns_422_invalid_tick_size(self, client: TestClient) -> None:
        response = client.post(
            "/price/validate",
            json={"type": "stock", "price": "10.01", "amount": 500},
            headers={"X-Request-Id": "req-tick-stock"},
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "INVALID_TICK_SIZE"
        assert body["error"]["message"] == "價格不符合升降單位規定"
        assert body["error"]["details"] == {
            "value": "10.01",
            "nearest_lower": "10.00",
            "nearest_upper": "10.05",
        }
        assert body["error"]["requestId"] == "req-tick-stock"

    def test_etf_invalid_tick_returns_422_invalid_tick_size(self, client: TestClient) -> None:
        response = client.post(
            "/price/validate",
            json={"type": "etf", "price": "50.01", "amount": 1000},
            headers={"X-Request-Id": "req-tick-etf"},
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "INVALID_TICK_SIZE"
        assert body["error"]["details"] == {
            "value": "50.01",
            "nearest_lower": "50.00",
            "nearest_upper": "50.05",
        }
        assert body["error"]["requestId"] == "req-tick-etf"

    def test_invalid_price_format_returns_invalid_price(self, client: TestClient) -> None:
        response = client.post(
            "/price/validate",
            json={"type": "stock", "price": "bad", "amount": 500},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_PRICE"

    def test_zero_amount_returns_invalid_amount(self, client: TestClient) -> None:
        response = client.post(
            "/price/validate",
            json={"type": "stock", "price": "49.95", "amount": 0},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_AMOUNT"

    def test_invalid_type_returns_invalid_type(self, client: TestClient) -> None:
        response = client.post(
            "/price/validate",
            json={"type": "bond", "price": "49.95", "amount": 500},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_TYPE"


class TestDecimalPrecision:
    def test_valid_stock_request_returns_200(self, client: TestClient) -> None:
        response = client.post(
            "/price/validate",
            json={"type": "stock", "price": "49.95", "amount": 500},
        )
        assert response.status_code == 200
        assert response.json() == {"data": {"type": "stock", "price": "49.95", "amount": 500}}

    def test_valid_etf_request_returns_200(self, client: TestClient) -> None:
        response = client.post(
            "/price/validate",
            json={"type": "etf", "price": "50.05", "amount": 1000},
        )
        assert response.status_code == 200
        assert response.json() == {"data": {"type": "etf", "price": "50.05", "amount": 1000}}

    def test_price_is_serialized_as_string(self, client: TestClient) -> None:
        response = client.post(
            "/price/validate",
            json={"type": "stock", "price": "49.95", "amount": 500},
        )
        price_value = response.json()["data"]["price"]
        assert isinstance(price_value, str), "price must be a JSON string, not a number"

    def test_precision_survives_http_roundtrip(self, client: TestClient) -> None:
        # 這些值在 float 運算下容易產生精度誤差，確保 Decimal → str 路徑完整保留
        cases = [
            ("stock", "0.01", 100),
            ("stock", "9.99", 100),
            ("stock", "49.95", 200),
            ("stock", "100.5", 300),
            ("etf", "49.99", 100),
            ("etf", "50.05", 500),
        ]
        for sec_type, price, amount in cases:
            response = client.post(
                "/price/validate",
                json={"type": sec_type, "price": price, "amount": amount},
            )
            assert response.status_code == 200, f"Expected 200 for {sec_type} {price}"
            data = response.json()["data"]
            assert data["type"] == sec_type
            assert data["price"] == price, f"Precision lost for {price}"
            assert data["amount"] == amount
