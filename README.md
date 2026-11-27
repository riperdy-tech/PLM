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
`.env.example` to `.env` and set a random `PLM_SERVICE_TOKEN` (the `change-me`
placeholder is refused outside CI). POSIX hosts can run `make <target>` instead.

The owner's day-to-day launcher is `PLM_Studio.vbs` in the main checkout: it starts
whatever is not running and opens the Studio (the live dashboard) at
http://127.0.0.1:8000/.

The live stack is compose project `plm`, run from the main checkout
(`C:\Users\riper\Downloads\PLM`) only. In a git worktree, set `COMPOSE_PROJECT_NAME` to
another name before any `docker compose` command; `make.ps1 up/down`, the Makefile and
the launcher refuse otherwise.

Migrations: `plm migrate` upgrades only a disposable database. The owner's local `plm`
database is upgraded with a retained, restore-verified backup:
`plm migrate --prepare-backup`, then `plm migrate --approved-backup <id>` with the
application containers stopped.

Runbooks: `docs/runbooks/` (backup and restore, stuck run, OAuth rotation, full disk).

## Status

Read [`docs/STATUS.md`](docs/STATUS.md) first. It records which capabilities are real,
mocked, stubbed or absent, and is the source of truth over plan checkboxes and
commit messages. P1–P9 previously passed a **mock-provider Compose full-chain**
gate; a fully autonomous, rights-cleared, real-provider release and economic
learning cycle has **not** passed. The staged plan is
[`docs/reviews/2026-10-13-system-review-and-remediation-plan.md`](docs/reviews/2026-10-13-system-review-and-remediation-plan.md).
The current A–B finding-by-finding remediation tracker is
[`docs/reviews/2026-10-28-audit-remediation-register.md`](docs/reviews/2026-10-28-audit-remediation-register.md).

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
## Independent Windows WAV utility

The separate [Suno WAV Studio](standalone/suno_wav_studio/README.md) desktop app
runs without the PLM stack: authenticated catalog browsing (unofficial read API),
normal authorized downloads, local media-to-WAV conversion and optional album
mastering. The locally built Windows executable is at
`standalone/suno_wav_studio/dist/SunoWavStudio.exe` (ignored by Git).
It does not generate music, bypass download allowances or upload releases.

The separate [SunoWavStudio_test](standalone/suno_wav_studio_test/README.md)
comparison utility reproduces the older private video-URL / MP4-audio-extraction
route, without altering the regular WAV app. The unofficial API and its impact
on account limits are unverified; it is only an experiment, not a production
provider or an approved R-stage gate.

## Where things live

Tests in `tests/`, the single alembic chain in `migrations/`, specs and plans in
`docs/`. The dashboard (`plm dashboard serve`, the Studio) reads the live database and
phase APIs; `plm dashboard serve --demo` serves the fixed demo sample instead.
