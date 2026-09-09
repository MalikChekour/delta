.PHONY: install dev test lint doctor run chat docker

install:
	python3 -m venv .venv && .venv/bin/pip install -e .

dev:
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/ruff check hermes tests

doctor:
	.venv/bin/hermes doctor

run:
	.venv/bin/hermes run

chat:
	.venv/bin/hermes chat

docker:
	docker compose up -d --build && docker compose logs -f
