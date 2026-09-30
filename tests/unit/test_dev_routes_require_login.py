"""`/dev/*` is gated by a logged-in, active user — explicitly on the router, not
as a side effect of the quote-provider dependency."""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/dev/evaluate-quotes"),
        ("POST", "/dev/twap/process-due-slices"),
        ("POST", "/dev/twap/process-price-followups"),
    ],
)
def test_dev_routes_reject_anonymous_callers(method: str, path: str) -> None:
    response = TestClient(create_app(), raise_server_exceptions=False).request(method, path, json={})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
