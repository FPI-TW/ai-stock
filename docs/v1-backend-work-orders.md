# V1 後端工單（respec）— 總索引

> **狀態**：現行版。已取代舊 `docs/orders/v1/BE-V1-01…20` 與舊索引（皆封存於 `docs/orders/v1/_archive/`）。
> **基準**：06-03 範圍收斂後的 `docs/domain-spec.md` 為唯一權威。
> **現況**：V0.5 已交付單機 local-user 平台（TradeIntent 生命週期、8 種策略、TWAP、quote evaluator+trigger、in_app+telegram(best-effort) 通知、Shioaji demo、tick service）。本套工單只切「V1 缺口」。
> **個別工單檔**：`docs/orders/v1/`（L1–L3 + P1–P6 + 各白話說明，見該目錄 README）。

---

## 1. 切分原則

1. **粗顆粒**：一張工單 = 一個內聚主題，不為單一欄位 / 單一 endpoint 開票。寧可票內用 P0/P1 分階段，也不跨票切碎。
2. **以擁有權界定邊界**：每張明寫「擁有（範圍內）」與「不碰（邊界外，指向擁有者）」，避免兩票改同一塊。
3. **依賴顯式**：跨票依賴一律在票頭寫清楚（硬依賴 vs 唯讀引用）；用「先 stub、後票補真實作並 refactor」解循環。
4. **對齊上線目標**：以「身份驗證 + 防護完成即可上線」為分界，工單分**上線前 / 上線後**兩層；上線前路徑最小化。

---

## 2. 上線目標與分層

**上線定義（使用者拍板）**：身份驗證 + 防護做好即上線。靠 V0.5 既有功能（8 策略 + TWAP + in_app 通知）承載業務，其餘能力上線後迭代。

```
┌─ 上線前（必要）──────────────────────────────────┐
│  L1 身分/帳號/Session   L2 平台守門/防護   L3 安全/部署硬化 │
└──────────────────────── 上線線 ───────────────────────┘
┌─ 上線後（迭代）──────────────────────────────────┐
│  P1 停利/停損+OCO   P2 Outbox交付   P3 Telegram綁定+設定    │
│  P4 除息調整        P5 Admin監控+覆寫+KillSwitch   P6 保留/隱私+SLO │
└──────────────────────────────────────────────┘
```

---

## 3. 工單總表

### 上線前（3 張）

| # | 工單 | 擁有（範圍內） | 不碰（邊界外） | 依賴 | ROM |
|---|---|---|---|---|---|
| **L1** | **身分 / 帳號 / Session** | users 表 + role(user/admin)；帳號生命週期（admin 建帳號 → invitation 24h → 首登設密 → active → 停用 cascade）；password reset 30min；JWT 15min access + DB refresh rotation + reuse 偵測 + revoke（logout/reset/disable/2fa）；登入/reset 鎖定（RateLimiter token-bucket primitive）；admin TOTP 2FA；把 LOCAL_USER_ID 換成真 auth context（全 API 真 owner scoping） | 跨 endpoint 通用限流（L2）；admin 監控/覆寫/killswitch（P5）；通知通道設定（P3） | — | **L** |
| **L2** | **平台守門 / 防護** | audit_events + AuditEventWriter（§17 最低事件，refactor 既有 callers）；idempotency_keys + manager（create/cancel，24h）；request/correlation id 貫穿；建立上限 enforcement（200/標的 20，env 預設）；全 mutating endpoint 套 RateLimiter（沿用 L1 primitive）+ `Retry-After`；**最小 Kill Switch**（`system_flags` 表 + 全域停止觸發旗標 + admin toggle endpoint(2FA+必填 reason+audit) + evaluator 每輪檢查；in-process cache 30s TTL+切換時失效；旗標 on 時照抓 quote 但不產 TriggerEvent / 不發通知；止血不回放） | RateLimiter primitive 本體（L1 建）；admin 可調 config endpoint（P5，L2 先用 env 預設不被擋）；Kill Switch 完整分層（P5 擴充）；retention（P6） | L1（audit actor=user/admin + admin 2FA；L1 先用 logging stub，L2 補真表並 refactor） | **M** |
| **L3** | **安全與部署硬化** | CSRF token + Origin/Referer 檢查；cookie HttpOnly/Secure/SameSite；CORS 收斂；密碼規則 MVP(≥8)；secrets 全進 env（.env in .gitignore）；單機 EC2+RDS 部署 + PG-everywhere；process 邊界最小落地（web / evaluator / notification / jobs 不互相拖垮）；備份每日 + PITR + 一次還原演練；認證流程 E2E + 上線冒煙測試 | 業務功能測試（各 WO 自帶）；完整 SLO 量測（P6） | L1, L2 | **M** |

