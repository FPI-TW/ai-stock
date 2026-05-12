import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.integration
def test_health_success_with_real_database() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["data"]["database"] == "ok"
