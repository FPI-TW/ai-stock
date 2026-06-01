from fastapi.testclient import TestClient

from tests.conftest import ClientFactory


def test_health_success(client_factory: ClientFactory) -> None:
    client: TestClient = client_factory(database_available=True)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "service": "ai-stock-api",
            "version": "0.5.0",
            "environment": "local",
            "database": "ok",
        }
    }
    assert response.headers["X-Request-Id"]


def test_health_database_unavailable_uses_error_envelope(client_factory: ClientFactory) -> None:
    client: TestClient = client_factory(database_available=False)

    response = client.get("/health", headers={"X-Request-Id": "req-db-down"})

    assert response.status_code == 503
    assert response.headers["X-Request-Id"] == "req-db-down"
    assert response.json() == {
        "error": {
            "code": "DATABASE_UNAVAILABLE",
            "message": "資料庫暫時無法使用",
            "details": {},
            "requestId": "req-db-down",
        }
    }


def test_cors_allows_local_frontend(client_factory: ClientFactory) -> None:
    client: TestClient = client_factory(database_available=True)

    for origin in ("http://localhost:3000", "http://localhost:3001", "http://localhost:3100"):
        response = client.options(
            "/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
