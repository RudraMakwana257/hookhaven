.PHONY: install lint format typecheck test check up down

install:
	cd backend && pip install -e ".[dev]"

lint:
	cd backend && ruff check . && ruff format --check .

format:
	cd backend && ruff check --fix . && ruff format .

typecheck:
	cd backend && mypy app

test:
	cd backend && pytest

check: lint typecheck test

up:
	docker compose up --build

down:
	docker compose down -v
