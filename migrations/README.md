# Migrations

- One alembic chain for the whole platform ("single chain"). Revision files live
  in `migrations/versions/<schema>/` — one directory per schema (P0 §4).
- Platform-wide revisions use the `plm` token instead of a phase schema (the
  baseline that creates the nine schemas is `migrations/versions/plm/`).
- Migrations are a **single-writer** resource: exactly one migration may be in
  flight across the whole repository at a time, even in a parallel wave. Never
  dispatch two migration-creating steps concurrently — the chain forks.
- A phase may only alter its own schema (`market`, `ar`, `experiments`, `music`,
  `qa`, `media`, `youtube`, `distribution`, `analytics`); cross-schema changes
  are coordinated through the seam registry, never by editing another phase's
  revision.
- Every `upgrade()` must carry a working `downgrade()`: `alembic downgrade base`
  followed by `alembic upgrade head` must round-trip.

## Revision naming

Revision files are named `<schema>_<revision>_<slug>.py`:

- `<schema>` — one of the nine phase schemas, or `plm` for platform-wide
  revisions; it must match the directory the file lives in;
- `<revision>` — the alembic revision id (short and sortable, e.g. `0002`);
- `<slug>` — a short snake_case description (e.g. `initial_tables`).

Example: `migrations/versions/market/market_0002_initial_tables.py`.
`tests/unit/test_migrations_layout.py` enforces the convention.

## Divergent migration histories — do not guess

Before a schema change, `plm doctor` must recognize the database's exact
`alembic_version` as an ancestor of the current checkout's one head. An
unknown revision is **not** an ordinary pending upgrade. The `plm migrate`
CLI performs a read-only revision preflight and refuses to call Alembic upgrade
if the database is at a revision unknown to this checkout, if the checkout has
no head, or if the preflight cannot read the database. **An absent/empty
Alembic version record is not evidence of a fresh database:** the CLI also
refuses an unversioned target with **any non-system schema** or existing
objects in `public` (tables, views, sequences, foreign tables, routines,
user-defined types or extensions). An unavailable catalog result also refuses
the migration. A verified-empty unversioned target remains eligible for the
normal baseline upgrade. These are command-level
safety checks, not transaction locks or substitutes for the release migration
review. Direct `alembic` commands bypass the CLI guard; since the
0039 code-only installation, `migrations/env.py` also refuses online
operations against non-disposable targets (including the shared `plm` DB).
Do not run `alembic stamp`, a downgrade or a manual schema patch to
make the warning disappear. First compare the revision files from all relevant
worktrees and the existing database schema, then prepare a reviewed linear or
merge migration and validate it on an isolated database copy. Applying any
migration to shared data requires its own approval and rollback plan.

Originally observed on the **local development database, 2026-10-25**
(read-only): `public.alembic_version = 0038_release_reviews`, and
`ops.release_reviews` / `ops.release_review_events` existed with real rows.
The other independently developed sibling `0038_youtube_candidate_selection`
descends from the same `0037_dashboard_views` revision and adds
`youtube.publish_manifests.selection_json`; that column was absent in the
connected development DB.

**Code-installation update (2026-10-27, approved for code ONLY):** the active
tree now includes the SHA256-pinned original
`migrations/versions/plm/plm_0038_release_reviews.py` (unchanged historical
source) and the no-op
`migrations/versions/plm/plm_0039_merge_0038_branches.py` whose parents are
both historical 0038 revisions. Alembic's **active code graph has one 0039
head**. This is a one-time convergence of already-diverged histories; future
migrations must continue from the 0039 head under the single-writer rule.
Both upgrade paths passed real PostgreSQL 16 rehearsal on *disposable*
databases, including snapshot preservation and backup/restore.

**Local development DB update (separately approved, 2026-10-27):** the
connected local `plm-postgres-1` database is now actually at **one
`0039_merge_0038_branches` row**. The update followed a retained
Git-ignored, host-side custom-format full backup, an independently
restored/merged PostgreSQL 16 clone with matching row/schema/security
fingerprints, and a bounded transactional upgrade of the missing YouTube
sibling. Fresh DB checks preserved 2 reviews, 4 review events and 8
manifests; `plm doctor` passed 16/16. See
`docs/reviews/2026-10-27-0039-approved-local-dev-upgrade.md`.
**This was NOT staging or production deployment.** The retained backup is
on the development host, not offsite; independent disaster/WAL recovery,
other environment inventory and operator rollout approval remain separate.

