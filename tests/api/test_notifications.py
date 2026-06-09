"""API tests for GET /notifications and POST /notifications/{id}/read."""

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_mark_notification_read_command, get_notification_repository
from app.core.security import RequestUser
from app.domain.notification import NotificationData, NotificationNotFoundError
from app.domain.trade_intent import InvalidCursorError
from app.main import create_app

_OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")
_NOTIFICATION_ID = uuid4()
_INTENT_ID = uuid4()


def _make_notification(
    *,
    notification_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    read_at: datetime | None = None,
    rendered_title: str = "2330 到價提醒已觸發",
    rendered_body: str = (
        "買進到價提醒 2330\n目標價：100.0000\n觸發價：99.5000\n2026-05-12 10:00:00\n僅通知、未下單、不保證成交。"
    ),
) -> NotificationData:
    now = datetime.now(tz=UTC)
    return NotificationData(
        id=notification_id or _NOTIFICATION_ID,
        owner_user_id=owner_user_id or _OWNER_ID,
        trade_intent_id=_INTENT_ID,
        type="price_triggered",
        rendered_title=rendered_title,
        rendered_body=rendered_body,
        read_at=read_at,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def mock_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_command() -> MagicMock:
    return MagicMock()


@pytest.fixture
def api_client(mock_repo: MagicMock, mock_command: MagicMock) -> Generator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_notification_repository] = lambda: mock_repo
    app.dependency_overrides[get_mark_notification_read_command] = lambda: mock_command
    app.dependency_overrides[get_current_user] = lambda: RequestUser(user_id=_OWNER_ID, role="user")
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


# ------------------------------------------------------------------
# GET /notifications — list
# ------------------------------------------------------------------


def test_list_returns_created_notification(api_client: TestClient, mock_repo: MagicMock) -> None:
    notification = _make_notification()
    mock_repo.list_by_owner.return_value = ([notification], None)

    response = api_client.get("/notifications")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert len(body["data"]) == 1
    item = body["data"][0]
    assert item["id"] == str(notification.id)
    assert item["type"] == "price_triggered"
    assert item["tradeIntentId"] == str(_INTENT_ID)
    assert item["renderedTitle"] == notification.rendered_title
    assert item["renderedBody"] == notification.rendered_body
    assert item["readAt"] is None
    assert "createdAt" in item
    assert body["nextCursor"] is None
    assert body["pageSize"] == 50


def test_list_passes_owner_from_context(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.list_by_owner.return_value = ([], None)

    response = api_client.get("/notifications")

    assert response.status_code == status.HTTP_200_OK
    call_kwargs = mock_repo.list_by_owner.call_args.kwargs
    assert call_kwargs["owner_user_id"] == _OWNER_ID


def test_list_empty_returns_200(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.list_by_owner.return_value = ([], None)

    response = api_client.get("/notifications")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["data"] == []
    assert body["nextCursor"] is None


def test_list_unread_only_propagates_to_repo(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.list_by_owner.return_value = ([_make_notification(read_at=None)], None)

    response = api_client.get("/notifications?unreadOnly=true")

    assert response.status_code == status.HTTP_200_OK
    call_kwargs = mock_repo.list_by_owner.call_args.kwargs
    assert call_kwargs["unread_only"] is True


def test_list_default_unread_only_is_false(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.list_by_owner.return_value = ([], None)

    api_client.get("/notifications")

    call_kwargs = mock_repo.list_by_owner.call_args.kwargs
    assert call_kwargs["unread_only"] is False


def test_list_returns_next_cursor(api_client: TestClient, mock_repo: MagicMock) -> None:
    next_id = str(uuid4())
    mock_repo.list_by_owner.return_value = ([_make_notification()], next_id)

    response = api_client.get("/notifications")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["nextCursor"] == next_id


def test_list_page_size_respects_query(api_client: TestClient, mock_repo: MagicMock) -> None:
    mock_repo.list_by_owner.return_value = ([], None)

    response = api_client.get("/notifications?pageSize=25")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["pageSize"] == 25
    assert mock_repo.list_by_owner.call_args.kwargs["page_size"] == 25


def test_list_page_size_below_one_returns_422(api_client: TestClient) -> None:
    response = api_client.get("/notifications?pageSize=0")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_list_page_size_above_max_returns_422(api_client: TestClient) -> None:
    response = api_client.get("/notifications?pageSize=101")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


def test_list_invalid_cursor_format_returns_400(api_client: TestClient) -> None:
    response = api_client.get("/notifications?cursor=not-a-uuid")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]["cursor"] == "must be a valid UUID"


def test_list_expired_cursor_returns_400(api_client: TestClient, mock_repo: MagicMock) -> None:
    stale_id = uuid4()
    mock_repo.list_by_owner.side_effect = InvalidCursorError(stale_id)

    response = api_client.get(f"/notifications?cursor={stale_id}")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "INVALID_CURSOR"


# ------------------------------------------------------------------
# POST /notifications/{id}/read
# ------------------------------------------------------------------


def test_mark_read_sets_read_at(api_client: TestClient, mock_command: MagicMock) -> None:
    now = datetime.now(tz=UTC)
    mock_command.execute.return_value = _make_notification(read_at=now)

    response = api_client.post(f"/notifications/{_NOTIFICATION_ID}/read")

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["data"]["id"] == str(_NOTIFICATION_ID)
    assert body["data"]["readAt"] is not None
    # Response intentionally omits other fields per BE-V0.5-10 spec.
    assert set(body["data"].keys()) == {"id", "readAt"}


def test_mark_read_passes_owner_from_context(api_client: TestClient, mock_command: MagicMock) -> None:
    now = datetime.now(tz=UTC)
    mock_command.execute.return_value = _make_notification(read_at=now)

    api_client.post(f"/notifications/{_NOTIFICATION_ID}/read")

    inp = mock_command.execute.call_args.args[0]
    assert inp.notification_id == _NOTIFICATION_ID
    assert inp.owner_user_id == _OWNER_ID


def test_mark_read_twice_is_idempotent(api_client: TestClient, mock_command: MagicMock) -> None:
    fixed_read_at = datetime(2026, 5, 12, 2, 1, 0, tzinfo=UTC)
    mock_command.execute.return_value = _make_notification(read_at=fixed_read_at)

    first = api_client.post(f"/notifications/{_NOTIFICATION_ID}/read")
    second = api_client.post(f"/notifications/{_NOTIFICATION_ID}/read")

    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert first.json()["data"]["readAt"] == second.json()["data"]["readAt"]
    assert mock_command.execute.call_count == 2


def test_mark_read_not_found_returns_404(api_client: TestClient, mock_command: MagicMock) -> None:
    mock_command.execute.side_effect = NotificationNotFoundError(_NOTIFICATION_ID)

    response = api_client.post(f"/notifications/{_NOTIFICATION_ID}/read")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_mark_read_owner_mismatch_returns_404(api_client: TestClient, mock_command: MagicMock) -> None:
    """Owner-mismatch must surface as NOT_FOUND so we never leak existence cross-owner."""
    mock_command.execute.side_effect = NotificationNotFoundError(_NOTIFICATION_ID)

    response = api_client.post(f"/notifications/{_NOTIFICATION_ID}/read")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_mark_read_invalid_uuid_returns_422(api_client: TestClient) -> None:
    response = api_client.post("/notifications/not-a-uuid/read")

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
