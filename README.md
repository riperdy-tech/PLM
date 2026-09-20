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
## Docs

The architecture and every phase plan live in `docs/`:

- `docs/architecture.md` — the master spec (parts I–XXVII).
- `docs/P0_master_integration_implementation_plan.md` — the master plan; the
  nine phase plans are `docs/P1_...` through `docs/P9_...`.
- `docs/contracts/` — `CONTRACT_REGISTRY.md` (the frozen cross-phase seams),
  `PHASE_MAP.md` (the three numbering systems and the ARCH↔phase map),
  `DECISION_LOG.md`.
- `docs/adr/` — the ADR template and one ADR per ruling (`R1`–`R20`).
## Where things live

Tests in `tests/`, the single alembic chain in `migrations/`. Specs live in
`docs/` (added by W0.10). Until then the implementation plans are in the
repo root, next to this file.
