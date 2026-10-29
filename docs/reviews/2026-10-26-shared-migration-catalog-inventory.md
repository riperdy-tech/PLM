# Shared migration target catalog inventory (read-only safety)

**2026-10-26** — Offline code changes, no migration or schema/object writes.

## Issue and change

The `plm migrate` operator CLI had a comprehensive unversioned-target check, but the explicitly opted-in destructive integration round-trip used a separate, narrower query (PLM schema names plus public tables). A database with an unrelated user schema, public routine, type, view, sequence or extension could be considered empty by that integration guard while the normal CLI refused it.

A new small runtime module, `packages/plm_platform/migration_safety.py`, centralises the **read-only** PostgreSQL catalog query in `user_object_count(connection)`. It counts all non-system schemas and the relevant relation, routine, type and extension classes in `public`; it returns only a nonnegative integer, rejecting missing, boolean, negative or noninteger catalog evidence.

Both the `plm migrate` unversioned preflight in `packages/plm_platform/cli.py` and the destructive opt-in target guard in `tests/integration/test_migrations.py` now use the **same function**, rather than two diverging definitions of an empty database. The integration path still first verifies the explicit `plm_migration_disposable_*` name/opt-in and checks `current_database()` on the connected session. The same-connection catalog query is a necessary additional gate, not permission to run the destructive round-trip.

`tests/unit/test_migration_safety.py` adds synthetic success/invalid/missing/query-error checks for the common collector. Existing CLI tests continue to test populated/unknown/unversioned targets, clean initial installation and foreign Alembic revisions without running database upgrades.

## Evidence

- Focused CLI + safety + offline graph suite after refactor: **75 passed, 1 skipped** (exit 0).
- Real connected development PostgreSQL, **SET TRANSACTION READ ONLY**: the common collector returned a valid integer and reported only that user objects are present (exit 0). No object names, rows or credentials were printed.
- Repository-wide Ruff: **passed**, exit 0.
- mypy on `packages services`: **463 source files with no issues**, exit 0.
- Whole-project non-integration regression: **2,805 passed, 1 skipped, 80 deselected, 15 warnings**, exit 0 in 295.07 seconds. The destructive integration round-trip was deliberately excluded.

## Scope and limitations

The approved destructive round-trip has **not** been executed: the active shared DB is not a disposable test target, and the migration branches remain unreconciled. Both guards still have the documented time-of-check/time-of-use gap before Alembic obtains its own connection; the query is a fixed conservative inventory, not an exhaustive security audit, isolation lock or data backup. No additional PostgreSQL objects were created/dropped, no migration/stamp/downgrade was run, and no MinIO write, publication or Git commit was performed.

A separately approved isolated clone trial from each 0038 sibling, preservation comparison, and restoration verification remain required.
