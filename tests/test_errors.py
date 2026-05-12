from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.main import create_app


def test_unhandled_exception_uses_internal_error_envelope() -> None:
    router = APIRouter()

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("sensitive failure")

    app = create_app()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/boom", headers={"X-Request-Id": "req-boom"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "發生未預期錯誤",
            "details": {},
            "requestId": "req-boom",
        }
    }
