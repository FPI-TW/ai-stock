# 富邦 Neo SDK 參考

本目錄保存富邦 Neo SDK 的上游文件快照與本專案實測結果，作為尚未實作功能的設計參考，不是本專案 API 契約的權威來源。富邦說明若有更新，以[富邦官方 SDK 文件](https://www.fbs.com.tw/TradeAPI/docs/download/download-sdk/)為準。

## 檔案

- [`fubon-llms.txt`](fubon-llms.txt)：富邦官方提供的精簡 LLM 索引。
- [`fubon-llms-full.txt`](fubon-llms-full.txt)：富邦官方提供的完整 LLM 文件。
- [`fubon-neo-verified-behavior.md`](fubon-neo-verified-behavior.md)：本專案在富邦測試環境驗證的行為與已知限制。

## 快照資訊

- 取得日期：2026-09-09。
- 官方文件當時列出的最新 SDK：v2.3.0。
- 本專案已驗證並鎖定的 SDK：v2.2.9。

SDK 升版時，除了依 [README](../../../README.md#富邦-neo-sdk-wheel) 更新 wheel 與 lockfile，也要從富邦官方重新取得這兩份 LLM 文件，更新上述快照日期與版本，並重新確認實測結論是否仍適用。
