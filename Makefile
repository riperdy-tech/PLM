# PLM monorepo tasks (documentation of intent).
# Windows hosts: use ./make.ps1, which implements the same targets.
# POSIX: point PY at an interpreter that has the project installed,
# e.g. `make test PY=.venv/bin/python`.

PY ?= python

.PHONY: up down logs test lint fmt typecheck doctor migrate worktree-guard

# The live stack (compose project plm) belongs to the main checkout: from a git worktree
# (.git is a file there) up/down need their own COMPOSE_PROJECT_NAME.
worktree-guard:
	@if [ -f .git ] && [ "$${COMPOSE_PROJECT_NAME:-plm}" = plm ]; then \
		echo "refused: git worktree; set COMPOSE_PROJECT_NAME (not plm)"; exit 1; fi

up: worktree-guard  ## start the local stack (postgres, minio, temporal, temporal-ui, worker)
	docker compose up -d

down: worktree-guard  ## stop the local stack
	docker compose down

logs:  ## follow stack logs
	docker compose logs -f

test:  ## run the test suite
	$(PY) -m pytest -q -m "not integration"

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
