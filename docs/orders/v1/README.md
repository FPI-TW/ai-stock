# V1 後端工單（respec）— 工單檔

> 總索引：`docs/v1-backend-work-orders.respec.md`（切分原則、依賴圖、上線分層、舊 20→新 9 對應）。
> 基準：06-03 範圍收斂後的 `docs/domain-spec.md`。現況 code 為 V0.5 單機 local-user 平台，本套只切 V1 缺口。

## 工單清單

**上線前（身份驗證 + 防護）**
- [L1 身分 / 帳號 / Session](L1-identity-accounts-session.md) — 登入、帳號生命週期、admin 建帳號、JWT/refresh、role、真 owner scoping、admin 2FA（[白話說明](L1-白話說明.md)）
- [L2 平台守門 / 防護](L2-platform-guard.md) — audit / idempotency / 限流 / 建立上限 / **緊急停止鈕**（[白話說明](L2-白話說明.md)）
- [L3 安全與部署硬化](L3-security-deployment-hardening.md) — CSRF/CORS/cookie/secrets/密碼規則/部署/備份/auth E2E（[白話說明](L3-白話說明.md)）

**上線後**
- [P1 停利/停損 + OCO](P1-take-profit-stop-loss-oco.md)（[白話說明](P1-白話說明.md)）
- [P2 通知交付保證（Outbox）](P2-outbox-notification-delivery.md)（[白話說明](P2-白話說明.md)）
- [P2.5 intent 生命週期 audit](P2.5-intent-lifecycle-audit.md) — 補齊 §17 的 `intent_created`/`activated`/`triggered`/`expired`/`cancelled`（L2 清單列出但無票認領的孤兒；接於 P2 後）（[白話說明](P2.5-白話說明.md)）
- [P3 Telegram 綁定 + 通知設定](P3-telegram-binding-notification-settings.md)（[白話說明](P3-白話說明.md)）
- [P4 除息調整](P4-corporate-action-dividend.md)（[白話說明](P4-白話說明.md)）
- [P5 Admin 監控 + 覆寫 + Kill Switch 完整版](P5-admin-monitoring-overrides-killswitch.md)（[白話說明](P5-白話說明.md)）
- [P6 資料保留 / 隱私 + SLO](P6-retention-privacy-slo.md)（[白話說明](P6-白話說明.md)）

**技術債 / 重構（V0.5 遺留，上線後可獨立排程）**
- [T2 trade_intents create endpoint 按策略拆分](T2-split-trade-intent-create-endpoints.md) — 單一萬用 `POST /trade-intents` 拆成每策略 typed 端點，消除 union getattr；單表不動、legacy 端點保留（[白話說明](T2-白話說明.md)）

## 執行序

L1 → L2 → L3 →【上線】→（P1 ‖ P2 ‖ P4）→ P2.5 → P3 → P5 → P6
（P2.5 接於 P2 後：`intent_triggered` 需 P2 refactor 後的 outbox transaction）

T2（技術債）無功能依賴，與 T1（schema 衛星表分層，另分支）為兩件獨立事——T2 只動 API 層、單表不動。
