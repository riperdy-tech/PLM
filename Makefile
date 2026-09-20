# PLM monorepo tasks (documentation of intent).
# Windows hosts: use ./make.ps1, which implements the same targets.
# POSIX: point PY at an interpreter that has the project installed,
# e.g. `make test PY=.venv/bin/python`.

PY ?= python

.PHONY: up down logs test lint fmt typecheck doctor migrate

up:  ## start the local stack (postgres, minio, temporal, temporal-ui, worker)
	docker compose up -d

down:  ## stop the local stack
	docker compose down

logs:  ## follow stack logs
	docker compose logs -f

test:  ## run the test suite
	$(PY) -m pytest -q

lint:  ## lint
	$(PY) -m ruff check .

fmt:  ## format
	$(PY) -m ruff format .

typecheck:  ## strict typing for packages/
	$(PY) -m mypy

doctor:  ## Wave 0 gate via the plm CLI (same implementation as make.ps1)
	$(PY) -m plm_platform.cli doctor

migrate:  ## apply the alembic chain via the plm CLI
	$(PY) -m plm_platform.cli migrate
