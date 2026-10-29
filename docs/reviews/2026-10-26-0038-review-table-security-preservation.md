# 0038 review-table security and ownership evidence (READ ONLY)

**2026-10-26.** This step improves the offline, fail-closed evidence checker. It did not run Alembic, create a clone, change database objects or approve a release.

## Previously unverified gap

The existing release-review row fingerprints and schema inspection did not detect ownership transfers, unexpected explicit table ACL grants, user-defined triggers or enabled row-level security (RLS). Those alterations can change behavior without changing existing records, columns or indexes.

## Implemented safeguards

- `tests/support/migration_review_snapshot.py` collects `release_review_security` in the same PostgreSQL `REPEATABLE READ, READ ONLY` transaction as the review row and index snapshots. A fixed catalog query obtains table owner, whether the table ACL is the default NULL ACL, RLS flags and counts of non-internal triggers and RLS policies. It never emits the raw role name; it stores its SHA256 digest. Missing, duplicate, malformed or incomplete two-table catalog evidence is rejected.
- `tests/support/migration_review_compare.py` requires this new field in each report. For both historical review tables it requires an exact six-key typed contract, the same owner digest across the two tables, no explicit table ACL, no enabled/forced RLS, no non-internal triggers and no RLS policies. It rejects missing facts and integer/bool substitution. Review-first source, pre-clone and merged clone owner/security evidence must be identical; YouTube-first merged clone must satisfy the safe new-table contract.
- Offline unit tests exercise successful and malformed catalog collection, both historical starting branches, changed ownership, abnormal grants, trigger count, RLS/policies and missing evidence. Fixtures are synthetic tests, not claims about a database migration.

## Evidence and limitations

- Concentrated snapshot/comparator/offline PostgreSQL DDL suite: **118 passed, 0 failed**, exit 0.
- Actual connected development PostgreSQL, READ ONLY: `0038_release_reviews`; `schema_valid=True`, `security_valid=True`; 2 existing review rows, 4 review events, 8 YouTube manifests. No owner name, credentials or row contents were printed.
- Whole-project non-integration regression: **2,773 passed, 1 skipped, 80 integration tests deselected, 15 warnings** in 291.61s (exit 0).
- Whole-repository Ruff: **passed**, exit 0; mypy on `packages services`: **462 source files passed**, exit 0; changed-file `git diff --check`: **passed**, exit 0. The previous whole-worktree whitespace warnings in unrelated existing files were not modified.

The historical migration creates tables without explicit grants, triggers or RLS policies. This evidence intentionally rejects unreviewed deviations, but it does **not** independently authenticate a disposable clone; it is not proof of all permissions. Schema grants, inherited role membership, default privileges for future objects, column grants, triggers elsewhere, security-definer function implementations, ownership of unrelated objects and effective access for each service are not fully attested. A cross-environment clone restored under a differently named owner will fail closed and requires operator review. The owner digest is a comparison fingerprint, not a secret-protecting authentication token. Old snapshot JSON must be recaptured using the new required `release_review_security` field; never fabricate or backfill historical evidence.

Actual upgrades from both 0038 sibling revisions to proposed 0039, single-head convergence, record preservation on a disposable database, and backup/restore verification remain **unperformed** and require separately approved isolation. No DB/MinIO writes or external publication occurred.
