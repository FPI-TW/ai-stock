# BE-V1-17：EC2/RDS Deployment Readiness、Backup/Restore、Production Hardening

## Metadata

- 類型：AFK
- 優先序：P2
- 預估：20h
- 依賴：BE-V1-01, BE-V1-05, BE-V1-15
- 交付版本：V1

## 背景

V0.5 只跑本地（FastAPI + 本地 PostgreSQL）。V1 需部署 EC2 + RDS（`docs/engineering-v1-backend.md` §1）。本工單把部署 readiness 與 production hardening 補齊：

- 拆分 web / worker process 邊界（domain-spec §16）。
- Production config 與 secrets 管理。
- Health / readiness / liveness probes。
- DB backup / point-in-time recovery 設定與 restore drill。
- Logging 結構化、log shipping。
- Production grade error handling。
- Pre-flight 啟動檢查（缺 env 必 fail-fast）。

## 目標

- 拆出 worker process entry point（`app/workers/main.py`）：notification worker、scheduler、backlog poller。
- 健康檢查 endpoint 拆 `/live` / `/ready`。
- Pre-flight settings check：必要 env 缺失 → fail-fast（production）。
- Structured JSON logging（含 `request_id`、`correlation_id`）。
- Secret 從 AWS SSM Parameter Store / Secrets Manager 載入（abstraction 留可換 GCP / Vault）。
- RDS connection pool 設定（pgbouncer 或 SQLAlchemy pool）。
- 部署 manifests（systemd unit / Dockerfile / docker-compose.prod.yml）。
- 備份 / restore 流程與 runbook。
- Pre-deployment checklist（runbook）。

## 非目標

- 不做 IaC（Terraform / CDK）模版（由 ops 另外負責）。
- 不做 ECS / EKS migration（V1 採 EC2 直跑或單機 docker compose）。
- 不做 CDN / WAF（domain-spec / pm-v1-plan 未要求）。
- 不做 multi-region。

## 架構調整

### Process 邊界

| Process     | Entry point                  | 負責                                              |
| ----------- | ---------------------------- | ------------------------------------------------- |
| `web`       | `uvicorn app.main:app`       | API + telegram webhook                            |
| `worker`    | `python -m app.workers.main` | notification worker + backlog poller              |
| `scheduler` | `python -m app.scheduler`    | importer + activation / expiry / snapshot + retention |

部署上：

- web 可多 instance。
- worker 多 instance（domain-spec：notification worker 可並行，outbox SKIP LOCKED 已支援）。
- scheduler 必須單 instance（in-process APScheduler），透過 systemd `Restart=always` 守護。

### Configuration

`Settings` 擴：

- `APP_ENV` enum：`local` / `staging` / `production`。
- `LOG_FORMAT`：`json` for production。
- `SECRET_PROVIDER`：`env` / `aws_ssm`。
- 各 RDS / cache / vendor 端點。

Pre-flight check（`Settings.validate_for_env()`）：

- production：
  - `JWT_ACCESS_SECRET` 必填，且非 default sentinel。
  - `LICENSED_QUOTE_API_KEY` / `LICENSED_QUOTE_SECRET` 必填。
  - `TELEGRAM_BOT_TOKEN` 必填。
  - `DATABASE_URL` 必填且 host 不在 localhost / 私有預設值。
  - `AUTH_COOKIE_SECURE = true`。
  - `LOCAL_MODE = false`。
- 缺失 → 啟動失敗（exit code != 0）。
- staging 略放鬆但仍要求 `JWT_ACCESS_SECRET`。

### Health endpoints

- `GET /health/live`：純存活；只回 process up。
- `GET /health/ready`：DB ping + quote provider status（不要求 healthy，只要 reachable）+ outbox query ping。
- 既有 `GET /health` 保留為 deprecated alias（V0.5 用），內部呼叫 `/health/ready`。

### Logging

- Production：JSON 結構化（`logger`、`level`、`message`、`request_id`、`correlation_id`、`user_id` 若有、`event_type` 若有）。
- 不寫敏感資料（明文 token / refresh token / password / TOTP secret）。
- Log file via stdout → systemd → CloudWatch Logs / Fluent Bit（部署層）。
- `LOG_LEVEL` env 控制：production 預設 INFO，staging DEBUG。

### Database

- PgBouncer optional（v1 可先用 SQLAlchemy pool）。
- Pool size 由 env 控制：`DB_POOL_SIZE`、`DB_POOL_MAX_OVERFLOW`、`DB_POOL_RECYCLE_SECONDS`。
- 連線失效 / RDS failover：使用 `pool_pre_ping = true`。
- Migration 由部署流程 `alembic upgrade head` 跑，runbook 規範執行順序（先停 worker、跑 migration、滾 web → worker → scheduler）。

