"""End-to-end UI smoke tests for the /test page.

Covers the two highest-ROI UI flows that the backend pytest suite can't see:
1. User → Service cross-mode trigger: Alice creates an intent in user mode,
   the service mode pushes a matching quote, and the resulting notification
   shows up under Alice.
2. Service "wrong side" diagnostic: pushing ask against a sell_price_alert
   surfaces the smart "你應該改推送 bid=N" hint instead of just "0 觸發".

Marked `ui` so they stay out of the default `pytest` run; trigger via
`make test-ui`.
"""

from __future__ import annotations

import httpx
import pytest
from playwright.sync_api import Page

from .conftest import (
    alice_headers,
    cancel_all_active_intents,
    freeze_clock_in_session,
    reset_clock,
)


@pytest.mark.ui
def test_alice_intent_and_service_push_produces_notification(
    page: Page,
    live_server_url: str,
    api: httpx.Client,
    pre_seed_users: None,
) -> None:
    """Cross-mode flow: user 切換到 Alice 看到自己的 active 委託，service push 後通知出現。

    The create-sheet UI is exercised separately; here we API-seed the intent so
    the assertion target is the user↔service hand-off, not the multi-step form.
    """

    freeze_clock_in_session(api)
    cancel_all_active_intents(api, alice_headers())
    api.post(
        "/trade-intents",
        headers=alice_headers(),
        json={
            "symbol": "2330",
            "strategy": "buy_price_alert",
            "quantityLots": 1,
            "targetPrice": "800",
        },
    ).raise_for_status()

    try:
        page.goto(f"{live_server_url}/test")

        # ---- User mode: switch to Alice, confirm her active intent shows ----
        page.click('.mode-toggle button[data-mode="user"]')
        page.wait_for_function('() => document.querySelectorAll("#user-mode-menu .identity-menu-item").length >= 4')
        page.click("#user-mode-trigger")
        page.locator('#user-mode-menu .identity-menu-item[data-user-label="alice"]').click()
        page.wait_for_function('() => document.getElementById("user-greeting")?.textContent?.trim() === "Alice"')
        # Dashboard refreshes after switch — wait for the intent card.
        page.wait_for_selector("#uv-intent-list .intent-card", timeout=10_000)

        # ---- Service mode: push 2330 ask=800 (should trigger Alice) -------
        page.click('.mode-toggle button[data-mode="service"]')
        page.fill("#svc-quote-symbol", "2330")
        page.wait_for_selector("#svc-symbol-results .symbol-result-item")
        page.click("#svc-symbol-results .symbol-result-item >> nth=0")
        page.fill("#svc-quote-price", "800")
        page.click("#svc-push-btn")

        # Assert the push log reports Alice got triggered.
        log_locator = page.locator("#svc-push-log")
        log_locator.wait_for(state="visible")
        page.wait_for_function('() => /alice:\\s*1/.test(document.getElementById("svc-push-log")?.textContent || "")')
        log_text = log_locator.inner_text()
        assert "已推送 2330 ask=800" in log_text, log_text
        assert "alice" in log_text, log_text

        # ---- Backend cross-check: Alice has 1 notification ----------------
        notif_resp = api.get("/notifications?pageSize=10", headers=alice_headers())
        notif_resp.raise_for_status()
        items = notif_resp.json().get("data", [])
        assert len(items) >= 1, items
        assert items[0]["type"] in ("price_triggered", "limit_order_triggered"), items[0]
    finally:
        cancel_all_active_intents(api, alice_headers())
        reset_clock(api)


@pytest.mark.ui
def test_service_push_wrong_side_surfaces_diagnostic(
    page: Page,
    live_server_url: str,
    api: httpx.Client,
    pre_seed_users: None,
) -> None:
    """sell_price_alert@1000 + push ask=1000 → log 應出現「改推送 bid=...」提示。"""

    # Seed an active sell_price_alert via the API (faster + more deterministic
    # than driving the create-sheet UI; the wrong-side check is what we want).
    freeze_clock_in_session(api)
    cancel_all_active_intents(api, alice_headers())
    api.post(
        "/trade-intents",
        headers=alice_headers(),
        json={
            "symbol": "2330",
            "strategy": "sell_price_alert",
            "quantityLots": 1,
            "targetPrice": "1000",
        },
    ).raise_for_status()

    try:
        page.goto(f"{live_server_url}/test")
        page.click('.mode-toggle button[data-mode="service"]')

        page.fill("#svc-quote-symbol", "2330")
        page.wait_for_selector("#svc-symbol-results .symbol-result-item")
        page.click("#svc-symbol-results .symbol-result-item >> nth=0")
        page.fill("#svc-quote-price", "1000")
        # ask is the default side chip
        page.click("#svc-push-btn")

        log_locator = page.locator("#svc-push-log")
        log_locator.wait_for(state="visible")
        page.wait_for_function('() => /改推送\\s+bid/.test(document.getElementById("svc-push-log")?.textContent || "")')
        log_text = log_locator.inner_text()
        assert "沒有委託被觸發" in log_text, log_text
        assert "改推送 bid=1000" in log_text, log_text
        assert "alice / 賣出到價提醒" in log_text, log_text
    finally:
        cancel_all_active_intents(api, alice_headers())
        reset_clock(api)
