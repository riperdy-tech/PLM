# 0038 reconciliation: YouTube manifest security preservation

**2026-10-26 — read-only evidence check. No schema-changing migration was run.**

## Risk and change

Previous source/clone-before/clone-after evidence covered manifest row digests, all 29 existing columns, six indexes, constraints and the optional selection column. It did not detect a change in `youtube.publish_manifests` ownership, grants, user-defined triggers or row-level security that preserves every row and index.

- `tests/support/migration_review_snapshot.py` now adds mandatory `youtube_manifest_security` from PostgreSQL catalogs inside the same `REPEATABLE READ, READ ONLY` transaction.
- Record SHA256 digests of manifest table owner, `youtube` schema owner, table ACL, schema ACL, explicit column ACL, and schema-local default ACL. Missing ACL values remain `null` and are distinguishable from present grants. Role names and ACL strings are **not emitted**.
- Record boolean RLS enable/force state, user-defined (non-internal) trigger count, and RLS policy count. Reject missing/duplicated catalog rows and invalid or incomplete metadata.
- `tests/support/migration_review_compare.py` requires exact typed security evidence on all three snapshots. It requires inactive RLS, zero user-defined triggers and zero policies, and compares all ownership and ACL hashes source → clone-before → clone-after. A changed permission that retains the same number of ACL entries is detected.
- Tests exercise both sibling starting branches, altered owner/grant digests, triggers, RLS, forged clone evidence, and invalid catalog values.

## Verified

- New focused snapshot/comparison/offline PostgreSQL DDL suite: **203 passed**, exit code 0.
- Python syntax validation: all four edited Python files passed.
- Repository-wide Ruff: passed, exit code 0.
- mypy `packages services`: **463 source files** without issues, exit code 0.
- Full non-integration regression: **2,874 passed, 1 skipped, 80 integration tests deselected, 15 warnings** in **307.34 s**, exit code 0. Destructive/integration tests were not run.
- Live connected PostgreSQL **READ ONLY** check: `0038_release_reviews`, **2 release-review rows, 4 events, 8 YouTube publish manifests**, and manifest security **0 user triggers, 0 policies, RLS disabled**. Exit code 0. No raw rows, roles, grants or credentials were printed.

## Limits / next gate

This is a baseline-preservation check, not a proof that the source database's effective permissions are correct. Grant hashes detect changes but do not derive inherited memberships, effective privileges for application roles, superuser behavior or external policy; PostgreSQL representation changes may intentionally fail closed. Internal triggers and unrelated objects are not attested here. Old three-way snapshot JSON is incompatible and must be recollected from actual source and approved disposable clones.

An approved **isolated PostgreSQL clone per sibling branch** is still necessary for the actual missing-sibling migration, verified single Alembic head, pre/post data preservation, and backup/restore test. No Alembic upgrade/downgrade/stamp, shared DB/MinIO write, release or Git commit was performed.
