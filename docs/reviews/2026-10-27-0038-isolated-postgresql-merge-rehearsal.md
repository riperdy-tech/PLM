# Verified isolated PostgreSQL 0038-to-0039 merge rehearsal — 2026-10-27

**Scope:** User approved migration testing in separately created **disposable copies only**. No owner approval was given to alter the existing PLM/shared database, install the proposal into the main Alembic migration tree, or deploy any production change.

## Environment, isolation and source data

- PostgreSQL **16**, with a freshly created, independently named Docker container, distinct port bound to host loopback, default `bridge` network (not the PLM Compose network), and a data volume verified different from the shared PostgreSQL container.
- The test runner `tests/support/migration_isolated_rehearsal.py` required an explicit `--approved-isolated-test-only` argument and independently verified the source container, shared DB identity `plm`, revision `0038_release_reviews`, major server version, and source table/schema owner compatibility before any test operation.
- Original data was accessed **read-only** via PostgreSQL snapshot and `pg_dump`; the same digest-only source snapshot was checked again after the dump to reject a changed baseline. Its observed baseline: **2 release review rows, 4 release review event rows, 8 YouTube manifest rows**.
- A **custom-format source backup** of **11,438,027 bytes** was created in a temporary local directory. Temporary archives were not checked into Git or printed. Restoration was verified by strict source-versus-clone snapshot comparison, not just `pg_restore` exit status.
- The two historical 0038 revisions and proposed 0039 no-op merge were copied **only into a temporary Alembic script tree**. The active `migrations/versions/` tree was not changed.

## Real SQL results

| Starting branch | How it was constructed | Actual operations on disposable database | Preservation check | Result |
| --- | --- | --- | --- | --- |
| `0038_release_reviews` | Restored a full read-only backup of the existing shared development DB into isolated `plm_migration_disposable_0038_release` | Applied the missing YouTube sibling, then the proposal `0039_merge_0038_branches` | Source, restored clone-before and clone-after passed the typed, fail-closed three-way snapshot comparator | **PASS** |
| `0038_youtube_candidate_selection` | Restored the same shared-DB archive into **a separate disposable database**; reverted the review sibling **on that disposable copy only** to `0037_dashboard_views`, then applied the YouTube sibling. One existing manifest was given a synthetic non-NULL selection value **on the disposable source only** to check preservation of prior assignments. | Created a custom-format backup of this disposable YouTube-first state (**11,435,521 bytes**), restored it into another separately named disposable clone, then applied the missing review sibling and proposed `0039` merge | YouTube-source, restored YouTube-clone-before, and clone-after passed the typed three-way snapshot comparator, including the non-NULL selection fixture | **PASS** |

Each merged clone reached **exactly one Alembic version row**, `0039_merge_0038_branches`. Calling upgrade again at that head did not change the read-only snapshot (both branches **idempotent**). The restored backups and preservation checks passed. The test-created Docker container was stopped using `--rm`, and the temporary backup files and temporary migration tree were removed.

**Post-rehearsal read-only verification:** the shared development DB still reported `0038_release_reviews`, with **2 reviews, 4 events and 8 manifests**; no container with the unique rehearsal label remained.

## Validation and scope limits

- Actual isolated command: `python -m tests.support.migration_isolated_rehearsal --approved-isolated-test-only` — **exit 0** and `passed=true` (request `plm-20261027-0038-approved-isolated-rehearsal-01`, job `bc642190cb92e4b4b16db446f09407f6`).
- Focused rehearsal-guard/preflight/offline graph/DDL tests: **64 passed**, exit 0.
- Repository-wide Ruff: **passed**; mypy `packages services`: **463 source files, passed**. Full non-integration regression: **2,993 passed, 1 skipped, 80 integration tests deselected, 15 warnings** in **307.15 seconds**, exit code 0 (request `plm-20261027-0038-isolated-rehearsal-regression-01`, job `2c5eb235c882c2f595bdcd7d68f262e6`). Destructive integration suite not run.
- The YouTube-first scenario is a **faithful synthetic historical branch created on disposable PostgreSQL**, *not* an actual external environment that was found already running the YouTube-first revision. The non-NULL selection is synthetic test data.
- This validates **these two real PostgreSQL migration paths on the current development data and the synthetic second-branch state**. It does not prove all deployment environments are inventoried, post-deployment reliability, crash/WAL recovery, or an independent off-site production backup/restore process. The temporary archives were deleted after successful restore checks; no persistent operator backup was created.
- No deployment, `alembic stamp`, shared-DB migration/downgrade/write, MinIO write, publishing, or commit occurred. A reviewed ADR/linear-chain exception, actual environment inventory and a separately approved production rollout/rollback process remain required.
