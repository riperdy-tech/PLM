# 0038 reconciliation: YouTube manifest index and constraint evidence

**2026-10-26 — read-only evidence hardening, no database migration.**

## Risk addressed

The three-way source / clone-before / clone-after comparator already fingerprinted existing `youtube.publish_manifests` row values and recorded the 29 common column definitions, their primary-key column, and the new `selection_json` column contract. It did **not** record the underlying PostgreSQL index definitions or reflected CHECK, foreign-key and UNIQUE constraints. Existing rows may be unchanged while a damaged index, deleted uniqueness guarantee, changed index predicate/method, or added constraint changes subsequent production behavior.

## Implemented

- `tests/support/migration_review_snapshot.py` now uses a fixed `pg_catalog.pg_index` / `pg_get_indexdef` query for **all six historical publish-manifest indexes**, including its primary-key index. It requires index names matching the historical migration, exactly one actual primary index, and `indisvalid`, `indisready`, `indislive` flags to be `true`. It cross-checks the five non-primary names against SQLAlchemy reflection. In the same existing `REPEATABLE READ, READ ONLY` transaction, it includes `get_check_constraints`, `get_foreign_keys`, and `get_unique_constraints` evidence as `youtube_manifest_integrity`.
- `tests/support/migration_review_compare.py` requires this exact-key evidence field on **all three snapshots**, the six historical index names and primary-key designation, typed index DDL, and the expected empty additional CHECK / foreign-key / UNIQUE constraint lists. It rejects missing, malformed, extra or changed definitions; a source and its clone must be identical before upgrade, and the clone's indexes/constraints must be unchanged after the proposed merge even when all existing row digests match.
- Added synthetic tests for invalid catalog readiness, duplicate/missing index definitions, reflection disagreement, changed index DDL, unexpected constraints and forged/missing clone evidence, on both historical 0038 starting branches.

## Verification

- Focused snapshot/comparator/offline PostgreSQL DDL tests: **176 passed** (exit 0).
- Connected development PostgreSQL READ ONLY snapshot: `0038_release_reviews`, **six valid historical manifest indexes** (one primary, five non-primary) and **zero reflected CHECK, foreign-key or separately declared UNIQUE constraints**. Structure and integrity contracts passed. Existing data counts remained **2 release reviews, 4 events and 8 YouTube manifests**; `selection_json` was correctly absent on this branch. No row values, secrets or credentials were printed.
- Repository-wide Ruff: **passed** (exit 0); mypy `packages services`: **463 source files passed** (exit 0).
- Full non-integration regression: **2,847 passed, 1 skipped, 80 integration tests deselected, 15 warnings** in 297.38 seconds (exit 0); destructive integration testing was not run.

## Limits and next gate

Index DDL is captured and compared verbatim; different PostgreSQL versions or formatting can intentionally fail closed and require human review. Index names/validity/primary identity are checked against the historical migration, but this does not independently authenticate the origin of the evidence or enforce a complete known-good SQL statement for every source index. This particular check also does not attest YouTube table triggers, owners, effective grants, or the complete database schema. **Old snapshot JSON must be recaptured**, never filled with invented metadata. A separately approved disposable PostgreSQL clone from each 0038 sibling is still required to verify actual migration, data preservation, single-head convergence and backup/restore. No Alembic upgrade, downgrade, stamp, DB/MinIO write, external publication or Git commit was performed.