### 上線後（6 張）

| # | 工單 | 擁有（範圍內） | 不碰（邊界外） | 依賴 | ROM |
|---|---|---|---|---|---|
| **P1** | **停利/停損 + OCO 群組** | take_profit_alert / stop_loss_alert 策略 + evaluator 分支 + position_side→order_side 推導；TradeIntentGroup(bracket_alert) + 兩子 intent；OCO 連動取消 / 單腳取消整組；ambiguous_trigger；建立驗證（多單 tp>sl、空單 tp<sl） | 既有 8 策略（已交付）；觸發後通知交付（P2/既有） | L1（owner） | **M** |
| **P2** | **通知交付保證（Outbox）** | 將既有「直接寫 notifications 表」refactor 成 transactional outbox；NotificationDelivery 表 + 狀態機(pending/sent/failed_retryable/failed_permanent/skipped)；notification worker(claim/lock)；telegram retryable 3x backoff / permanent→revoke binding；skip reason；rendered 快照欄位 | telegram 綁定流程（P3）；通知偏好（P3） | L2（correlation id）；refactor 既有 trigger 路徑 | **M** |
| **P3** | **Telegram 綁定 + 通知設定** | bot /start /bind；bind code 10min 一次性；telegram_chat_id 1:1 綁定 + unbind（audit）；user 層通道啟用（in_app 必開、telegram 可選）；以觸發當下設定派送 | 通知交付機制（P2 擁有）；in_app 既有 | L1, P2（telegram delivery skip 用 binding 狀態） | **S–M** |
| **P4** | **除息調整（Corporate Action）** | corporate_actions 表 + provider adapter(TWSE/TPEx)；importer + normalize + import report；盤前 trading_day_adjustment_snapshot job；target_price_effective + round away from trigger；paused_data_issue(snapshot 爭議)；unsupported action→暫停 | 漲跌停 / invalid_for_day（V1 不做）；admin 覆寫 UI（P5） | L2（audit）；scheduled-jobs worker | **L** |
| **P5** | **Admin 監控 + 資料覆寫 + Kill Switch（完整版）** | admin aggregate dashboard + §18 告警；kill switch **分層擴充**（在 L2 全域旗標上加 symbol / telegram / external / corporate-action 層）；symbol master override；corporate action override；platform_config 可調 endpoint（限額等）；admin 查 user intent（reason+audit） | admin 帳號 CRUD（L1 擁有）；最小 kill switch 全域旗標（L2 已建，本票繼承勿重建）；各業務本體 | L1, L2（繼承 kill switch 旗標）, P4（CA 覆寫目標）, P2（通知失敗監控） | **M–L** |
| **P6** | **資料保留 / 隱私 + 完整 SLO** | retention job（intent 2y / delivery 2y / audit 3y / logs 90d）；帳號匿名化（email/chat id 不可逆）；完整 SLO 量測（觸發≤3s / 通知≤10s / 可用性 99.5%）；debug payload 7–30d 或 hash | — | L2, P2 | **S–M** |

> ROM = 粗估規模等級（S/M/L），非工時承諾；展開個別工單時由下而上重估。

---

## 4. 依賴圖

