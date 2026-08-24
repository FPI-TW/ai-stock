# syntax=docker/dockerfile:1.7
# 正式環境映像：多階段建置，runtime 以非 root 執行。

FROM python:3.13-slim AS builder

# 從官方映像取 uv，沿用 uv.lock 做可重現安裝。
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# 先只用 lock + manifest 裝相依，最大化 layer cache。
# vendor/ 是富邦 SDK 的本地 wheel（不上 PyPI），uv.lock 的 path source 指向它，
# 不 COPY 進 build context 的話 uv sync 會找不到檔案而失敗。
COPY pyproject.toml uv.lock ./
COPY vendor ./vendor
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 再帶入專案原始碼與 alembic 設定後安裝本專案（README 為 pyproject metadata 所需）。
COPY src ./src
COPY alembic.ini README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.13-slim AS runtime

# 非 root 執行帳號。
RUN useradd --create-home --uid 10001 app_runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# shioaji SDK 在 import 時就建 log 檔，預設相對路徑會落在不可寫的 WORKDIR /app；
# 導到可寫的 /tmp，否則非 root 的 app_runtime 啟動即 PermissionError。
ENV SJ_LOG_PATH=/tmp/shioaji.log

WORKDIR /app

# 只搬必要產物：venv + 原始碼 + alembic 設定。
COPY --from=builder --chown=app_runtime:app_runtime /app/.venv /app/.venv
COPY --from=builder --chown=app_runtime:app_runtime /app/src /app/src
COPY --from=builder --chown=app_runtime:app_runtime /app/alembic.ini /app/alembic.ini

USER app_runtime

EXPOSE 8100

# 預設啟動 API；migration 由 compose 的一次性 migrate service 負責。
# 一對一服務：每個部署只服務單一使用者、負載為 I/O-bound，單 worker 的 async event
# loop 即足夠；保留 --workers 1 讓 uvicorn supervisor 在 worker crash 時自動重啟。
#
# ⚠️ 不要直接把 --workers 調大。app.main 的 lifespan 內含「券商登入 + 行情訂閱 +
# TWAP/cleanup 排程」，這些每個 worker 各跑一份：多 worker 會造成券商重複登入、互踢
# session，以及背景工作 N 倍冗餘。要橫向擴展請先改架構（拆出單一背景行程，或用 PG
# advisory lock 做 leader election），詳見 app/main.py lifespan 的註解。
CMD ["uvicorn", "app.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