### Secrets

`SecretProvider` interface：

```python
class SecretProvider(Protocol):
    def get(self, name: str) -> str: ...
```

Implementations：

- `EnvSecretProvider`：local / staging。
- `SsmSecretProvider`：production。

Lazy load + cache，避免每 request 都打 SSM。

### Backup / Restore

RDS PostgreSQL：

- 自動 daily snapshot + 7 day retention（domain-spec §22 RPO 24h）。
- Point-in-time recovery enable（若 RDS plan 支援）。
- Manual snapshot before major migration。

Restore drill（上線前一次）：

- 從 backup 還原至 staging environment。
- 跑 smoke test：登入、建 intent、取 notification。
- 驗證 audit / outbox 資料一致性。
- 紀錄 runbook：步驟、預估時間、failure scenarios。

### Pre-deployment checklist (runbook)

`docs/runbooks/deploy-v1.md`：

1. Tag release。
2. Stop scheduler。
3. Drain worker（停止 claim 新 batch）。
4. 停 worker。
5. Run `alembic upgrade head`。
6. Deploy web（rolling，2 instance）。
7. Deploy worker。
8. Deploy scheduler。
9. Smoke test：login / 建 intent / quote evaluator dry run。
10. 確認 admin alert 無新 critical。

### Rollback

- DB migration 設計必須相容前一版本 1 step（domain-spec §22）。
- Rollback：先 web → worker → scheduler 改回前版；若 migration 不相容，須事先準備 down migration（V1 採「forward-only」風險評估，多版相容期）。

### Security headers

- `Strict-Transport-Security`（production）。
- `X-Content-Type-Options: nosniff`。
- `Referrer-Policy: same-origin`。
- `Content-Security-Policy`：對 API 較簡單；對 admin web 由前端 repo 補。
- CORS：production 限制 origin（前端 repo 域名），不允許 wildcard。

### 部署 artifacts

- `Dockerfile`（multi-stage build）。
- `docker-compose.prod.yml`（單機部署選項：web + worker + scheduler + nginx）。
- `systemd unit` template（EC2 直跑選項）。
- `Makefile` 加 `make build-image` / `make deploy-runbook-check`。

## 驗收條件

- [ ] Pre-flight `Settings.validate_for_env()` 在 production env 缺 env 時 process exit。
- [ ] `python -m app.workers.main` 與 `python -m app.scheduler` 可獨立啟動。
- [ ] `GET /health/live` 不打 DB；`GET /health/ready` 對 DB / outbox / quote provider 都檢查。
- [ ] Production JSON log 包含 `request_id`、`correlation_id`。
- [ ] 敏感資料不出現在 log（grep `password=` / `Bearer ` / `token=` 在 log fixture 為 0）。
- [ ] DB pool 在 RDS failover 後自動 recover（`pool_pre_ping` 生效）。
- [ ] Restore drill 文件存在並完成一次演練。
- [ ] CORS 在 production env 對未授權 origin 拒絕。
- [ ] Migration 透過 alembic 從 V0 → V1 完整 upgrade 不需 manual fix。

## 測試要求

- Unit：`Settings.validate_for_env()` 對缺 / 非法 env 各 case fail。
- Unit：Log formatter 對 sensitive fields filter。
- Integration：worker process 可單獨啟動並處理 outbox event。
- Integration：scheduler 啟動後在 mock 時間下執行 job。
- Integration：`/health/ready` 對 DB 斷線回 503，`/health/live` 仍 200。
- Manual：restore drill log（含時間、人員、結果）寫進 runbook。

## 工程注意事項

- Worker / scheduler / web 三者共用 same codebase + same Settings；不要拆 repo 或拆 settings 模組。
- Scheduler 必須單一 instance；多開會重複跑 import / activate / expire（雖然有 `scheduled_job_executions` 防重，但每次都失敗一筆會浪費資源）。
- 部署 runbook 要把「先 stop scheduler 再 migration」明寫，否則 scheduler 在 migration window 跑會撞 schema。
- 敏感 env 不入 Dockerfile / image；只在 runtime 注入。
- DB connection pool 對 worker 可比 web 小（worker 用 short-lived session），對 web 較大（每 request 一個 session）。
- 部署前必驗 staging 完整 happy path + ambiguous_trigger / kill switch / disabled user 等 corner case。
- BE-V1-18 production tests 用 staging 跑 contract test。
- Backup 不只是 RDS 自動 snapshot；同時要驗 manual restore 流程，否則出事才發現備份不可用。