`plm migrate` still refuses pending 0039 migrations; the normal online
`alembic` environment still blocks direct operations against non-disposable
targets. The authorised one-time local upgrade instead passed an already
connected, source-verified SQLAlchemy connection through a narrowly scoped
in-process Alembic Config attribute; there is no general-purpose production
override. A disposable-looking name and non-default port are not proof of
isolation or permission. Guard details:
`docs/reviews/2026-10-27-0039-direct-alembic-code-only-guard.md`.
A review-only reconciliation checklist is at
`docs/runbooks/reconcile-alembic-0038.md`. The destructive round-trip in
`tests/integration/test_migrations.py` now skips on the shared DB; it requires
an explicitly opted-in `plm_migration_disposable_*` database, a matching
live `current_database()` check, and confirmation that **no non-system user
schemas or public relations, routines, types or extensions** remain. As of
2026-10-26, `plm migrate` and this destructive integration guard reuse
`plm_platform.migration_safety.user_object_count` for the *same* read-only
catalog inventory (see `docs/reviews/2026-10-26-shared-migration-catalog-inventory.md`).
Never use the shared development database for that test. The additional
`migrations/env.py` code-only hold covers ordinary direct online Alembic
invocations, but does not guard scripts using a different migration environment
or prove isolation, nor does it eliminate preflight-to-migration races.

## Pending B03 P4 costs schema — 0040 code only (2026-10-28)

The active tree has one new head `0040_p4_unpriced_costs`, descending
from merged `0039_merge_0038_branches`. This revision is **CODE ONLY**
and **not approved for the connected local DB**. That DB is still on 0039.
It changes only `music.generation_attempts` and
`music.provider_cost_records`: nullable unknown monetary fields,
explicit `money_basis`, receipt references and currency. Existing
numeric records remain unchanged with `legacy_unverified` marker;
after backfilling existing rows the default is removed, so new writes
must declare provenance. Two DB CHECK constraints also reject unlabeled
or wrongly labeled actual monetary amounts. Downgrade refuses if it
would discard unknown monetary amounts or their basis. **A real
disposable PostgreSQL 16 restore of the local 0039 DB, followed by an
active-code 0040 upgrade passed**, preserving all original 341 attempt
and 341 cost rows, historic review/YouTube fingerprints, and idempotence.
A fresh source check confirmed the shared DB is **still 0039**. This
isolated test is not a retained recovery backup or rollout approval.
A separate real-target approval, durable backup and independent restore
are still required. Normal CLI and direct Alembic holds remain in force
for shared databases. The code-dependent P4 service cannot be claimed
ready on a DB that has not received 0040; do not paper over this with
`alembic stamp`. Decision:
`docs/adr/0038-p4-unpriced-provider-costs.md`. Evidence:
`docs/reviews/2026-10-28-p4-0040-isolated-postgresql-rehearsal.md`.

## Pending B04 credit-use provenance — 0041 code only (2026-10-28)

The active **single head** is now `0041_p4_credit_provenance`,
extending the unchanged 0040 money fix. Both are **code only**:
the connected local development PostgreSQL remains at 0039.
0041 permits UNKNOWN credit usage as SQL NULL instead of false 0/10;
adds a required basis (unknown, mock_simulated, provider_reported,
legacy_unverified) and optional provider receipt reference to both P4
tables, with DB CHECK constraints enforcing safe combinations.
Old numerical credits remain byte-for-byte unchanged but labelled
`legacy_unverified`. No default is left for future writes; downgrade
refuses discarding UNKNOWN or provenance. **A true isolated PG16 restore
and upgrade 0039→0040→0041 passed** with 341+341 historical row
fingerprints preserved and checked R5 data/security, single Alembic
head and idempotent re-upgrade. The source DB was independently verified
still 0039. Separate backup, restoration and environment-specific
approval remain prerequisites for real migration; neither `plm migrate`
nor raw Alembic should bypass the hold. Decision:
`docs/adr/0039-p4-credit-unknown-provenance.md`.

## Running — schema changes require a verified disposable target or approval

`env.py` reads the connection string from `plm_platform.config` (process
environment, then `.env` at the repo root); it never reads `os.environ` and
never hardcodes a URL. Offline mode (`alembic upgrade head --sql`) is supported.

The following illustrate Alembic operations, **not instructions to run them
against the shared `plm` development database**. Always verify the resolved
target database name and migration branch before proceeding; destructive
downgrades belong exclusively to a separately created disposable test DB.

```text
alembic upgrade head      # schema-changing; only after target verification and approval
alembic downgrade base    # destructive: drops platform phase schemas; disposable DB ONLY
```

`tests/integration/test_migrations.py` exercises the real chain against the
docker compose postgres and skips with an explicit reason when it is down.
