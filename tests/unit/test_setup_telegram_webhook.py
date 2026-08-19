from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "setup_telegram_webhook.py"
    spec = importlib.util.spec_from_file_location("setup_telegram_webhook", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_webhook_info_requires_exact_url_and_allowed_updates() -> None:
    script = _load_script()
    expected_url = "https://example.invalid/telegram/webhook"

    script._verify_webhook_info(
        {"ok": True, "result": {"url": expected_url, "allowed_updates": ["callback_query", "message"]}},
        expected_url,
    )

    with pytest.raises(RuntimeError):
        script._verify_webhook_info(
            {"ok": True, "result": {"url": expected_url, "allowed_updates": ["message"]}},
            expected_url,
        )


def test_set_webhook_sends_secret_and_exact_allowed_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script()
    captured: dict[str, object] = {}

    def fake_request(token: str, method: str, *, http_method: str, payload: dict[str, object]) -> dict[str, object]:
        captured.update({"token": token, "method": method, "http_method": http_method, "payload": payload})
        return {"ok": True}

    monkeypatch.setattr(script, "_telegram_request", fake_request)
    script._set_webhook("test-token", "https://example.invalid/telegram/webhook", "test-secret")

    assert captured == {
        "token": "test-token",
        "method": "setWebhook",
        "http_method": "POST",
        "payload": {
            "url": "https://example.invalid/telegram/webhook",
            "secret_token": "test-secret",
            "allowed_updates": ["message", "callback_query"],
        },
    }
