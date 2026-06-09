# P5：Admin 監控 + 資料覆寫 + Kill Switch（完整版）

## Metadata

- 分層：上線後
- 優先序：P2
- ROM：**M–L**
- 依賴：L1（admin role + 2FA）、L2（audit + 繼承最小 kill switch 全域旗標）、P4（CA 覆寫目標）、P2（通知失敗監控）
- 交付版本：V1
- 併自舊工單：BE-V1-14-admin-data-overrides、BE-V1-15-admin-monitoring

## 背景

L1 已給 admin「帳號 CRUD」、L2 已給「全域 kill switch」。本票補完整 admin 後台：監控 dashboard、分層 kill switch、資料覆寫、可調 config。對齊 `docs/domain-spec.md` §13（admin 能力）、§18（監控 + kill switch）、§9（corporate action 覆寫）、§15（可調上限）。

## 目標

- **監控 dashboard（aggregate）**：intent count、failure count、affected symbol count；admin 查 user 完整 intent 需明確 support 操作 + 輸入 reason + 寫 audit（admin id/user id/reason/time）。
- **告警**（§18 最低）：quote source stale/斷線、某 symbol quote validation 連續失敗、telegram delivery failure rate 過高、corporate action import job 失敗、snapshot 未在開盤前產生、notification worker backlog 過高、trigger worker backlog/evaluator 心跳停擺。
- **Kill switch 分層擴充**（在 L2 全域旗標上加層）：特定 symbol 停止觸發、停止 telegram、停止所有外部通知保留站內、停止 corporate action adjustment；停止觸發時 quote 仍抓取供 UI/健康但不產 TriggerEvent；全部寫 audit。
- **資料覆寫**：symbol master override（狀態/備註）、corporate action override（接 P4 command）；寫 audit `admin_override_applied`。
- **platform_config 可調 endpoint**：建立上限（200/20）等改為 admin 可調（覆蓋 L2 env 預設，注入式）。

## 非目標

- 不重建最小 kill switch 全域旗標（L2 已建，繼承擴充）。
- 不做 admin 帳號 CRUD（L1）。
- 不做 symbol master / market calendar 自動匯入（V1 用 seed，延後）。

## DB / 介面

- 擴充 `system_flags`：加 `symbol_trigger_halt:<symbol>`、`telegram_halt`、`external_channels_halt`、`corporate_action_halt` 等 key。
- `platform_config`：`config_key`、`value`、`updated_by`、`updated_at`。
- `worker_heartbeats`（若 P2 未建）：evaluator/notification 心跳，盤中停擺偵測。
- API：`GET /admin/dashboard`、`GET /admin/alerts`、`POST /admin/kill-switch/*`（分層）、`POST /admin/symbols/{symbol}/override`、`POST /admin/corporate-actions/override`、`GET/PATCH /admin/config`、`POST /admin/users/{id}/intents:view`（support 查閱，reason+audit）。

## 驗收條件

- [ ] dashboard 只顯示 aggregate；查 user intent 需 reason + audit。
- [ ] 7 類告警可觸發（含 evaluator 心跳停擺，盤外不誤報）。
- [ ] 分層 kill switch 各層獨立生效（symbol/telegram/external/corporate）；停止觸發時 quote 仍抓但不產 TriggerEvent；全寫 audit。
- [ ] symbol/corporate action override 寫 `admin_override_applied`。
- [ ] platform_config 改限額即時生效（覆蓋 L2 預設）。

## 測試要求

- Unit：分層旗標判定；告警門檻；config 覆蓋優先序。
- Integration：kill switch 各層；override audit；config 調整影響建立上限；support 查閱 audit。

## 工程注意事項

- in-process cache + invalidation（Postgres-everywhere，不引入 Redis）。
- config 注入式覆蓋 L2 env 預設，L2 不被本票阻塞。
