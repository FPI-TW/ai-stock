"""API tests for POST/GET/cancel /trade-intents endpoints."""

from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.deps import get_cancel_trade_intent_command, get_create_trade_intent_command, get_intent_repository
from app.domain.trade_intent import CancelNotAllowedError, DuplicateIntentError, IntentNotFoundError, TradeIntentData
from app.main import create_app

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")
_INTENT_ID = uuid4()

_BUY_PAYLOAD = {
    "symbol": "2330",
    "strategy": "buy_price_alert",
    "quantityLots": 1,
    "targetPrice": "600",
}
_SELL_PAYLOAD = {
    "symbol": "2330",
    "strategy": "sell_price_alert",
    "quantityLots": 2,
    "targetPrice": "650",
}


def _make_intent(
    *,
    strategy: str = "buy_price_alert",
    status: str = "active",
    symbol: str = "2330",
    quantity_lots: int = 1,
    price: str = "600",
) -> TradeIntentData:
    now = datetime.now(tz=UTC)
    return TradeIntentData(
        id=_INTENT_ID,
        owner_user_id=_OWNER_ID,
        symbol=symbol,
        strategy=strategy,
        execution_mode="notify_only",
        quantity_lots=quantity_lots,
        target_price_original=Decimal(price),
        target_price_effective=Decimal(price),
        trigger_reference_price_type="ask" if "buy" in strategy else "bid",
        trading_date=date.today(),
        time_in_force="day",
        status=status,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def mock_create_command() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_cancel_command() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def api_client(
    mock_create_command: MagicMock, mock_cancel_command: MagicMock, mock_repo: MagicMock
) -> Generator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_create_trade_intent_command] = lambda: mock_create_command
    app.dependency_overrides[get_cancel_trade_intent_command] = lambda: mock_cancel_command
    app.dependency_overrides[get_intent_repository] = lambda: mock_repo
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


# ------------------------------------------------------------------
# POST /trade-intents — create
# ------------------------------------------------------------------


def test_create_buy_alert_success(api_client: TestClient, mock_create_command: MagicMock) -> None:
    mock_create_command.execute.return_value = _make_intent(strategy="buy_price_alert")

    response = api_client.post("/trade-intents", json=_BUY_PAYLOAD)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()["data"]
    assert data["strategy"] == "buy_price_alert"
    assert data["timeInForce"] == "day"
    assert data["executionMode"] == "notify_only"
    assert data["status"] == "active"


def test_create_sell_alert_success(api_client: TestClient, mock_create_command: MagicMock) -> None:
    mock_create_command.execute.return_value = _make_intent(strategy="sell_price_alert", price="650", quantity_lots=2)

    response = api_client.post("/trade-intents", json=_SELL_PAYLOAD)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()["data"]
    assert data["strategy"] == "sell_price_alert"
    assert data["quantityLots"] == 2


def test_create_intent_rejects_owner_user_id_in_payload(api_client: TestClient) -> None:
    payload = {**_BUY_PAYLOAD, "ownerUserId": str(_OWNER_ID)}

    response = api_client.post("/trade-intents", json=payload)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_intent_duplicate_returns_409(api_client: TestClient, mock_create_command: MagicMock) -> None:
    mock_create_command.execute.side_effect = DuplicateIntentError(_OWNER_ID, "2330", "buy_price_alert")

    response = api_client.post("/trade-intents", json=_BUY_PAYLOAD)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error"]["code"] == "DUPLICATE_INTENT"


# ------------------------------------------------------------------
# GET /trade-intents — list
# ------------------------------------------------------------------


def test_list_intents_returns_only_owner_resources(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.list_by_owner.return_value = ([_make_intent()], None)

    response = api_client.get("/trade-intents")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert len(body["data"]) == 1
    # verify owner filter was applied with the local user id
    call_kwargs = mock_repo.list_by_owner.call_args
    assert call_kwargs.kwargs["owner_user_id"] == _OWNER_ID


def test_list_intents_status_filter(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.list_by_owner.return_value = ([], None)

    api_client.get("/trade-intents?status=active&status=scheduled")

    call_kwargs = mock_repo.list_by_owner.call_args.kwargs
    assert set(call_kwargs["statuses"]) == {"active", "scheduled"}


def test_list_intents_comma_separated_status(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.list_by_owner.return_value = ([], None)

    api_client.get("/trade-intents?status=active,scheduled")

    call_kwargs = mock_repo.list_by_owner.call_args.kwargs
    assert set(call_kwargs["statuses"]) == {"active", "scheduled"}


def test_list_intents_invalid_status_returns_422(api_client: TestClient) -> None:
    response = api_client.get("/trade-intents?status=INVALID")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------
# POST /trade-intents/{id}/cancel
# ------------------------------------------------------------------


def test_cancel_active_intent_success(api_client: TestClient, mock_cancel_command: MagicMock) -> None:
    mock_cancel_command.execute.return_value = _make_intent(status="cancelled")

    response = api_client.post(f"/trade-intents/{_INTENT_ID}/cancel")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"]["status"] == "cancelled"


def test_cancel_triggered_intent_returns_409(api_client: TestClient, mock_cancel_command: MagicMock) -> None:
    mock_cancel_command.execute.side_effect = CancelNotAllowedError(_INTENT_ID, "triggered")

    response = api_client.post(f"/trade-intents/{_INTENT_ID}/cancel")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["error"]["code"] == "CANCEL_NOT_ALLOWED"


def test_cancel_nonexistent_intent_returns_404(api_client: TestClient, mock_cancel_command: MagicMock) -> None:
    mock_cancel_command.execute.side_effect = IntentNotFoundError(_INTENT_ID)

    response = api_client.post(f"/trade-intents/{_INTENT_ID}/cancel")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "NOT_FOUND"
