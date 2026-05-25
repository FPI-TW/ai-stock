"""API tests for POST /trade-intents/batch — structural validation layer.

This file covers the schema / structural validation pass only. Row-level
business validation + the same-transaction loop are pending PR #15 merge;
the handler currently raises `BATCH_NOT_IMPLEMENTED_STUB` (501) once
structural validation passes, which lets us assert "structure is fine,
business path not yet wired" without exercising the command layer.

When the stub is replaced (work order §97 + Implementation notes), the
"valid → 501 stub" cases below become the "valid → 200 with createdRows"
cases; structural negative cases stay as-is.
"""

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.deps import get_cancel_trade_intent_command, get_create_trade_intent_command, get_intent_repository
from app.main import create_app

_VALID_BUY = {
    "symbol": "2330",
    "strategy": "buy_price_alert",
    "quantityLots": 1,
    "targetPrice": "600.0",
}
_VALID_SELL = {
    "symbol": "2317",
    "strategy": "sell_price_alert",
    "quantityLots": 2,
    "targetPrice": "200.0",
}


@pytest.fixture
def api_client() -> Generator[TestClient]:
    """Stub all command / repo deps — the batch endpoint should never reach
    them while it lives in the structural-validation-only stub phase. If a
    test below ever hits a Mock call here, that's the regression signal."""
    app = create_app()
    app.dependency_overrides[get_create_trade_intent_command] = lambda: MagicMock()
    app.dependency_overrides[get_cancel_trade_intent_command] = lambda: MagicMock()
    app.dependency_overrides[get_intent_repository] = lambda: MagicMock()
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


# ------------------------------------------------------------------
# Happy structural path → 501 stub (proves validation passed)
# ------------------------------------------------------------------


def test_batch_single_valid_row_passes_structural_validation(api_client: TestClient) -> None:
    response = api_client.post("/trade-intents/batch", json={"rows": [_VALID_BUY]})

    assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
    body = response.json()
    assert body["error"]["code"] == "BATCH_NOT_IMPLEMENTED_STUB"
    assert body["error"]["details"]["receivedRows"] == 1


def test_batch_two_valid_rows_passes_structural_validation(api_client: TestClient) -> None:
    response = api_client.post("/trade-intents/batch", json={"rows": [_VALID_BUY, _VALID_SELL]})

    assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
    assert response.json()["error"]["details"]["receivedRows"] == 2


def test_batch_twenty_valid_rows_at_boundary_passes_structural_validation(api_client: TestClient) -> None:
    rows = [{**_VALID_BUY, "symbol": f"23{i:02d}"} for i in range(20)]
    response = api_client.post("/trade-intents/batch", json={"rows": rows})

    assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
    assert response.json()["error"]["details"]["receivedRows"] == 20


# ------------------------------------------------------------------
# rows length: 0 → VALIDATION_ERROR, > 20 → CSV_BATCH_LIMIT_EXCEEDED
# ------------------------------------------------------------------


def test_batch_zero_rows_returns_422_validation_error(api_client: TestClient) -> None:
    response = api_client.post("/trade-intents/batch", json={"rows": []})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_batch_twenty_one_rows_returns_csv_batch_limit_exceeded(api_client: TestClient) -> None:
    rows = [{**_VALID_BUY, "symbol": f"23{i:02d}"} for i in range(21)]
    response = api_client.post("/trade-intents/batch", json={"rows": rows})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "CSV_BATCH_LIMIT_EXCEEDED"
    assert body["error"]["details"] == {"actual": 21, "limit": 20}


def test_batch_twenty_one_rows_with_invalid_row_returns_csv_batch_limit_exceeded(
    api_client: TestClient,
) -> None:
    """Row count check must precede per-row schema validation (work order §90).

    Regression guard: prior to moving the limit to the schema layer
    (`max_length=BATCH_ROW_LIMIT`), the handler did `len(request.rows) > 20`
    AFTER Pydantic finished validating each row — so oversized payloads with a
    malformed row leaked out as VALIDATION_ERROR (the bad row's error) instead
    of CSV_BATCH_LIMIT_EXCEEDED. This test pins the precedence so a future
    refactor can't silently revive that bug."""
    bad_first = {k: v for k, v in _VALID_BUY.items() if k != "symbol"}
    rows = [bad_first] + [{**_VALID_BUY, "symbol": f"23{i:02d}"} for i in range(20)]
    response = api_client.post("/trade-intents/batch", json={"rows": rows})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "CSV_BATCH_LIMIT_EXCEEDED"
    assert body["error"]["details"] == {"actual": 21, "limit": 20}


