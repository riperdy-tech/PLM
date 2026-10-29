# Alembic 0038 review-table structure preservation audit — 2026-10-26

**State: read-only schema extraction and offline evidence checks implemented; actual 0038 branch merge not run.**

## Why this check is necessary

The 0038 preservation comparator previously checked saved release-review rows and event rows by full-row digests, and also checked the newly nullable YouTube `selection_json` column. It did **not** require the two original `ops` review tables to retain their expected column definitions, primary keys or event-index structure. Consequently, a cloned schema could lose the event package index, change a primary key or alter a column's type/nullability and still report identical row digests. Empty table row fingerprints would not detect such structural drift either.

The historical reference revision is still the SHA256-pinned, **uninstalled** `docs/proposals/plm_0038_release_reviews_reference.py`; the proposed 0039 merge node remains outside the active migration graph.

## Implementation

- `tests/support/migration_review_snapshot.py` now uses SQLAlchemy's inspector in the existing `REPEATABLE READ, READ ONLY` transaction to collect `release_review_schema`. It captures exact column names, SQL type strings, nullability, timestamp time-zone flags, primary-key columns, and non-PK index names, indexed columns and unique flags for both tables. The field is `null` when **both** review tables are absent, and incomplete presence refuses the snapshot.
- `tests/support/migration_review_compare.py` now requires `release_review_schema` in all three source, clone-before and clone-after evidence files. A present schema is compared against the structural definitions of the pinned historical 0038 revision, rather than merely compared against another potentially corrupted snapshot. The comparator requires source and clone-before agreement, exact preservation on the review-first path, and creation of the expected structure on the YouTube-first path.
- The schema comparison serializes JSON canonically for scalar-type-sensitive equality. This prevents Python equality from treating a forged integer `0` as equivalent to the boolean `false` in the index uniqueness or column-nullability evidence.
- Extended `tests/unit/test_migration_review_compare.py` with negative examples for missing/wrong indexes, primary keys, column type/nullability and boolean impersonation. No operational database schema was altered.

This is a **bounded structural subset**, not a full DDL dump: it does not attest every possible database default, foreign key, check constraint, role, ownership, privilege or other table outside the two review tables. An isolated migration and restore must still verify those conditions where relevant. The comparator consumes operator-managed JSON and does not authenticate the backing database host, clone isolation or reviewer approval.

## Real connected development DB observation

A read-only live snapshot returned revision `0038_release_reviews`, **2 review rows, 4 review events and 8 YouTube manifest rows**, with their previously recorded row digests unchanged. The two present review tables reflected the expected columns, types, primary keys and the nonunique event-package index. The follow-up source-only contract check exited **0** and printed `live_schema_contract_matched=True`; no raw row values or connection secrets were logged. The YouTube sibling's `selection_json` column remains absent on this database.

## Tests and outstanding actions

The targeted review schema, comparator, snapshot and offline PostgreSQL DDL suite passed **84 tests, 0 failed** (exit 0), including the final rerun after an import-order style fix. Repository-wide Ruff **passed**; mypy **462 source files passed**; the full non-integration regression **2,739 passed, 1 skipped, 80 integration deselected, 15 warnings** (exit 0). The complete regression preceded only the import-order change; focused tests were rerun afterward.

The evidence schema has gained `release_review_schema`; older source/clone-before/clone-after reports **must be re-collected** with the new reader, not manually fabricated. Real PostgreSQL migration behavior from **both** historical 0038 branches, preservation of all six review rows under an actual branch upgrade, single revision-head convergence, backup/restore and branch-specific idempotence remain unverified.

No migration application, database/object-store write, Alembic stamp, destructive downgrade, publication or Git commit was performed.
