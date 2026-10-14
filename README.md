# PLM

Autonomous music-label platform monorepo: shared contracts and platform runtime
in `packages/`, one service package per phase (P1-P9) in `services/`.

## Start

    ./make.ps1 up

That starts postgres, minio, temporal, temporal-ui and the worker container.
Other targets: `down`, `logs`, `test`, `lint`, `fmt`, `typecheck`, `doctor`,
`migrate`. First time: `python -m venv .venv`, install with
`.\.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[dev]"`
(the versions CI verifies), then copy
`.env.example` to `.env`. POSIX hosts can run `make <target>` instead.

## Status

Read [`docs/STATUS.md`](docs/STATUS.md) first. It records which capabilities are real,
mocked, stubbed or absent, and it is the source of truth over plan checkboxes and commit
messages. The phases do not yet run as one system; the staged plan to get there is
[`docs/reviews/2026-10-13-system-review-and-remediation-plan.md`](docs/reviews/2026-10-13-system-review-and-remediation-plan.md).

## Docs

The architecture and every phase plan live in `docs/`:

- `docs/STATUS.md` — verified capability per phase, plus the test baseline.
- `docs/architecture.md` — the master spec (parts I–XXVII).
- `docs/P0_master_integration_implementation_plan.md` — the master plan; the
  nine phase plans are `docs/P1_...` through `docs/P9_...`.
- `docs/contracts/` — `CONTRACT_REGISTRY.md` (the frozen cross-phase seams),
  `PHASE_MAP.md` (the three numbering systems and the ARCH↔phase map),
  `DECISION_LOG.md`.
- `docs/adr/` — the ADR template and one ADR per ruling (`R1`–`R20`).
## Where things live

Tests in `tests/`, the single alembic chain in `migrations/`, specs and plans in
`docs/`. The dashboard (`plm dashboard`) currently serves demo data only.
