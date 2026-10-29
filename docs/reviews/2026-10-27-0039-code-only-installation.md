# Alembic 0039 code-only installation — 2026-10-27

**Authorization scope:** The user expressly approved adding the tested 0039 merge revision to the **active PLM project code only**. Database migration, `alembic stamp`, real/shared DB writes, Docker deployment, Git commit/push and provider publication were **not** approved.

## Installed code and protection

- `migrations/versions/plm/plm_0038_release_reviews.py` — installed historical migration with the **exact original byte sequence** (SHA256 `58ba6d410af2ff96ad2faf0e9e915e945df12b773677cdae4cd5b89d722b65a6`), independently checked against `docs/proposals/plm_0038_release_reviews_reference.py`. Original revision ID, parent and DDL were not changed.
- `migrations/versions/plm/plm_0039_merge_0038_branches.py` — code-installed **no-op graph merge** with parents `0038_release_reviews` and `0038_youtube_candidate_selection`; no CREATE, DROP, ALTER or data SQL. Original `0038_youtube_candidate_selection` remains untouched.
- Alembic's **active code graph has exactly one head** (`0039_merge_0038_branches`), one base (`0001_baseline`), both 0038 parents, and exact upgrade paths through only the missing sibling and then the merge. This code graph is not the shared database state.
- `packages/plm_platform/cli.py` now deliberately **holds all pending migrations while the installed code head is 0039**. `plm doctor` reports a known-but-unapproved pending merge without suggesting `plm migrate`; `plm migrate` runs the usual read-only preflight and **refuses** before calling Alembic. This hold prevents the code-only approval from silently enabling shared DB writes. **Raw `alembic` bypasses the CLI hold** and remains unapproved on shared data.
- Tests inspect **installed** revisions, pin the historical source bytes, compile the actual installed merge to empty offline PostgreSQL DDL, and check that `plm migrate` refuses pending 0039. The isolated rehearsal tool now uses the installed revision tree rather than overwriting its temporary copy with proposal files.

## Verified

- The previously approved independent PostgreSQL 16 **disposable-copy rehearsal** succeeded from both historical starting states; report: `docs/reviews/2026-10-27-0038-isolated-postgresql-merge-rehearsal.md`. That previous test was before code installation, but its parent revision SQL and equivalent no-op merge operations are preserved in the installed graph.
- Focused installed graph, PostgreSQL DDL, migration-layout, CLI and rehearsal guard tests: **73 passed, 1 skipped** (exit code 0).
- Repository Ruff `check .`: **passed** (exit code 0). Mypy `packages services`: **463 source files passed** (exit code 0).
- The real connected development DB underwent **read-only** `_migration_preflight` and refused the pending 0039 rollout (expected exit code 1; no Alembic upgrade call). It was not migrated.
- Full non-integration regression: **2,995 passed, 1 skipped, 80 integration tests deselected, 15 warnings** in **384.75 seconds**, exit code **0** (request `plm-20261027-0039-code-installed-regression-01`, job `a73f129558c43da7c1a0e8d34476ee08`). Destructive/integration tests were not run.

## Not verified or approved

No 0039 revision has been applied to the connected development database or production. No new real PostgreSQL isolated migration after code installation was run; the earlier successful disposable rehearsal remains the relevant real SQL evidence. No retained off-site restore backup, WAL/crash recovery test, inventory of every deployment, accepted one-time ADR, production compatibility review, or environment-specific rollout/rollback authorization was completed. The code-only migration hold must be removed or replaced **only as part of separately approved deployment work**; direct Alembic commands bypass it.

The original existing data, including the two approval reviews, four events and eight YouTube manifests recorded in the isolated rehearsal, was not written in this code-installation task. No shared DB / MinIO writes, external publication or Git commit occurred.