```
        L1 身分/帳號/Session ──┬───────────────> P3 Telegram綁定+設定
        (地基，全體 owner scoping)│              ├> P5 Admin監控+覆寫
                                │              └> P1 停利停損+OCO
        L2 守門/防護 ───────────┼> P2 Outbox ──> P3
        (L1 先 stub→L2 補真表)  ├> P4 除息 ────> P5
                                ├> P5
                                └> P6 保留/隱私
        L3 安全/部署硬化 (依 L1,L2)

        執行序：L1 → L2 → L3 →【上線】→ (P1 ‖ P2 ‖ P4) → P3 → P5 → P6
```

---

## 5. 上線前最小路徑 + 上線 caveats

**最小路徑**：L1 → L2 → L3 →【上線】。完成後平台具備：真多使用者登入、admin 建帳號、真 owner 隔離、登入/操作防護、稽核、冪等、安全部署。既有 8 策略 + TWAP + in_app 通知對真實使用者運作。

**上線時刻意延後的行為（需明確告知產品/法務）**：
- **Telegram 通知不可用**：上線時只有站內 in_app 通知；per-user telegram 派送待 P3（綁定流程）。
- **通知為 best-effort**：無 transactional outbox 交付保證，極少數情況可能漏通知，待 P2。
- **除息日到價會算錯**：無 corporate action 調整；上線初期由 admin 手動避開 / 處理除息日標的，待 P4。
- **無停利/停損/OCO 策略**：上線只有既有 8 策略（含移動出場、市價、限價、TWAP）；tp/sl/OCO 待 P1。
- **有「最小緊急停止鈕」、但無完整監控 dashboard**：L2 已含全域 kill switch（admin 一鍵停止所有觸發/通知，止血用）；但 admin 監控 dashboard、告警、以及「只停某檔/只停 Telegram」等分層 kill switch 待 P5。

### 5.1 緊急停止鈕（最小版，屬 L2）

- **行為**：admin 後台一鍵「全域停止觸發」。開啟後系統**照常抓 quote**（UI 行情/最後更新時間不受影響），但**不判定到價、不產 TriggerEvent、不發任何通知**（站內 + Telegram 皆停）。
- **操作**：限 admin（需 2FA）、**必填原因**；開/關各寫 audit（`kill_switch_enabled`/`kill_switch_disabled`，含 actor/time/reason）。
- **生效速度**：evaluator 每輪先讀旗標（in-process cache 30s TTL + 切換時主動失效）→ 數秒內生效。
- **限制（止血非重播）**：停止期間錯過的到價**不回補**；關閉後從當下最新 quote 重新評估，不回放暫停期間行情（與其他暫停/恢復語意一致）。
- **與 P5 關係**：P5 在此全域旗標上擴充分層（per-symbol / telegram-only / external-only / corporate-action），P5 繼承不重建。

---

## 6. 與舊 20 張的對應與封存

新 9 張由舊 20 張收斂而來（**20→9**，更粗）：

| 新 | 併自舊 |
|---|---|
| L1 | 01-auth-sessions、02-role-authorization、13-admin-user-management（帳號生命週期部分） |
| L2 | 16-audit-rate-limit-retention（audit+rate-limit）、+ idempotency、+ 建立上限 |
| L3 | 17-deployment-hardening、18-production-tests（auth E2E）、19-openapi-fixtures |
| P1 | 07-take-profit-stop-loss、08-oco-bracket |
| P2 | 06-outbox-notification-worker |
| P3 | 11-telegram、12-notification-settings |
| P4 | 09-corporate-action |
| P5 | 14-admin-data-overrides、15-admin-monitoring |
| P6 | 16（retention 部分）、19（privacy 部分） |

**直接刪除（06-03 收斂後 V1 不做 / 已交付，不進新工單）**：
- 03-symbol-importer → V1 用 seed，自動匯入延後。
- 04-market-calendar → 延後（V1 固定時段）。
- 05-licensed-quote → 延後（V1 留 Shioaji demo）。
- 10-csv-batch → 後端 CSV 整套移除（改純前端）。
- 20-twap-orders → 已交付（V0.5）。

封存狀態：✅ 已完成——舊 20 張 + 舊索引（`v1-backend-work-orders.legacy.md`）已移至 `docs/orders/v1/_archive/`，並附 `_archive/README.md` 註明「收斂前版本，僅供溯源」。
