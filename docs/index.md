# 專案文件

本目錄只保存仍需維護的產品、工程與操作事實。已完成、取消或被取代的設計可由 Git history 查詢，不在工作樹保留 archive。

## 導覽

| 文件 | 用途 |
| --- | --- |
| [產品定位](product.md) | 現況、產品邊界與未來演進方向 |
| [系統架構](architecture.md) | 已落地元件、資料流、持久層與外部整合 |
| [Domain 規則](domain.md) | 交易意圖、策略、狀態、價格與觸發語意 |
| [API 指南](api.md) | API 分組、認證、共通契約與主要流程 |
| [開發指南](development.md) | 本地安裝、migration、seed、測試與品質指令 |
| [維運指南](operations.md) | production 設定、CI/CD、TLS、部署與回滾 |
| [通知訊息](notifications.md) | 現行通知類型、文字與 Telegram 同步方式 |
| [已知技術債](technical-debt.md) | 已確認但尚未排程的工程缺口 |
| [工單](orders/index.md) | 已定案、尚未完成的複雜需求 |

## 權威來源

遇到不一致時，依下列順序判讀：

1. 已實作行為：程式碼、資料庫 schema／migration、測試與執行期 OpenAPI。
2. 產品方向與範圍：`product.md`。
3. 尚未實作的複雜需求：`orders/` 中仍被索引的工單。
4. 小型 bug、enhancement 與即時進度：[GitHub Issues](https://github.com/FPI-TW/ai-stock/issues)／PR。

文件不是 API schema 的複本。欄位、response 與錯誤碼的完整契約以執行期 `/openapi.json` 為準。

## 維護原則

- 主題文件只描述 current state；未實作內容只能放在產品方向、技術債或工單，並清楚標示。
- 同一事實只維護一份。其他文件以連結引用，不複製段落或另做白話版。
- 功能 PR 應同步更新受影響的主題文件；若完成一張工單，同一 PR 刪除該工單及索引項目。
- 取消、完成或被取代的工單直接刪除，不移到 archive。追溯需求使用 Git history。
- `docs/orders/` 只收需要完整背景、介面與驗收條件的工作；小型工作留在 GitHub Issues，避免雙寫狀態。
