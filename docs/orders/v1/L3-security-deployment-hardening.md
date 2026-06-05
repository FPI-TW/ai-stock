# L3：安全與部署硬化

## Metadata

- 分層：**上線前（必要）**
- 優先序：P0
- ROM：**M**
- 依賴：L1（auth/cookie/CSRF）、L2（限流/守門）
- 交付版本：V1
- 併自舊工單：BE-V1-17-deployment-hardening、BE-V1-18-production-tests（auth E2E 部分）、BE-V1-19-openapi-fixtures

## 背景

L1/L2 把身分與守門做好後，本票負責「把網站本身鎖緊、架到正式機器、確保上得去也救得回」——對齊 `docs/domain-spec.md` §13（CORS/SameSite/CSRF/cookie/secrets）、§16（process 邊界）、§22（備份/DR 目標）、§23（測試）。這是「防護」的部署面與安全面，是上線最後一哩。

架構基準（既定）：**Postgres-everywhere、單機 EC2 + RDS（或單機 docker compose）**，不上 Redis、不上 ECS/EKS、不做 multi-region。

## 目標

- **Web 安全表面**：CSRF（驗 L1 的 double-submit + `Origin`/`Referer` 白名單）、cookie flags（HttpOnly/Secure/SameSite）、CORS 收斂到允許來源、安全 headers（HSTS、X-Content-Type-Options 等）。
- **密碼規則 MVP**：≥8 字元、不強制組合、不做弱密碼/外洩檢查（釘死 `WEAK_PASSWORD` = 長度 < 8，message 不提「強度」）。
- **Secrets 管理**：JWT secret / DB 連線 / mailer 憑證全進 env，`.env` 在 `.gitignore`；production 缺關鍵 secret fail-fast；log 不輸出 secret/PII。
- **Process 邊界最小落地**：web/API、quote evaluator、notification、scheduled-jobs 維持可獨立部署的邊界（單機同群組可，但不互相拖垮）；目前 V0.5 dev 端點（`/dev/*`）在 production 關閉或鎖權限。
- **部署**：單機 EC2 + RDS 部署腳本 / docker compose；HTTPS（反向代理 TLS）；健康檢查 endpoint 已有（沿用 `/health`）。
- **備份與 DR**：DB 每日自動備份、開啟 PITR（基礎設施支援時）、RPO 24h / RTO 4h、上線前至少一次還原演練；audit/product data 不可只存 log 系統。
- **認證流程 E2E + 上線冒煙**：invitation→login→建單→收 in_app 通知→logout 全鏈 E2E；上線冒煙清單。
- **OpenAPI / fixtures**：產出對外 API schema + 範例 fixtures，供前端對接。

## 非目標

- 不做業務功能測試（各 WO 自帶）。
- 不做完整 SLO 量測與告警（P6 / P5）。
- 不做多機水平擴展 / sharding（post-V1）。
- 不做 WAF / DDoS 等基礎設施層防護（屬 infra，非後端工單；列風險）。

## 範圍細項

### 安全表面
- CSRF：state-changing 一律驗；`Origin`/`Referer` 對允許清單；前後端跨站時明確處理 SameSite。
- CORS：allow-list 來源 + credentials；不可 `*`。
- Headers：HSTS（HTTPS 下）、`X-Content-Type-Options: nosniff`、`Referrer-Policy`、`X-Frame-Options`。
- 檔案上傳（若有）：類型/大小限制——V1 後端無 CSV 上傳，僅列原則。

### 密碼規則
- 釘死於共用 validator：`len(password) >= 8`，不足回 `WEAK_PASSWORD`(422)，message 中文不含「強度」字眼；invitation accept 與 password reset confirm 共用同 validator。

### 部署 / DR
- 部署文件：env 清單、啟動順序、TLS、reverse proxy。
- 備份：每日全量 + PITR；還原演練紀錄。
- DR 目標：RPO 24h、RTO 4h。

## 驗收條件

- [ ] state-changing 缺/錯 CSRF → 403；錯誤 `Origin` → 拒絕。
- [ ] CORS 僅允許白名單來源；非白名單被擋。
- [ ] 安全 headers 在 production 回應出現。
- [ ] production 缺關鍵 secret 啟動 fail-fast；log 不含 secret/PII。
- [ ] `/dev/*` 在 production 關閉或需特權。
- [ ] 密碼 7 字元 → `WEAK_PASSWORD`；8 字元 → 通過（invitation + reset 兩路一致）。
- [ ] 單機部署腳本可一鍵起服務（web + evaluator + notification + jobs 邊界保持）。
- [ ] DB 每日備份可驗證；完成一次還原演練並留紀錄。
- [ ] 認證 E2E：invitation→login→建單→in_app 通知→logout 全綠。
- [ ] 上線冒煙清單可執行通過；OpenAPI schema + fixtures 產出。

## 測試要求

- Integration：CSRF/CORS/headers 行為；secret 缺失 fail-fast；`/dev/*` 權限。
- E2E：認證全鏈路；冒煙清單。
- 演練：備份還原（手動 + 紀錄）。

## 工程注意事項

- 沿用 `make check` 品質門檻；E2E 與 integration 分離（integration 需 PostgreSQL，對齊 `make test-integration`）。
- TLS 終結在反向代理；後端信任 proxy headers 需明確設定。
- 部署不引入 Redis / ECS；維持單機 + RDS（對齊 Postgres-everywhere 決策）。
- DR 演練在上線前完成，結果寫入 PROJECT_HISTORY / runbook。
