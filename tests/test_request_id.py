from collections.abc import Callable
from uuid import UUID

from fastapi.testclient import TestClient


def test_preserves_request_id_header(client_factory: Callable[[bool], TestClient]) -> None:
    client: TestClient = client_factory(database_available=True)

    response = client.get("/health", headers={"X-Request-Id": "client-request-id"})

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "client-request-id"


def test_generates_request_id_when_missing(client_factory: Callable[[bool], TestClient]) -> None:
    client: TestClient = client_factory(database_available=True)

    response = client.get("/health")

    request_id = response.headers["X-Request-Id"]
    assert UUID(request_id)


def test_rejects_too_long_request_id(client_factory: Callable[[bool], TestClient]) -> None:
    client: TestClient = client_factory(database_available=True)

    response = client.get("/health", headers={"X-Request-Id": "x" * 129})

    assert response.status_code == 400
    request_id = response.headers["X-Request-Id"]
    assert UUID(request_id)
    assert response.json() == {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "請求資料不合法",
            "details": {"field": "X-Request-Id", "reason": "request id length must be 1..128"},
            "requestId": request_id,
        }
    }
