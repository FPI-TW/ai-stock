# AI Stock

AI Stock 是台股與台股 ETF 的事件驅動盯盤後端。目前版本讓使用者建立交易意圖，系統依近即時行情判斷條件並產生站內／Telegram 通知；**不會送出券商委託**。

目前已支援：

- 買進／賣出到價提醒、限價／市價意圖、移動出場與 TWAP。
- FastAPI API、PostgreSQL 持久層、帳號／session、admin 2FA 與 owner scope。
- Shioaji demo 行情、單一 Telegram 群組文字建單、站內通知與選配 Telegram 同步。
- 全域 kill switch、建立上限、操作限流、冪等與 request ID。
- Docker Compose + Nginx + GitHub Actions 部署至單機 EC2，資料庫使用 RDS／Aurora。

## 快速開始

環境需求：Python 3.13、[uv](https://docs.astral.sh/uv/) 與 Docker。

```bash
cp .env.example .env
make install
docker compose up -d postgres
make migrate
make seed
make bootstrap-admin
make dev
```

API 預設位於 `http://127.0.0.1:8100`，健康檢查為 `GET /health`，互動式 OpenAPI 文件為 `/docs`。

常用品質指令：

```bash
make check
make test-integration
```

完整安裝、資料庫與 admin 操作見 [開發指南](docs/development.md)。

## 文件

- [文件索引與維護規則](docs/index.md)
- [產品定位與演進方向](docs/product.md)
- [系統架構](docs/architecture.md)
- [Domain 規則](docs/domain.md)
- [API 使用方式](docs/api.md)
- [正式環境維運](docs/operations.md)
- [目前工單](docs/orders/index.md)

專案協作與開發規則集中在 [AGENTS.md](AGENTS.md)。
