# AI Stock Backend

Local FastAPI backend for the V0.5 trading intent and price alert workflow.

V0.5 is local mode only. Do not deploy this configuration publicly.

## Requirements

- Python 3.13
- uv
- Docker, for local PostgreSQL integration tests

## Setup

```bash
cp .env.example .env
make install
```

## Run Locally

Start PostgreSQL:

```bash
docker compose up -d postgres
```

Start the API:

```bash
make dev
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## Quality Commands

```bash
make lint
make format-check
make test
```

Integration tests require local PostgreSQL:

```bash
docker compose up -d postgres
make test-integration
```

`make test-integration` injects the local compose `DATABASE_URL`.

Equivalent uv commands:

```bash
uv sync
uv run uvicorn app.main:app --app-dir src --reload
uv run ruff check .
uv run ruff format --check .
uv run pytest
DATABASE_URL=postgresql+psycopg://ai_stock:ai_stock@localhost:5432/ai_stock uv run pytest -m integration
```

## Environment Variables

- `APP_ENV`, default `local`
- `APP_NAME`, default `ai-stock-api`
- `APP_VERSION`, default `0.5.0`
- `DATABASE_URL`, local example in `.env.example`
- `LOCAL_USER_ID`, default `local-user`
- `LOCAL_MODE`, default `true`
- `REQUEST_ID_HEADER`, default `X-Request-Id`
