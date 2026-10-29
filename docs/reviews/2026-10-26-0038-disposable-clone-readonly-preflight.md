# 0038 disposable clone: read-only preflight before isolation testing

**2026-10-26. No Alembic or database-changing operation was executed.**

## Risk and countermeasure

The proposed 0039 reconciliation remains outside the active Alembic chain. Existing three-way preservation comparison checks row/DDL/security/durability evidence **after** snapshots have been recorded, but does not provide an operator entry point that rejects an accidentally selected shared development database **before** opening a connection.

Added `tests/support/migration_clone_preflight.py`, runnable as a Python module with **two required arguments**: the exact independently provisioned disposable database name and one historical 0038 starting revision. The guard reuses `destructive_migration_name_refusal` and requires the resolved `DATABASE_URL` database name to match `plm_migration_disposable_[a-z0-9_]+` and the exact named target. Unsafe targets are rejected *before* opening a database connection. It then reuses the **same resolved settings** for the existing PostgreSQL `REPEATABLE READ, READ ONLY` snapshot collector.

The offline `preflight_clone_snapshot` checks a complete snapshot, matching connected database and exact starting revision, valid manifest column/index/security/storage evidence, valid row hashes and counts, and the branch-specific release-review or selection-column structure. For review-first, both review tables and their digest/DDL/security/storage evidence must exist and no selection column may exist. For YouTube-first, the review tables and all related evidence must be absent while the JSON selection column and its row evidence must exist.

Every result includes `"authorizes_migration": false`, including a successful preflight. This is **not** a sign-off on the clone's origin, backup, data provenance, actual migration path, branch convergence, rollback, privileges or isolation. Three separately captured source/clone-before/clone-after snapshots still require the separate comparator and operator approval.

## Operator invocation after explicit clone authorization

When an already approved, separately provisioned disposable database is selected in the process configuration (not the shared `plm` DB), run:

```text
.\.venv\Scripts\python.exe -m tests.support.migration_clone_preflight <exact-disposable-database-name> <0038_release_reviews-or-0038_youtube_candidate_selection>
```

This command is **read-only** and does not create a disposable DB or install a revision. It intentionally refuses the current shared default DB.

## Actually verified

- Focused clone-preflight, three-way comparator and snapshot unit tests: **307 passed**, exit code 0. Both historical starting states, incomplete/inconsistent evidence, wrong identities, malformed input, and CLI fail-closed behavior were exercised without a PostgreSQL connection in unit tests.
- Live command under the existing shared development configuration was **correctly refused before opening a database connection**: `passed=false`, `authorizes_migration=false`; process exit code **1 (intentional guard refusal)**. No secrets printed.
- Repository-wide Ruff: passed.
- mypy `packages services`: **463 source files**, no issues.
- Full non-integration regression: **2,984 passed, 1 skipped, 80 integration tests deselected, 15 warnings** in **293.94s**, exit code 0 (request `plm-20261026-0038-clone-preflight-regression-01`, job `9bfbe853aee08b8cd7900fad24bbbc96`). Destructive/integration tests were not run.

## Remaining gate

An actual isolated PostgreSQL database beginning at **each** historical 0038 revision, backup and tested restoration, fresh pre/post three-way digest-only evidence, real 0039 upgrade, single Alembic head, and approval/ADR are still outstanding. Do not execute migration, stamp, downgrade, shared database or MinIO write, or publication based on a preflight result.
