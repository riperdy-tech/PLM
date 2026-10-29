# Guard direct Alembic while 0039 is code-only — 2026-10-27

**Scope:** This task adds a fail-closed protection to the active Alembic entry point after the separately approved **code-only** installation of the 0039 merge. It does **not** approve or execute a database migration.

## Problem

The 0039 release-review/YouTube history merge has one code head. The `plm migrate` operator CLI was already holding its rollout pending an approved backup/restore procedure, but the raw `alembic` command could bypass that CLI check and open the connected shared development PostgreSQL. Installing migration source is **not** permission to apply it.

## Change

- `plm_platform.migration_safety.code_only_0039_alembic_refusal` now defines a shared, pure policy for the **raw Alembic** entry point. Once revision `0039_merge_0038_branches` is anywhere in the code's known migration graph (including when there is a later head), it refuses non-disposable database targets.
- `migrations/env.py` checks before creating an engine: the resolved URL must identify a database named exactly `plm_migration_disposable_[a-z0-9_]+`, on `localhost` or `127.0.0.1`, with a non-default TCP port in the 1024–65535 range (not 5432).
- If that pre-connection test passes, the actual connected `SELECT current_database()` must match the configured disposable database name **before** configuring Alembic or executing migration SQL. The read-only identity query's implicit transaction is rolled back before Alembic's transaction handling starts. Anything missing, unknown or mismatched refuses migration.
- The normal `plm migrate` CLI hold remains intact. Offline `alembic --sql` generation is not subject to the online connection guard. Other migration scripts that do not use this `migrations/env.py` are **not** protected.
- This safety condition is **not** proof a PostgreSQL instance is genuinely isolated. A disposable-looking name and port do not by themselves authorize migration. Use only approved, independently provisioned throwaway DBs for tests. Applying 0039 to any shared/production DB still requires the separate environment-specific approval, retained backup and restoration check.

## Tests and actual observations

- Focused no-database unit/layout tests: **50 passed**, exit code 0. They cover wrong DB names, configured/connected identity mismatch, shared/default port, remote hosts, missing metadata, revision 0040 continuation, preconnection refusal, connection-identity refusal and a mocked safe disposable path.
- Actual normal **read-only** `python -m alembic current` against the existing shared configuration: **expected refusal**, exit code 1, with `0039 is installed in code ONLY` message **before engine creation**. This command is read-only; no `alembic upgrade` or downgrade was run.
- Repository Ruff: **passed**. Mypy `packages services`: **463 files passed**.
- Full non-integration regression: **3,012 passed, 1 skipped, 80 integration tests deselected, 15 warnings** in **289.14 seconds**, exit code **0** (request `plm-20261027-0039-direct-alembic-regression-01`, job `e360cc3974a22426195359a8f03fd9f2`).
- The **updated `migrations/env.py`** passed a new real PostgreSQL 16 **disposable-only** rehearsal (request `plm-20261027-0039-direct-guard-isolated-rehearsal-01`, job `5561a138f2db043cba91ca8b5d6e9419`, **exit 0**). Release-first and reconstructed YouTube-first both converged to `0039` with a single Alembic version row, preserved row/schema/security/storage evidence, passed source and YouTube branch archive restoration, preserved a synthetic non-NULL YouTube selection, and passed idempotent re-upgrade. The test container was automatically removed.
- Post-rehearsal read-only inspection verified the shared DB remained at `0038_release_reviews`, with **2 review rows, 4 event rows and 8 YouTube manifests**; no disposable rehearsal container was listed. The source PostgreSQL was not written. This **does not** verify actual production recovery, WAL crash recovery, a real external YouTube-first installation, or environment-specific rollout approval.

**No database schema or row was changed, no shared DB/MinIO write or external publication occurred, and no Git commit was created.** The original code-only head is still 0039. This does not address offsite recovery, WAL/crash recovery, production staging/inventory or deployment approval.
