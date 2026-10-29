# Alembic 0038 reconciliation: historical proposals and decision draft

**Code installation approved and completed 2026-10-27; database rollout NOT
approved.** This directory retains the historical revision reference, original
no-op merge proposal, and an unaccepted architecture decision draft. Exact
historical `0038_release_reviews` and the equivalent no-op `0039` merge
are now installed in `migrations/versions/plm/`. The shared development
database was **not** upgraded, stamped or otherwise modified. A separate
reviewed rollout, durable backup/restore and explicit approval are required.

- `plm_0038_release_reviews_reference.py`: exact byte copy of the migration
  originally applied to the shared development database, from
  `.claude/worktrees/program-interface-dashboard-9c6dc4/migrations/versions/plm/`.
  SHA256: `58ba6d410af2ff96ad2faf0e9e915e945df12b773677cdae4cd5b89d722b65a6`.
  It preserves `0038_release_reviews` and the `ops` review tables.
- `plm_0039_merge_0038_branches.py`: review-only no-op graph merge draft
  whose parents are `0038_release_reviews` and
  `0038_youtube_candidate_selection`. This merge does not itself create
  schema; Alembic must apply the missing parent operation on each starting
  branch before reaching the shared head.
- `tests/unit/test_migration_reconciliation_preview.py` builds a temporary
  Alembic script tree from the **code-installed active revisions**.
  It checks a single head/base and **the exact two upgrade steps** from
  either historical `0038` revision: only the missing sibling operation,
  then the no-op merge, never a re-run of the applied sibling. It also
  intercepts each historical revision's Alembic upgrade calls with mocks
  (two `ops` tables and one index from the review branch; one nullable
  `youtube` column from the other), and verifies that the installed merge
  node has no SQL operations. The revision files are **installed in code**,
  not applied to any real/shared database. These tests never open PostgreSQL
  or execute SQL.

## Isolated database evidence and pending decision (2026-10-27)

- Real PostgreSQL 16 migrations from both historical 0038 starting states
  **passed** on disposable clones, including custom-format backup/restore,
  existing row/DDL/security/storage fingerprints, preservation of a synthetic
  non-NULL YouTube selection, a single final `0039` row and re-upgrade
  idempotence. The shared database remains on `0038_release_reviews`.
  Detailed evidence:
  `docs/reviews/2026-10-27-0038-isolated-postgresql-merge-rehearsal.md`.
- `adr-0038-sibling-merge-exception-draft.md` is a **proposed, unaccepted**
  one-time exception to strictly linear ancestry. It documents the candidate
  decision, alternatives, owner acceptance gates, environment inventory,
  retained off-target recovery backup and **separate approval for applying**
  any migration to a real database; code installation was already approved.
  The draft remains outside accepted `docs/adr/` decisions.

**Limits:** Offline graph tests alone cannot prove real SQL, and the successful
isolated rehearsal cannot establish the state of every environment, production
backup durability or crash recovery. The YouTube-first branch was reproduced
on a disposable clone, not observed on an independently deployed environment.
`0039` is installed in the active code graph. **With new explicit owner
approval, the connected local development `plm` database was successfully
upgraded on 2026-10-27 to exactly `0039_merge_0038_branches`**, after
retaining a host-side archive and verifying separate PostgreSQL restoration,
clone upgrade and preservation. `plm doctor` passed 16/16 afterwards.
The normal `plm migrate` and direct-online-Alembic guards remain in place
for unapproved pending migrations; the local upgrade used a narrowly scoped,
already verified in-process SQLAlchemy connection. This is **not** staging or
production deployment, offsite disaster recovery, or approval for any other
database. See `docs/reviews/2026-10-27-0039-approved-local-dev-upgrade.md`
and `docs/runbooks/reconcile-alembic-0038.md`.
