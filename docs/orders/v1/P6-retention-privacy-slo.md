# P6：資料保留 / 隱私 + 完整 SLO

## Metadata

- 分層：上線後
- 優先序：P3
- ROM：**S–M**
- 依賴：L2（audit 表）、P2（delivery 表）
- 交付版本：V1
- 併自舊工單：BE-V1-16（retention 部分）、BE-V1-19（privacy 部分）

## 背景

收尾票：把資料保留、帳號匿名化、完整 SLO 量測補上。對齊 `docs/domain-spec.md` §19（資料保留與隱私）、§22（SLO）。上線初期可暫缺，待資料量累積與營運需求再做。

## 目標

- **保留政策 job**：TradeIntent history 2 年、NotificationDelivery 2 年、AuditEvent 3 年、Technical logs 90 天、debug raw payload 含敏感資料 7–30 天或只存 hash/reference；逾期清理 job（idempotent）。
- **帳號刪除 / 匿名化**：帳號可停用（L1 已做）；個資欄位（email、telegram chat id）可匿名化為不可逆識別；TradeIntent/NotificationDelivery/AuditEvent 保留，user reference 改匿名 id。
- **完整 SLO 量測**：quote 每 1–5s/symbol 評估、觸發延遲 ≤3s、通知延遲 ≤10s、交易時段核心監控可用性 99.5%、除息 snapshot 開盤前完成；量測 + 報表（告警接 P5）。
- **核心資料原則**（§17 line 1070）：避免通用 `deleted_at` 表語意（用明確狀態 + audit event）；audit/product data 不可只存在 log 系統。

## 非目標

- 不做 KYC（V1 不接券商、不下單、不收付款）。
- 不做告警通道本身（P5 擁有），本票提供 SLO 量測數據。

## DB / 介面

- 各表加保留清理 job（scheduled-jobs worker）；匿名化 command `AnonymizeUserAccount`。
- SLO 量測：trigger latency / notification latency / evaluation interval 指標收集。

## 驗收條件

- [ ] 各類資料逾保留期被清理（job idempotent，可重跑）。
- [ ] 帳號匿名化後 email/chat id 不可逆，交易/通知/audit 仍保留且 user reference 匿名。
- [ ] SLO 指標可量測並產報表；超標數據可供 P5 告警。
- [ ] 全 repo 無通用 `deleted_at` 語意（用 status + audit）。

## 測試要求

- Unit：保留期判定；匿名化不可逆。
- Integration：清理 job 邊界（剛好到期/未到期）；匿名化後查詢一致性；SLO 指標收集。

## 工程注意事項

- 匿名化不可逆，需與法務確認欄位清單。
- 清理 job 與 audit 保留期不同步（audit 3 年 > intent 2 年），分別處理。
