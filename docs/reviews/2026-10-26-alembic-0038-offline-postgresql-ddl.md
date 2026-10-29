# 0038 competing siblings: PostgreSQL DDL and read-only baseline (2026-10-26)

**State:** offline DDL compilation and real connected-source read-only fingerprint verification completed. **Not approved for schema migration. No database upgrade, downgrade, stamp, clone, backup restore or destructive test occurred.**

## Existing branch conflict remains

The live *development* database is `plm`, at `0038_release_reviews`. It contains the `ops.release_reviews` and `ops.release_review_events` tables created by that sibling. The active checkout ends at the other sibling, `0038_youtube_candidate_selection`, from the same `0037_dashboard_views` parent; its nullable YouTube `selection_json` column has not been applied. The review-only draft `docs/proposals/plm_0039_merge_0038_branches.py` remains outside the active Alembic revisions. Existing branch IDs are not rewritten.

## Offline PostgreSQL compile test

New `tests/unit/test_migration_reconciliation_postgresql_ddl.py` uses Alembic's `MigrationContext.configure(dialect_name="postgresql", as_sql=True)` with only an in-memory `StringIO` output buffer. It imports the uninstalled reference review revision, the active YouTube revision and the uninstalled 0039 merge proposal. **No Engine, DSN, PostgreSQL connection, SQL execution or migration of any database is involved.**

The compiled *upgrade* statements for each missing sibling were checked to be:

- Review sibling: `CREATE TABLE ops.release_reviews`, `CREATE TABLE ops.release_review_events`, `CREATE INDEX ix_release_review_events_package ON ops.release_review_events` (three PostgreSQL DDL statements). The historical review revision source SHA256 remains pinned to `58ba6d410af2ff96ad2faf0e9e915e945df12b773677cdae4cd5b89d722b65a6`.
- YouTube sibling: `ALTER TABLE youtube.publish_manifests ADD COLUMN selection_json JSON` (one DDL statement), nullable to preserve existing rows.
- The 0039 merge draft's `upgrade()` and `downgrade()` generate **no schema SQL**. The *parent downgrades* are explicitly destructive: review-index and both review-table drops, or dropping `youtube.publish_manifests.selection_json`. They must never be used to reconcile or test on the shared database.

The test enforces the statement count/order and basic PostgreSQL type, constraints and absence of destructive *upgrade* statements. Existing graph tests separately prove Alembic would traverse only the missing sibling and the no-op 0039 merge from each 0038 starting state; that is still **not** an actual SQL migration on PostgreSQL.

## Actual source database read-only snapshot

On 2026-10-26, `python -m tests.support.migration_review_snapshot` returned exit code 0 using a single `REPEATABLE READ, READ ONLY` transaction:

| Snapshot field | Verified reading |
| --- | --- |
| Database | `plm` (shared local development; NOT a disposable test target) |
| Current Alembic revision | `0038_release_reviews` |
| `ops.release_reviews` | 2 rows; SHA256 `37d0323eac48569d50dc58e608c6d5ef3b1e2109bc0c1a855203fca6a5a9e1cc` |
| `ops.release_review_events` | 4 rows; SHA256 `2e9b174a65eb0ec2c0731fb08b8a15819057fd407dfbe73a9fac84f663fdfbef` |
| Common `youtube.publish_manifests` columns | 8 rows; SHA256 `495808fcbaf39a88490e3768eed4a68f33483111c62b0f1b465ddb197f2a2460` |
| YouTube `selection_json` | Absent |

All three counts and full-row digests **match** the prior 2026-10-25 preservation baseline. No raw table rows or credentials were copied into this document. This shows only no detected changes in the measured rows/columns between the two snapshots; it cannot establish integrity of unseen environments or guarantee backup fidelity.

## Verification and next controlled gate

- New offline PostgreSQL SQL-compilation tests: **6 passed**.
- Combined offline migration-layout, graph, digest snapshot and comparator tests: **68 passed, 0 failed**.
- Repository-wide Ruff: **passed**; mypy: **462 source files passed**. Full non-integration suite: **2,708 passed, 1 skipped, 80 integration deselected, 15 warnings** (exit code 0). Isolated PostgreSQL migration remains untested.

**Still blocked:** One approved isolated disposable PostgreSQL clone per historical 0038 starting branch, measured source-to-clone digest equality before the upgrade, PostgreSQL application of the missing sibling plus merge, post-upgrade table/column and digest conservation, revision table convergence to the single 0039 head, and tested restore/rollback. Owner approval, clone isolation and backup provenance must be verified before any schema-changing test. The active migration graph and shared database remain untouched.

This step did not make a publication, rights, operational near-copy-threshold or historical fingerprint backfill decision.
