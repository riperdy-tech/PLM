# 0038 YouTube manifest persistence and rewrite safety evidence

**2026-10-26. READ ONLY catalog evidence; no isolated migration or database write.**

## Gap and implementation

Previous 0038 reconciliation snapshots attested manifest rows, 29 historical columns, six indexes, constraints, grants, ownership, triggers, and RLS. They did not detect a replacement of `youtube.publish_manifests` by an unlogged/temporary or partitioned relation, changes to replica identity, or additional PostgreSQL rewrite rules when all existing row/index fingerprints still matched.

- `tests/support/migration_review_snapshot.py`: collect mandatory `youtube_manifest_storage` from `pg_class` (`relkind`, `relpersistence`, `relreplident`, `relispartition`) and `pg_rewrite` count in the same REPEATABLE READ, READ ONLY transaction as the row digests. Reject a missing/duplicated row and unknown/malformed catalog values.
- `tests/support/migration_review_compare.py`: require exact storage evidence in source, disposable clone-before and clone-after. Historical baseline is **ordinary relation `r`**, **permanent storage `p`**, **default replica identity `d`**, **not a partition**, and **zero rewrite rules**. Reject unsafe baseline even if all three reports agree; also reject any clone or post-migration drift.
- Two unit test files: positive/negative catalog parsing and both 0038 starting branches, including unlogged/temporary, changed replica identity, partitioned table, rewrite rules, type spoofing, and missing/identically invalid evidence.

## Actually verified

- Focused snapshot/comparator/PostgreSQL offline-DDL tests: **255 passed**, exit code 0.
- All four edited Python files passed syntax validation.
- Repository-wide Ruff passed, exit code 0.
- mypy on `packages services`: **463 files** passed, exit code 0.
- Full non-integration regression: **2,926 passed, 1 skipped, 80 integration tests deselected, 15 warnings** in **293.33 s**, exit code 0. Destructive/integration tests were not run.
- Connected development PostgreSQL READ ONLY snapshot: `0038_release_reviews`, eight manifests; `relation_kind=r`, `persistence=p`, `replica_identity=d`, `is_partition=false`, `rewrite_rules=0`. The new comparator accepted the real catalog evidence, exit code 0.

## Limits and next gate

This is a bounded catalog-contract check. It is not a test of crash durability, WAL replay, a standby, backup or restore, and does not attest arbitrary PostgreSQL objects or effective role permissions. Existing source/clone-before/clone-after JSON must be **recaptured**, not fabricated or amended by hand. Both 0038 start-state upgrades, true 0039 merge, one-head convergence, and restoration require a separately approved isolated PostgreSQL setup. No Alembic upgrade/downgrade/stamp, shared DB/MinIO write, external publishing or Git commit was performed.
