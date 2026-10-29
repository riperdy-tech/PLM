# 0038 review-table index definition preservation (READ ONLY)

**2026-10-26:** Offline evidence contract improvement. No migration, clone, stamp, rollback, DB/MinIO write or publication.

## Previously unverified risk

The 0038 evidence comparator checked index name, columns and uniqueness but not its PostgreSQL access method, expression keys, predicates, INCLUDE columns, operator classes, collations or sort order. A partial or differently implemented event lookup index could retain the same reported columns and row digests while changing query behaviour.

## Change

- `tests/support/migration_review_snapshot.py` now uses fixed read-only `pg_catalog.pg_index` queries and `pg_get_indexdef()` inside the existing REPEATABLE READ, READ ONLY snapshot to capture full DDL of every non-primary-key index on the two 0038 ops review tables. PostgreSQL `indisvalid`, `indisready` and `indislive` must all be true or collection fails closed.
- `review_schema_contract` requires the catalog index-name set to agree with SQLAlchemy's reflected index names; missing, extra or duplicated definitions fail closed.
- `tests/support/migration_review_compare.py` now requires exact expected historical DDL: no non-PK indexes on `ops.release_reviews`, one ordinary B-tree index `ix_release_review_events_package` on `ops.release_review_events(package_id)`. Defective source/before metadata is refused even when all row fingerprints agree.
- Extended `tests/unit/test_migration_review_snapshot.py` and `tests/unit/test_migration_review_compare.py` to reject unknown, duplicate, invalid or unready index evidence, catalog/reflection mismatches and changes to index method, DESC order, INCLUDE, WHERE predicate, expression and opclass.

## Evidence

- Focused snapshot/comparator/offline PostgreSQL DDL tests: **98 passed, 0 failed** (exit 0) after fixing a synthetic test setup error.
- Full non-integration regression after the final style correction: **2,753 passed, 1 skipped, 80 deselected, 15 warnings**, exit 0 (292.12s).
- Repository-wide Ruff: **passed**, exit 0; mypy `packages services`: **462 source files passed**, exit 0; changed-file `git diff --check`: **passed**, exit 0. The broader worktree previously had two unrelated trailing blank-line warnings, which this change did not modify.
- Connected development PostgreSQL (READ ONLY): `live_schema_contract_matched=True`, exit 0, revision `0038_release_reviews`, 2 review rows, 4 events and 8 YouTube manifests. No raw rows or credentials were printed.

## Boundaries

The proposed 0039 merge remains uninstalled. An isolated PostgreSQL clone, actual branch upgrade, single-head convergence, backup/restore drill, external publication and complete privilege/trigger/ownership audit have not been performed. PostgreSQL-generated `pg_get_indexdef()` strings can depend on server version or session settings; differences intentionally fail closed and require human review. Old evidence JSON must be recaptured with new `index_definitions` fields, never fabricated. The comparator is not an independent authentication of DB identity or permission to migrate.
