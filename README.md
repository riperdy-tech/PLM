# PLM

Autonomous music-label platform monorepo: shared contracts and platform runtime
in `packages/`, one service package per phase (P1-P9) in `services/`.

## Start

    ./make.ps1 up

That starts postgres, minio, temporal, temporal-ui and the worker container.
Other targets: `down`, `logs`, `test`, `lint`, `fmt`, `typecheck`, `doctor`,
`migrate`. First time: `python -m venv .venv`, install with
`.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`, then copy
`.env.example` to `.env`. POSIX hosts can run `make <target>` instead.

## Where things live

Tests in `tests/`, the single alembic chain in `migrations/`. Specs live in
`docs/` (added by W0.10). Until then the implementation plans are in the
repo root, next to this file.
