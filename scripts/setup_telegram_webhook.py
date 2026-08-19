#!/usr/bin/env python3
"""Set and verify the Telegram webhook used by the inbound workflow."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NoReturn

WEBHOOK_PATH = "/telegram/webhook"
ALLOWED_UPDATES = ["message", "callback_query"]


def main() -> None:
    _load_dotenv(Path(".env"))
    args = _parse_args()
    token = _require_env("TELEGRAM_BOT_TOKEN")
    if args.delete:
        response = _telegram_request(
            token,
            "deleteWebhook",
            http_method="POST",
            payload={"drop_pending_updates": False},
        )
        if not response.get("ok"):
            _die(f"deleteWebhook failed: {response}")
        print("Deleted Telegram webhook.")
        return

    secret = _require_env("TELEGRAM_WEBHOOK_SECRET")
    webhook_url = _webhook_url(args.url or _require_env("TELEGRAM_WEBHOOK_URL"))
    _set_webhook(token, webhook_url, secret)
    info = _get_webhook_info(token)
    _verify_webhook_info(info, webhook_url)
    print(f"Verified Telegram webhook: {webhook_url}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure and verify the Telegram webhook.")
    parser.add_argument("--url", help="Public HTTPS base URL or full /telegram/webhook URL.")
    parser.add_argument("--delete", action="store_true", help="Delete the current Telegram webhook.")
    return parser.parse_args()


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        _die(f"{name} is required")
    return value


def _webhook_url(base_or_full_url: str) -> str:
    url = base_or_full_url.rstrip("/")
    if not url.startswith("https://"):
        _die("Telegram webhook URL must be public HTTPS")
    return url if url.endswith(WEBHOOK_PATH) else f"{url}{WEBHOOK_PATH}"


def _validate_secret(secret: str) -> None:
    if not 1 <= len(secret) <= 256 or re.fullmatch(r"[A-Za-z0-9_-]+", secret) is None:
        _die("TELEGRAM_WEBHOOK_SECRET must be 1-256 characters using only A-Z, a-z, 0-9, _ or -")


def _set_webhook(token: str, webhook_url: str, secret: str | None) -> None:
    if not secret:
        _die("TELEGRAM_WEBHOOK_SECRET is required")
    _validate_secret(secret)
    response = _telegram_request(
        token,
        "setWebhook",
        http_method="POST",
        payload={
            "url": webhook_url,
            "secret_token": secret,
            "allowed_updates": ALLOWED_UPDATES,
        },
    )
    if not response.get("ok"):
        _die(f"setWebhook failed: {response}")


def _get_webhook_info(token: str) -> dict[str, Any]:
    return _telegram_request(token, "getWebhookInfo", http_method="GET", payload={})


def _verify_webhook_info(response: dict[str, Any], expected_url: str) -> None:
    if response.get("ok") is not True:
        _die(f"getWebhookInfo failed: {response}")
    result = response.get("result")
    if not isinstance(result, dict):
        _die("getWebhookInfo returned no result")
    actual_url = result.get("url")
    actual_allowed = result.get("allowed_updates")
    if (
        actual_url != expected_url
        or not isinstance(actual_allowed, list)
        or set(actual_allowed) != set(ALLOWED_UPDATES)
    ):
        _die("Telegram webhook verification failed")


def _set_webhook_with_retry(token: str, webhook_url: str, secret: str | None, wait_timeout: int) -> None:
    deadline = time.monotonic() + wait_timeout
    last_error = "not checked"
    while time.monotonic() < deadline:
        try:
            _set_webhook(token, webhook_url, secret)
            return
        except RuntimeError as exc:
            last_error = str(exc)
        time.sleep(1)
    _die(f"Telegram did not accept webhook in time: {last_error}")


def _telegram_request(token: str, telegram_method: str, *, http_method: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{telegram_method}",
        method=http_method,
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8") if http_method == "POST" else None,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"Telegram {telegram_method} request failed") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"Telegram {telegram_method} returned invalid response")
    return decoded


def _die(message: str) -> NoReturn:
    raise RuntimeError(message)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
