# 0038 reconciliation: independently pinned historical YouTube schema/index references

**2026-10-26 — read-only evidence strengthening; no migration executed.**

## Previously unblocked false-pass risk

`tests.support.migration_review_compare` accepted any well-formed 1–100-column `youtube_manifest_schema` with `manifest_id` as primary key. If the same incorrect schema appeared in all three source/clone-before/clone-after snapshots, comparison passed even with historical columns missing. Similarly, all six index names and primary-key identity were checked but an identically altered index definition in the three snapshots passed. Row fingerprints, especially on empty tables, cannot establish intact historical schema or index behavior.

## Change made

- Added a fixed **29-column PostgreSQL reference** to `tests/support/migration_review_compare.py`, derived from `migrations/versions/youtube/youtube_0013_initial_tables.py` (original manifest table) and `youtube_0033_manifest_tracks.py` (`tracks_json`). It includes every name, SQL type, NULL flag, server default, timestamp timezone, generated/identity properties, and the primary key. The separately validated `selection_json` remains absent from this pre-0038 reference.
- `_valid_manifest_schema` requires exact type-sensitive JSON agreement to this fixed reference, not just source-vs-clone agreement. Missing, extra, reordered, renamed, generated, altered-default or altered-type columns and numeric/boolean type masquerading fail closed.
- Added a fixed reference for the six historic `publish_manifests` PostgreSQL `pg_get_indexdef` statements, including the primary-key and idempotency UNIQUE indexes. Index DDL is now compared against its historical expected definition as well as across snapshots. Altered key columns, access method, UNIQUE flag or predicates refuse comparison.
- Updated the synthetic test fixtures to contain all 29 historical columns and correct six index definitions rather than reduced two-column placeholder evidence. Added positive reference tests and negative cases where **all three snapshots agree on the same forged schema or DDL**, for both initial 0038 branches.

## Verified

- Focused schema/comparator/offline PostgreSQL DDL suite: **232 passed**, exit code 0.
- Actual connected development PostgreSQL under a `REPEATABLE READ, READ ONLY` snapshot: revision `0038_release_reviews`, 29 existing columns and all six indexes matched the fixed independent references (exit code 0). Eight existing manifest rows; no source rows or credentials printed.
- Repository-wide Ruff: passed (exit code 0).
- mypy `packages services`: 463 source files passed (exit code 0).
- Full non-integration regression: **2,903 passed, 1 skipped, 80 integration tests deselected, 15 warnings** in **292.58 s**, exit code 0. No destructive/integration run was performed.

## Scope and next approval gate

The full expected PostgreSQL DDL strings are intentionally strict; differences due to PostgreSQL catalog formatting or dialect/version require review rather than silent acceptance. This checks the historic column/index shape, not every object in the database. The earlier mandatory `youtube_manifest_security`, row fingerprints, review-table evidence and selection-column contract still apply.

Three-way evidence files require real recapture; old JSON evidence is not automatically migrated or fabricated. No approved isolated clone from either 0038 sibling was upgraded, no 0039 merge installed, no one-head convergence, backup/restore, destructive integration, or production behavior was tested. No shared DB/MinIO write, external publication or Git commit was performed.
