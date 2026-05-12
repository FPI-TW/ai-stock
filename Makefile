.PHONY: install dev lint format format-check typecheck check migrate downgrade test test-integration

DATABASE_URL ?= postgresql+psycopg://ai_stock:ai_stock@localhost:5432/ai_stock

install:
	uv sync

dev:
	uv run uvicorn app.main:app --app-dir src --reload

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy src tests

check: lint format-check typecheck test

migrate:
	DATABASE_URL=$(DATABASE_URL) uv run alembic upgrade head

downgrade:
	DATABASE_URL=$(DATABASE_URL) uv run alembic downgrade base

test:
	uv run pytest

test-integration:
	DATABASE_URL=$(DATABASE_URL) uv run pytest -m integration
