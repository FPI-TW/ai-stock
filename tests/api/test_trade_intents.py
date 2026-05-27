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
from app.domain.trade_intent import (
    CancelNotAllowedError,
    DuplicateIntentError,
    IntentNotFoundError,
    TradeIntentData,
)
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
_LIMIT_BUY_PAYLOAD = {
    "symbol": "2330",
    "strategy": "limit_buy_order",
    "quantityLots": 1,
    "targetPrice": "600",
    "transactionMode": "partial_fill_allowed",
    "notificationMode": "single",
}
_TRAILING_STOP_PAYLOAD = {
    "symbol": "2330",
    "strategy": "trailing_stop_alert",
    "positionSide": "long",
    "quantityLots": 1,
    "trailMode": "percentage",
    "trailValue": "5.0",
}


def _make_intent(
    *,
    strategy: str = "buy_price_alert",
    status: str = "active",
    symbol: str = "2330",
    quantity_lots: int = 1,
    price: str = "600",
    cancelled_at: datetime | None = None,
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
        cancelled_at=cancelled_at,
    )


def _make_trailing_intent(
    *,
    position_side: str = "long",
    trail_mode: str = "percentage",
    trail_value: str = "5.0",
    status: str = "active",
    symbol: str = "2330",
    quantity_lots: int = 1,
) -> TradeIntentData:
    now = datetime.now(tz=UTC)
    return TradeIntentData(
        id=_INTENT_ID,
        owner_user_id=_OWNER_ID,
        symbol=symbol,
        strategy="trailing_stop_alert",
        execution_mode="notify_only",
        quantity_lots=quantity_lots,
        target_price_original=None,
        target_price_effective=None,
        trigger_reference_price_type="bid" if position_side == "long" else "ask",
        trading_date=date.today(),
        time_in_force="day",
        status=status,
        created_at=now,
        updated_at=now,
        position_side=position_side,
        trail_mode=trail_mode,
        trail_value=Decimal(trail_value),
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
    assert "createdAt" in data


def test_create_sell_alert_success(api_client: TestClient, mock_create_command: MagicMock) -> None:
    mock_create_command.execute.return_value = _make_intent(strategy="sell_price_alert", price="650", quantity_lots=2)

    response = api_client.post("/trade-intents", json=_SELL_PAYLOAD)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()["data"]
    assert data["strategy"] == "sell_price_alert"
    assert data["quantityLots"] == 2


def test_create_limit_order_rejected_until_v0_5_15(api_client: TestClient, mock_create_command: MagicMock) -> None:
    """V0.5 union 暫不開放 limit_*_order;BE-V0.5-15 接手後改為 201。

    避免 schema 通過後 command 邊界以 500 收尾。子 schema 行為由
    tests/test_intent_create_schema.py 直接驗證。
    """
    response = api_client.post("/trade-intents", json=_LIMIT_BUY_PAYLOAD)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    mock_create_command.execute.assert_not_called()


def test_create_trailing_stop_long_percentage_success(api_client: TestClient, mock_create_command: MagicMock) -> None:
    """Trailing 建單回 201,response 含 trailing 欄位,target_price_* 為 null。"""
    mock_create_command.execute.return_value = _make_trailing_intent(
        position_side="long", trail_mode="percentage", trail_value="5.0"
    )

    response = api_client.post("/trade-intents", json=_TRAILING_STOP_PAYLOAD)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()["data"]
    assert data["strategy"] == "trailing_stop_alert"
    assert data["status"] == "active"
    assert data["positionSide"] == "long"
    assert data["trailMode"] == "percentage"
    assert data["trailValue"] == "5.0"  # str(Decimal) preserves source precision
    assert data["targetPriceOriginal"] is None
    assert data["targetPriceEffective"] is None
    assert data["watermarkHigh"] is None
    assert data["watermarkLow"] is None
    assert data["dynamicTriggerPrice"] is None
    assert data["watermarkUpdatedAt"] is None
    mock_create_command.execute.assert_called_once()
    command_input = mock_create_command.execute.call_args.args[0]
    assert command_input.strategy == "trailing_stop_alert"
    assert command_input.position_side == "long"
    assert command_input.trail_mode == "percentage"
    assert command_input.trail_value == "5.0"
    assert command_input.target_price is None


def test_create_trailing_stop_short_fixed_amount_success(
    api_client: TestClient, mock_create_command: MagicMock
) -> None:
    payload = {
        "symbol": "2330",
        "strategy": "trailing_stop_alert",
        "positionSide": "short",
        "quantityLots": 1,
        "trailMode": "fixed_amount",
        "trailValue": "5",
    }
    mock_create_command.execute.return_value = _make_trailing_intent(
        position_side="short", trail_mode="fixed_amount", trail_value="5"
    )

    response = api_client.post("/trade-intents", json=payload)

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()["data"]
    assert data["positionSide"] == "short"
    assert data["trailMode"] == "fixed_amount"


def test_create_trailing_stop_missing_position_side_returns_422(
    api_client: TestClient, mock_create_command: MagicMock
) -> None:
    payload = {k: v for k, v in _TRAILING_STOP_PAYLOAD.items() if k != "positionSide"}

    response = api_client.post("/trade-intents", json=payload)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    locs = [tuple(e["loc"]) for e in body["error"]["details"]["errors"]]
    assert any("positionSide" in loc for loc in locs)
    mock_create_command.execute.assert_not_called()


def test_create_trailing_stop_with_target_price_returns_422(
    api_client: TestClient, mock_create_command: MagicMock
) -> None:
    """trailing 子 schema extra=forbid;多帶 targetPrice 一律 422。"""
    payload = {**_TRAILING_STOP_PAYLOAD, "targetPrice": "600"}

    response = api_client.post("/trade-intents", json=payload)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    locs = [tuple(e["loc"]) for e in body["error"]["details"]["errors"]]
    assert any("targetPrice" in loc for loc in locs)
    mock_create_command.execute.assert_not_called()


def test_create_trailing_stop_percentage_above_50_returns_422(
    api_client: TestClient, mock_create_command: MagicMock
) -> None:
    payload = {**_TRAILING_STOP_PAYLOAD, "trailValue": "60"}

    response = api_client.post("/trade-intents", json=payload)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    locs = [tuple(e["loc"]) for e in body["error"]["details"]["errors"]]
    assert any("trailValue" in loc for loc in locs)
    mock_create_command.execute.assert_not_called()


def test_create_trailing_stop_fixed_amount_invalid_tick_returns_422(
    api_client: TestClient, mock_create_command: MagicMock
) -> None:
    """Trailing fixed_amount mode: trail_value must match the symbol's tick
    table. Command raises InvalidTickSizeError with field='trailValue'; the
    envelope surfaces that field so clients know which input was rejected."""
    from app.domain.price import InvalidTickSizeError

    mock_create_command.execute.side_effect = InvalidTickSizeError(
        "0.07",
        "not a valid tick multiple",
        Decimal("0.05"),
        Decimal("0.10"),
        field="trailValue",
    )

    payload = {**_TRAILING_STOP_PAYLOAD, "trailMode": "fixed_amount", "trailValue": "0.07"}

    response = api_client.post("/trade-intents", json=payload)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["error"]["code"] == "INVALID_TICK_SIZE"
    assert body["error"]["details"]["field"] == "trailValue"
    assert body["error"]["details"]["value"] == "0.07"


def test_get_trailing_intent_returns_trailing_fields(api_client: TestClient, mock_repo: MagicMock) -> None:
    """GET /trade-intents/{id} for a trailing row exposes trailing fields and
    null target_price_*."""
    trailing = _make_trailing_intent(position_side="short", trail_mode="fixed_amount", trail_value="5")
    mock_repo.find_by_id.return_value = trailing

    response = api_client.get(f"/trade-intents/{trailing.id}")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert data["strategy"] == "trailing_stop_alert"
    assert data["positionSide"] == "short"
    assert data["trailMode"] == "fixed_amount"
    assert data["trailValue"] == "5"
    assert data["targetPriceOriginal"] is None
    assert data["targetPriceEffective"] is None


def test_create_intent_rejects_owner_user_id_in_payload(api_client: TestClient) -> None:
    payload = {**_BUY_PAYLOAD, "ownerUserId": str(_OWNER_ID)}

    response = api_client.post("/trade-intents", json=payload)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_intent_zero_quantity_returns_422(api_client: TestClient) -> None:
    response = api_client.post("/trade-intents", json={**_BUY_PAYLOAD, "quantityLots": 0})

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
    call_kwargs = mock_repo.list_by_owner.call_args
    assert call_kwargs.kwargs["owner_user_id"] == _OWNER_ID


def test_list_intents_empty_returns_200(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.list_by_owner.return_value = ([], None)

    response = api_client.get("/trade-intents")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["data"] == []
    assert body["nextCursor"] is None


def test_list_intents_returns_next_cursor(api_client: TestClient, mock_repo: MagicMock) -> None:
    next_id = str(uuid4())
    mock_repo.list_by_owner.return_value = ([_make_intent()], next_id)

    response = api_client.get("/trade-intents")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["nextCursor"] == next_id


def test_list_intents_status_filter(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.list_by_owner.return_value = ([], None)

    response = api_client.get("/trade-intents?status=active&status=scheduled")

    assert response.status_code == status.HTTP_200_OK
    call_kwargs = mock_repo.list_by_owner.call_args.kwargs
    assert set(call_kwargs["statuses"]) == {"active", "scheduled"}


def test_list_intents_comma_separated_status(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.list_by_owner.return_value = ([], None)

    response = api_client.get("/trade-intents?status=active,scheduled")

    assert response.status_code == status.HTTP_200_OK
    call_kwargs = mock_repo.list_by_owner.call_args.kwargs
    assert set(call_kwargs["statuses"]) == {"active", "scheduled"}


def test_list_intents_comma_separated_status_with_spaces(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.list_by_owner.return_value = ([], None)

    response = api_client.get("/trade-intents?status=active,%20scheduled")

    assert response.status_code == status.HTTP_200_OK
    call_kwargs = mock_repo.list_by_owner.call_args.kwargs
    assert set(call_kwargs["statuses"]) == {"active", "scheduled"}


def test_list_intents_invalid_status_returns_422(api_client: TestClient) -> None:
    response = api_client.get("/trade-intents?status=INVALID")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_list_intents_invalid_cursor_format_returns_400(api_client: TestClient) -> None:
    response = api_client.get("/trade-intents?cursor=not-a-uuid")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_list_intents_expired_cursor_returns_400(api_client: TestClient, mock_repo: MagicMock) -> None:
    from app.domain.trade_intent import InvalidCursorError

    stale_id = uuid4()
    mock_repo.list_by_owner.side_effect = InvalidCursorError(stale_id)

    response = api_client.get(f"/trade-intents?cursor={stale_id}")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "INVALID_CURSOR"


# ------------------------------------------------------------------
# GET /trade-intents/{id}
# ------------------------------------------------------------------


def test_get_intent_success(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.find_by_id.return_value = _make_intent()

    response = api_client.get(f"/trade-intents/{_INTENT_ID}")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert data["id"] == str(_INTENT_ID)
    assert "createdAt" in data
    mock_repo.find_by_id.assert_called_once_with(_INTENT_ID, _OWNER_ID)


def test_get_intent_not_found_returns_404(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.find_by_id.side_effect = IntentNotFoundError(_INTENT_ID)

    response = api_client.get(f"/trade-intents/{_INTENT_ID}")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------------------
# POST /trade-intents/{id}/cancel
# ------------------------------------------------------------------


def test_cancel_active_intent_success(api_client: TestClient, mock_cancel_command: MagicMock) -> None:
    now = datetime.now(tz=UTC)
    mock_cancel_command.execute.return_value = _make_intent(status="cancelled", cancelled_at=now)

    response = api_client.post(f"/trade-intents/{_INTENT_ID}/cancel")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert data["status"] == "cancelled"
    assert data["cancelledAt"] is not None


def test_cancel_already_cancelled_is_idempotent(api_client: TestClient, mock_cancel_command: MagicMock) -> None:
    """Re-cancelling an already-cancelled intent returns 200 (idempotent)."""
    now = datetime.now(tz=UTC)
    mock_cancel_command.execute.return_value = _make_intent(status="cancelled", cancelled_at=now)

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
