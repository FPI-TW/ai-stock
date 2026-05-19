"""API tests for POST /dev/quotes (BE-V0.5-08)."""

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.symbol_errors import SymbolNotTradableError, UnknownSymbolError
from app.main import create_app
from app.services.quote import get_dev_quote_store

VALID_QUOTE_PAYLOAD: dict[str, Any] = {
    "symbol": "2330",
    "bidPrice": "599.00",
    "askPrice": "600.00",
    "lastPrice": "599.50",
    "quoteTime": "2026-05-18T10:00:00+08:00",  # Monday inside session
}


class TestUpsertDevQuoteHappyPath:
    def test_full_quote_returns_200_and_persists(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        mock_symbol_service.get_tradable_symbol.return_value = MagicMock()
        response = client.post("/dev/quotes", json=VALID_QUOTE_PAYLOAD)
        assert response.status_code == 200
        assert response.json() == {"data": {"symbol": "2330", "updated": True}}

        snap = get_dev_quote_store().get("2330")
        assert snap is not None
        assert snap.bid_price == Decimal("599.00")
        assert snap.ask_price == Decimal("600.00")
        assert snap.last_price == Decimal("599.50")
        assert snap.received_at is not None
        mock_symbol_service.get_tradable_symbol.assert_called_once_with("2330")

    def test_last_only_quote_returns_200_and_persists(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        mock_symbol_service.get_tradable_symbol.return_value = MagicMock()
        payload = {**VALID_QUOTE_PAYLOAD, "bidPrice": None, "askPrice": None}
        response = client.post("/dev/quotes", json=payload)
        assert response.status_code == 200
        snap = get_dev_quote_store().get("2330")
        assert snap is not None
        assert snap.bid_price is None
        assert snap.ask_price is None
        assert snap.last_price == Decimal("599.50")

    def test_response_includes_request_id_header(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        mock_symbol_service.get_tradable_symbol.return_value = MagicMock()
        response = client.post(
            "/dev/quotes",
            json=VALID_QUOTE_PAYLOAD,
            headers={"X-Request-Id": "custom-trace-id"},
        )
        assert response.status_code == 200
        assert response.headers.get("X-Request-Id") == "custom-trace-id"


class TestUpsertDevQuoteSymbolErrors:
    def test_unknown_symbol_returns_404(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        mock_symbol_service.get_tradable_symbol.side_effect = UnknownSymbolError("9999")
        payload = {**VALID_QUOTE_PAYLOAD, "symbol": "9999"}
        response = client.post("/dev/quotes", json=payload)
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "UNKNOWN_SYMBOL"
        assert body["error"]["details"]["symbol"] == "9999"
        assert get_dev_quote_store().get("9999") is None

    def test_halted_symbol_returns_422(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        mock_symbol_service.get_tradable_symbol.side_effect = SymbolNotTradableError("2330", "halted")
        response = client.post("/dev/quotes", json=VALID_QUOTE_PAYLOAD)
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "SYMBOL_NOT_TRADABLE"
        assert body["error"]["details"]["symbol"] == "2330"
        assert body["error"]["details"]["tradable_status"] == "halted"
        assert get_dev_quote_store().get("2330") is None


class TestUpsertDevQuoteValidationErrors:
    def test_crossed_quote_returns_422(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        mock_symbol_service.get_tradable_symbol.return_value = MagicMock()
        payload = {**VALID_QUOTE_PAYLOAD, "bidPrice": "601.00", "askPrice": "600.00"}
        response = client.post("/dev/quotes", json=payload)
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "QUOTE_CROSSED"
        assert body["error"]["details"]["bid"] == "601.00"
        assert body["error"]["details"]["ask"] == "600.00"
        assert get_dev_quote_store().get("2330") is None

    def test_non_positive_bid_returns_422(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        mock_symbol_service.get_tradable_symbol.return_value = MagicMock()
        payload = {**VALID_QUOTE_PAYLOAD, "bidPrice": "-0.01"}
        response = client.post("/dev/quotes", json=payload)
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "QUOTE_NON_POSITIVE_PRICE"
        assert body["error"]["details"]["field"] == "bid"
        assert body["error"]["details"]["value"] == "-0.01"

    def test_zero_last_returns_422(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        mock_symbol_service.get_tradable_symbol.return_value = MagicMock()
        payload = {**VALID_QUOTE_PAYLOAD, "lastPrice": "0"}
        response = client.post("/dev/quotes", json=payload)
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "QUOTE_NON_POSITIVE_PRICE"
        assert body["error"]["details"]["field"] == "last"

    def test_out_of_session_quote_returns_422(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        mock_symbol_service.get_tradable_symbol.return_value = MagicMock()
        # Saturday 2026-05-23 is a weekend — outside the regular session.
        payload = {**VALID_QUOTE_PAYLOAD, "quoteTime": "2026-05-23T10:00:00+08:00"}
        response = client.post("/dev/quotes", json=payload)
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "QUOTE_OUT_OF_SESSION"
        assert "2026-05-23T10:00:00" in body["error"]["details"]["quote_time"]
        assert get_dev_quote_store().get("2330") is None

    def test_insufficient_prices_returns_422(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        mock_symbol_service.get_tradable_symbol.return_value = MagicMock()
        payload = {
            **VALID_QUOTE_PAYLOAD,
            "bidPrice": None,
            "askPrice": None,
            "lastPrice": None,
        }
        response = client.post("/dev/quotes", json=payload)
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "QUOTE_INSUFFICIENT_PRICES"
        assert body["error"]["details"]["symbol"] == "2330"


class TestUpsertDevQuoteSchemaErrors:
    def test_missing_quote_time_returns_422(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        payload = {k: v for k, v in VALID_QUOTE_PAYLOAD.items() if k != "quoteTime"}
        response = client.post("/dev/quotes", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        mock_symbol_service.get_tradable_symbol.assert_not_called()

    def test_extra_field_rejected(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        payload = {**VALID_QUOTE_PAYLOAD, "receivedAt": "2026-05-18T10:00:00+08:00"}
        response = client.post("/dev/quotes", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_naive_quote_time_rejected(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        payload = {**VALID_QUOTE_PAYLOAD, "quoteTime": "2026-05-18T10:00:00"}
        response = client.post("/dev/quotes", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


class TestUpsertDevQuoteMultiUpsert:
    def test_repeated_upsert_keeps_latest_quote(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        mock_symbol_service.get_tradable_symbol.return_value = MagicMock()
        first = client.post("/dev/quotes", json=VALID_QUOTE_PAYLOAD)
        assert first.status_code == 200
        second = client.post("/dev/quotes", json={**VALID_QUOTE_PAYLOAD, "lastPrice": "650.00"})
        assert second.status_code == 200
        snap = get_dev_quote_store().get("2330")
        assert snap is not None
        assert snap.last_price == Decimal("650.00")

    def test_different_symbols_are_stored_independently(
        self, client: TestClient, mock_symbol_service: MagicMock
    ) -> None:
        mock_symbol_service.get_tradable_symbol.return_value = MagicMock()
        r1 = client.post("/dev/quotes", json=VALID_QUOTE_PAYLOAD)
        assert r1.status_code == 200
        r2 = client.post(
            "/dev/quotes",
            json={**VALID_QUOTE_PAYLOAD, "symbol": "0050", "lastPrice": "200.00"},
        )
        assert r2.status_code == 200
        snap_2330 = get_dev_quote_store().get("2330")
        snap_0050 = get_dev_quote_store().get("0050")
        assert snap_2330 is not None and snap_2330.last_price == Decimal("599.50")
        assert snap_0050 is not None and snap_0050.last_price == Decimal("200.00")

    def test_snake_case_keys_rejected(self, client: TestClient, mock_symbol_service: MagicMock) -> None:
        # Schema 採 alias-only camelCase；snake_case 變成未知欄位，extra="forbid" 應拒絕
        payload = {
            "symbol": "2330",
            "bid_price": "599.00",
            "askPrice": "600.00",
            "lastPrice": "599.50",
            "quoteTime": "2026-05-18T10:00:00+08:00",
        }
        response = client.post("/dev/quotes", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        mock_symbol_service.get_tradable_symbol.assert_not_called()


class TestUpsertDevQuoteStrictPriceTypes:
    @pytest.mark.parametrize(
        ("alias", "value"),
        [
            ("bidPrice", True),
            ("askPrice", False),
            ("lastPrice", True),
            ("bidPrice", 599),
            ("askPrice", 600),
            ("lastPrice", 599.5),
        ],
    )
    def test_non_string_price_rejected(
        self,
        client: TestClient,
        mock_symbol_service: MagicMock,
        alias: str,
        value: object,
    ) -> None:
        mock_symbol_service.get_tradable_symbol.return_value = MagicMock()
        payload = {**VALID_QUOTE_PAYLOAD, alias: value}
        response = client.post("/dev/quotes", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert get_dev_quote_store().get("2330") is None


class TestUpsertDevQuoteLocalModeOff:
    def test_endpoint_not_registered_when_local_mode_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOCAL_MODE", "false")
        monkeypatch.delenv("LOCAL_USER_ID", raising=False)
        get_settings.cache_clear()
        app = create_app()
        client = TestClient(app)
        response = client.post("/dev/quotes", json=VALID_QUOTE_PAYLOAD)
        assert response.status_code == 404