# ------------------------------------------------------------------
# Missing required fields
# ------------------------------------------------------------------


@pytest.mark.parametrize("missing_field", ["symbol", "strategy", "quantityLots", "targetPrice"])
def test_batch_row_missing_required_field_returns_422(api_client: TestClient, missing_field: str) -> None:
    bad_row = {k: v for k, v in _VALID_BUY.items() if k != missing_field}
    response = api_client.post("/trade-intents/batch", json={"rows": [bad_row]})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_batch_missing_rows_key_returns_422(api_client: TestClient) -> None:
    response = api_client.post("/trade-intents/batch", json={})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------
# Invalid enum / type values
# ------------------------------------------------------------------


def test_batch_row_invalid_strategy_returns_422(api_client: TestClient) -> None:
    bad_row = {**_VALID_BUY, "strategy": "market_buy"}
    response = api_client.post("/trade-intents/batch", json={"rows": [bad_row]})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_batch_row_zero_quantity_returns_422(api_client: TestClient) -> None:
    bad_row = {**_VALID_BUY, "quantityLots": 0}
    response = api_client.post("/trade-intents/batch", json={"rows": [bad_row]})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_batch_row_negative_quantity_returns_422(api_client: TestClient) -> None:
    bad_row = {**_VALID_BUY, "quantityLots": -1}
    response = api_client.post("/trade-intents/batch", json={"rows": [bad_row]})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------
# extra='forbid' — backend-derived fields and ownerUserId rejected
# ------------------------------------------------------------------


def test_batch_row_with_trading_date_field_returns_422(api_client: TestClient) -> None:
    """tradingDate is backend-derived per work order §85; payload must reject it."""
    bad_row = {**_VALID_BUY, "tradingDate": "2026-05-22"}
    response = api_client.post("/trade-intents/batch", json={"rows": [bad_row]})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_batch_row_with_status_field_returns_422(api_client: TestClient) -> None:
    bad_row = {**_VALID_BUY, "status": "active"}
    response = api_client.post("/trade-intents/batch", json={"rows": [bad_row]})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_batch_row_with_owner_user_id_returns_422(api_client: TestClient) -> None:
    """Owner is taken from request context; row-level ownerUserId must be rejected."""
    bad_row = {**_VALID_BUY, "ownerUserId": "00000000-0000-0000-0000-000000000001"}
    response = api_client.post("/trade-intents/batch", json={"rows": [bad_row]})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_batch_row_with_unknown_field_returns_422(api_client: TestClient) -> None:
    """Generalized extra='forbid' check — any unknown key (not just the
    backend-derived ones above) must be rejected. Documents the contract
    intent 'reject ALL unknown fields' independently of the semantic-field
    tests, so if those tests get rewritten when a field becomes explicitly
    allowed, this one still guards the unknown-field policy."""
    bad_row = {**_VALID_BUY, "foo": "bar"}
    response = api_client.post("/trade-intents/batch", json={"rows": [bad_row]})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_batch_top_level_extra_field_returns_422(api_client: TestClient) -> None:
    response = api_client.post("/trade-intents/batch", json={"rows": [_VALID_BUY], "ownerUserId": "x"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------
# Mixed: first row invalid → entire request rejected at structural layer
# ------------------------------------------------------------------


def test_batch_one_invalid_row_among_valid_returns_422(api_client: TestClient) -> None:
    """Pydantic's per-item validation will fail the whole request; we don't yet
    return row-level errors at the structural layer. Row-level error envelope
    (BATCH_ROW_REJECTED) is the business-validation layer's job."""
    bad_row = {**_VALID_BUY, "strategy": "INVALID"}
    response = api_client.post("/trade-intents/batch", json={"rows": [_VALID_BUY, bad_row, _VALID_SELL]})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
