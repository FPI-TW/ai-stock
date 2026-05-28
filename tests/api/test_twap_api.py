from collections.abc import Generator
from datetime import UTC, date, datetime, time
from unittest.mock import MagicMock
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.deps import get_intent_repository, get_twap_confirm_command, get_twap_plan_command
from app.commands.twap import TwapConfirmOutput
from app.domain.trade_intent import TradeIntentData, TwapSliceData
from app.domain.trading_session import TradingDayPhase
from app.domain.twap import TwapInsufficientSlicesError, TwapPlan, TwapSlicePlan
from app.main import create_app

OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")
TAIPEI = ZoneInfo("Asia/Taipei")


def _plan() -> TwapPlan:
    start_at = datetime(2026, 5, 28, 9, 0, tzinfo=TAIPEI)
    return TwapPlan(
        position_side="long",
        trading_phase=TradingDayPhase.PRE_MARKET,
        trading_date=date(2026, 5, 28),
        start_at=start_at,
        end_at=datetime(2026, 5, 28, 9, 5, tzinfo=TAIPEI),
        interval_seconds=300,
        target_quantity_lots=2,
        available_slice_count=2,
        materialized_slice_count=2,
        slices=(
            TwapSlicePlan(sequence_no=1, scheduled_at=start_at, planned_quantity_lots=1),
            TwapSlicePlan(
                sequence_no=2,
                scheduled_at=datetime(2026, 5, 28, 9, 5, tzinfo=TAIPEI),
                planned_quantity_lots=1,
            ),
        ),
    )


def _intent(intent_id: UUID) -> TradeIntentData:
    now = datetime.now(tz=UTC)
    return TradeIntentData(
        id=intent_id,
        owner_user_id=OWNER_ID,
        symbol="2330",
        strategy="twap_order",
        execution_mode="notify_only",
        quantity_lots=2,
        target_price_original=None,
        target_price_effective=None,
        trigger_reference_price_type="last_fallback",
        trading_date=date(2026, 5, 28),
        time_in_force="day",
        status="scheduled",
        created_at=now,
        updated_at=now,
        position_side="long",
        twap_interval_seconds=300,
        twap_end_time=time(9, 5),
        twap_start_at=datetime(2026, 5, 28, 9, 0, tzinfo=TAIPEI),
        twap_end_at=datetime(2026, 5, 28, 9, 5, tzinfo=TAIPEI),
        twap_available_slice_count=2,
        twap_materialized_slice_count=2,
    )


def _slice(intent_id: UUID, sequence_no: int) -> TwapSliceData:
    now = datetime.now(tz=UTC)
    return TwapSliceData(
        id=uuid4(),
        trade_intent_id=intent_id,
        owner_user_id=OWNER_ID,
        symbol="2330",
        sequence_no=sequence_no,
        scheduled_at=datetime(2026, 5, 28, 9, 0 if sequence_no == 1 else 5, tzinfo=TAIPEI),
        planned_quantity_lots=1,
        status="pending",
        primary_notification_id=None,
        notified_at=None,
        primary_price_available=None,
        primary_reference_price=None,
        primary_reference_price_type=None,
        primary_quote_time=None,
        price_followup_required=False,
        price_followup_attempts=0,
        next_price_followup_at=None,
        price_followup_notification_id=None,
        price_followup_sent_at=None,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def plan_command() -> MagicMock:
    return MagicMock()


@pytest.fixture
def confirm_command() -> MagicMock:
    return MagicMock()


@pytest.fixture
def repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def client(plan_command: MagicMock, confirm_command: MagicMock, repo: MagicMock) -> Generator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_twap_plan_command] = lambda: plan_command
    app.dependency_overrides[get_twap_confirm_command] = lambda: confirm_command
    app.dependency_overrides[get_intent_repository] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_twap_preview_returns_schedule(client: TestClient, plan_command: MagicMock) -> None:
    plan_command.preview.return_value = _plan()

    response = client.post(
        "/trade-intents/twap/preview",
        json={
            "symbol": "2330",
            "positionSide": "long",
            "quantityLots": 2,
            "intervalSeconds": 300,
            "endTime": "09:05:00",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()["data"]
    assert data["strategy"] == "twap_order"
    assert data["positionSide"] == "long"
    assert data["availableSliceCount"] == 2
    assert data["slices"][0]["plannedQuantityLots"] == 1


def test_twap_confirm_creates_intent_and_returns_slices(
    client: TestClient,
    confirm_command: MagicMock,
    repo: MagicMock,
) -> None:
    intent_id = uuid4()
    confirm_command.execute.return_value = TwapConfirmOutput(intent=_intent(intent_id))
    repo.list_twap_slices.return_value = [_slice(intent_id, 1), _slice(intent_id, 2)]

    response = client.post(
        "/trade-intents/twap/confirm",
        json={
            "symbol": "2330",
            "positionSide": "long",
            "quantityLots": 2,
            "intervalSeconds": 300,
            "endTime": "09:05:00",
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()["data"]
    assert data["strategy"] == "twap_order"
    assert data["twap"]["materializedSliceCount"] == 2
    assert len(data["twapSlices"]) == 2


def test_twap_preview_domain_error_uses_twap_error_code(client: TestClient, plan_command: MagicMock) -> None:
    plan_command.preview.side_effect = TwapInsufficientSlicesError(available_slice_count=1, materialized_slice_count=1)

    response = client.post(
        "/trade-intents/twap/preview",
        json={"symbol": "2330", "positionSide": "long", "quantityLots": 2, "intervalSeconds": 1, "endTime": "09:00:00"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["error"]["code"] == "TWAP_INSUFFICIENT_SLICES"
