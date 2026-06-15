import pytest
from starlette.requests import Request

from app.api.routes.auth import _enforce_csrf_origin
from app.domain.auth import CsrfFailedError

_ALLOWED = ["https://app.example.com"]


def _request(headers: dict[str, str]) -> Request:
    raw = [(key.lower().encode(), value.encode()) for key, value in headers.items()]
    return Request({"type": "http", "headers": raw})


def test_allowed_origin_passes() -> None:
    _enforce_csrf_origin(_request({"origin": "https://app.example.com"}), _ALLOWED)


def test_disallowed_origin_rejected() -> None:
    with pytest.raises(CsrfFailedError):
        _enforce_csrf_origin(_request({"origin": "https://evil.example.com"}), _ALLOWED)


def test_referer_fallback_allowed() -> None:
    _enforce_csrf_origin(_request({"referer": "https://app.example.com/login"}), _ALLOWED)


def test_referer_fallback_rejected() -> None:
    with pytest.raises(CsrfFailedError):
        _enforce_csrf_origin(_request({"referer": "https://evil.example.com/page"}), _ALLOWED)


def test_no_origin_no_referer_falls_through() -> None:
    # Neither header present → rely on the double-submit token; do not hard-fail
    # (non-browser clients and some same-origin requests omit both).
    _enforce_csrf_origin(_request({}), _ALLOWED)
