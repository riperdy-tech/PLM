# Reconciling competing Alembic 0038 revisions — review plan

**State (updated 2026-10-27):** both 0038 branches passed isolated
PostgreSQL 16 rehearsals, and the exactly pinned historical 0038 review
revision plus no-op 0039 merge were installed into the active code graph
with owner approval. **Subsequent explicit local-development database
migration approval** allowed the connected `plm` database to be upgraded
from `0038_release_reviews` to the single `0039_merge_0038_branches`
head. A retained Git-ignored host backup was restored and independently
merged on a disposable PostgreSQL clone before the actual transaction;
source and post-merge data/schema/security fingerprints matched.
The connected local database still contains 2 reviews, 4 review events
and 8 YouTube manifests; `plm doctor` passed 16/16. The backup has **not**
been copied offsite. This **does not authorise staging or production**
deployment; their inventory, backup/recovery and approvals remain required.
The standard direct online Alembic guard continues to block non-disposable
targets absent the strictly scoped approved in-process connection. See
`docs/reviews/2026-10-27-0039-approved-local-dev-upgrade.md`, and the
still-unaccepted architecture decision draft at
`docs/proposals/adr-0038-sibling-merge-exception-draft.md`. Original
review plan written 2026-10-25.

## Observed facts (read-only)

- **Historical baseline, 2026-10-25:** local development DB was at
  `public.alembic_version = 0038_release_reviews`; the `ops` review/event
  tables already existed. **Current after 2026-10-27 authorised local
  upgrade:** one `0039_merge_0038_branches` revision row.
- The original `0038_release_reviews` migration lives in
  `.claude/worktrees/program-interface-dashboard-9c6dc4/migrations/versions/plm/plm_0038_release_reviews.py`.
  An exact SHA256-pinned reference copy for future review is in
  `docs/proposals/plm_0038_release_reviews_reference.py` (not active).
  It descends from `0037_dashboard_views` and creates those two ops tables.
  Read-only inspection verified the columns and event package index match
  the applied revision, with **two release-review rows and four event rows**
  in the development DB. These six records must not be discarded. A
  single-transaction read-only full-row SHA256 baseline for both tables is
  captured in `docs/reviews/2026-10-25-migration-preservation-baseline.md`.
  The baseline has no raw record values; recapture it at clone time if source
  data changes.
- Main checkout contains `migrations/versions/youtube/youtube_0038_candidate_selection.py`.
  It **also** descends from `0037_dashboard_views`, adds the nullable
  `youtube.publish_manifests.selection_json` column. It was **absent from
  the connected development DB before the 2026-10-27 local upgrade**, and is
  now present and nullable after that approved upgrade.
- **Historical sequence:** before code installation, `plm doctor` reported
  the database revision as foreign. After code installation but **before**
  approved DB rollout, `plm doctor` warned against migrating and
  `plm migrate` refused the pending 0039 upgrade. **Now**, after the
  separately approved local upgrade, `plm doctor` reports migrations
  **PASS at head 0039**. The normal `plm migrate` preflight continues to
  refuse any unapproved pending 0039 migration; direct online Alembic
  remains held for non-disposable targets absent the strictly scoped
  approved connection. Neither is authorisation to upgrade another DB.
- **Unversioned DB safeguard, extended 2026-10-26:** the CLI also refuses
  to migrate an absent/empty Alembic revision if *any* non-system user schema
  or a public relation, routine, user-defined type or extension remains.
  It fails closed on unreadable catalog evidence; it does not replace an
  isolation/rollback plan or prevent races before Alembic connects.
  Read-only development catalog inspection and synthetic fail-closed tests
  are documented in
  `docs/reviews/2026-10-26-unversioned-migration-preflight-guard.md`.
  **Guard consolidation (2026-10-26):** the `plm migrate` preflight and
  opt-in destructive integration round-trip now call the same
  `plm_platform.migration_safety.user_object_count` collector. Previously,
  the integration check omitted other user schemas and public routines/types,
  so its notion of an empty DB was weaker. Its explicit disposable target,
  opt-in and connected database identity checks remain mandatory. This is
  a code/READ ONLY verification, not an executed downgrade/restore; see
  `docs/reviews/2026-10-26-shared-migration-catalog-inventory.md`.
- No other environments have been checked for either sibling revision.

## Before any shared-database rollout of the code-installed merge

1. Inventory which sibling revision, if either, each environment has actually
   applied. Preserve the existing revision IDs and inspect every schema for
   partially applied or manually created structures.
2. Compare the exact operations and existing table/column/index definitions,
   not only revision labels; review any data already in the ops review tables
   and the candidate-selection column on other environments.
3. Review the **code-installed** 0039 merge as a one-time exception to the
   strictly linear ancestry rule in `migrations/README.md`. The code change
   was separately approved; the architecture ADR is still proposed and no
   shared-data migration is approved. A linear rebuild must **not** rewrite
   an already-applied revision without proving all affected environments.
4. Prepare a backup, deterministic migration diff and rollback/restore plan,
   with explicit handling for both starting states (`0038_release_reviews`
   and `0038_youtube_candidate_selection`), plus a fresh database.

## Installed code graph (DB rollout NOT approved)

`migrations/versions/plm/plm_0038_release_reviews.py` is the exact
SHA256-pinned historical revision, alongside the existing YouTube 0038
sibling. `migrations/versions/plm/plm_0039_merge_0038_branches.py`
contains the no-op Alembic merge node whose `down_revision` includes both
siblings. The active code checkout has **one 0039 head**; the two parent
migrations own their separate SQL operations. Proposal copies remain in
`docs/proposals/` as history.

`tests/unit/test_migration_reconciliation_preview.py` copies the **installed
code revisions** into a temporary inspection tree. Its SHA256 assertion pins
the historical review migration's original bytes. The tests verify both
upgrade paths to the one 0039 head and confirm that the installed merge
does not generate SQL. This is **not approval to migrate** any shared DB.
The combined offline/CLI/migration safety suite passed **63 tests, 1 skipped**
on 2026-10-25. Subsequently, exact Alembic upgrade-step testing confirmed
the `0038_release_reviews` starting state applies *only*
`0038_youtube_candidate_selection` then the `0039` merge; the other
`0038` starting state applies *only* `0038_release_reviews` then the merge.
Mock-intercepting the sibling upgrade operations confirmed the expected
two ops tables plus index vs. one nullable YouTube column, respectively,
with no SQL operations in the merge node (15 focused graph/layout tests
passed). Neither proof executed database SQL.

**2026-10-26 PostgreSQL offline DDL extension:**
`tests/unit/test_migration_reconciliation_postgresql_ddl.py` now compiles the
uninstalled sibling and merge proposal revisions using Alembic's PostgreSQL
dialect into an in-memory output buffer, without database connection. It
confirms that the missing review sibling emits two table creations and one
index creation, the missing YouTube sibling emits one **nullable** column
addition, and the 0039 merge emits no schema SQL. It also demonstrates that
either sibling's downgrade emits destructive drop statements, which must never
be used on the shared database. Six compilation tests passed, and the combined
offline graph/layout/snapshot/comparator group passed 68 tests. The 2026-10-26
READ ONLY development-DB fingerprint recapture still matches the prior two
review rows, four event rows, and eight common-column YouTube manifest rows.
Detailed digest-only evidence: `docs/reviews/2026-10-26-alembic-0038-offline-postgresql-ddl.md`.
**As of 2026-10-26, no isolated PostgreSQL migration or schema change had been run.**
An actual isolated rehearsal subsequently passed on 2026-10-27 (see updated
state and the linked review evidence). This offline DDL compilation alone
**does not prove schema/data migration success.** An actual approved
merge requires isolated PostgreSQL clones starting from each historical
branch, verification of all six existing review records, and a restore plan.
In particular, confirm how Alembic applies the missing sibling operation
and records the final single head on each historical starting state.

## Validation — isolated database only

- Use a **dedicated, disposable PostgreSQL database** with credentials and a
  name distinct from the shared `plm` database. Verify the resolved DSN and
  current database identity *before* any test that writes.
- Test from a fresh base and from two copies representing the respective
  sibling revisions. Assert both ops review tables and their existing rows
  survive; assert the new nullable YouTube column exists after migration.
  Record digest-only source and cloned-before/after snapshots with
  `tests.support.migration_review_snapshot`; use the offline fail-closed
  `tests.support.migration_review_compare` comparator to require the source
  and clone to match before upgrade, and the appropriate branch-specific
  row fingerprints and merge revision to hold afterward. **All three snapshot
  files must include `youtube_all_null_selection`, recomputed from the actual
  manifest IDs on each database**; a review-first clone's new selection digest
  must equal the independently expected all-NULL digest, not merely claim zero
  non-NULL rows. Additionally, every snapshot must contain
  `youtube_selection_column_contract`: `null` for the absent sibling column.
  **Extended 2026-10-26:** if the column exists, require exactly
  `{"type":"JSON","nullable":true,"server_default":null,"has_computed":false,"has_identity":false}`.
  The comparator rejects TEXT/JSONB/NOT NULL, defaults, generated/identity
  columns, missing evidence and numeric-boolean substitutes even when all
  row digests agree. This checks one column, not unrelated constraints.
  Recapture previous evidence using the new reader; never fabricate fields.
  See `docs/reviews/2026-10-26-0038-selection-default-generation.md`.
  **Further 2026-10-26 existing-manifest schema safeguard:** every source,
  clone-before and clone-after report also requires `youtube_manifest_schema`,
  describing **all pre-0038 publish_manifests columns** (types, nullability,
  server defaults, timestamp timezones, computed/identity properties) and its
  `manifest_id` primary key. The three contracts must match exactly;
  `selection_json` remains separately attested. This catches structural
  drift when historical row checksums still match. Existing snapshot
  evidence must be recaptured. See
  `docs/reviews/2026-10-26-0038-manifest-common-schema-preservation.md`.
  **Additional 2026-10-26 manifest index/integrity safeguard:** all three
  snapshots now also require `youtube_manifest_integrity`, containing exact
  `pg_get_indexdef` DDL and valid/ready/live catalog evidence for all six
  historical indexes (including the actual primary-key index), cross-checked
  with the five non-primary reflected names. CHECK, foreign-key and UNIQUE
  constraints must match the expected empty historical sets. Changed/missing
  index DDL or unexpected constraints reject the comparison even when the
  row digests match. This is read-only; unexpected PostgreSQL DDL formatting
  requires review; YouTube table trigger/grant evidence is covered separately below.
  Recapture all prior snapshots, see
  `docs/reviews/2026-10-26-0038-manifest-index-integrity-preservation.md`.
  **Independent historic 29-column and six-index reference (2026-10-26):**
  the comparator no longer accepts a source/clone-before/clone-after trio
  merely because all three share the same plausible but wrong manifest
  schema or index DDL. It requires exactly the 29 original columns from
  migrations 0013 and 0033 (types, NULL, defaults, timezones, generation,
  PK) and the exact PostgreSQL definitions of six historical indexes,
  including the UNIQUE idempotency and primary-key indexes. Schema changes
  or column/index omissions fail closed even for an empty manifest table.
  PostgreSQL version-dependent DDL formatting is not normalized: review
  unexpected formatting rather than force an approval. Existing JSON
  evidence still needs real recapture; see
  `docs/reviews/2026-10-26-0038-manifest-historical-reference-attestation.md`.
  **Further 2026-10-26 storage/durability safeguard:** all three reports
  now require `youtube_manifest_storage`, read from `pg_class` and
  `pg_rewrite` inside the same READ ONLY, REPEATABLE READ transaction.
  The source/clone-before/clone-after comparison rejects unlogged/temporary
  or partitioned manifest relations, nondefault replica identity and rewrite
  rules even when row fingerprints and indexes agree. The historically
  observed reference is a permanent ordinary relation (r/p), default
  replica identity (d), not a partition and zero rewrite rules.
  Old evidence must be recaptured, and this does not prove actual WAL
  recovery; see `docs/reviews/2026-10-26-0038-manifest-storage-preservation.md`.
  **Additional 2026-10-26 manifest security safeguard:** every
  source/clone-before/clone-after snapshot requires `youtube_manifest_security`.
  A READ ONLY catalog query hashes the manifest table and YouTube schema
  owners, table/schema/column ACLs and schema-local default ACLs; it also
  records RLS flags and user-defined trigger/policy counts. The comparator
  rejects changed ownership or grants, active RLS, user-defined triggers or
  policies even when row/index evidence agrees. Raw role and grant text is
  never emitted. This preserves an observed baseline, not inherited/effective
  role permissions. Existing snapshot evidence must be recaptured; see
  `docs/reviews/2026-10-26-0038-manifest-security-preservation.md`.
  **Release review persistence evidence (2026-10-26):** capture
  `release_review_storage` for both ops review tables. Each must remain
  a permanent ordinary relation with default replica identity, zero rewrite
  rules and no partition. YouTube-first before-upgrade evidence is null.
  See `docs/reviews/2026-10-26-0038-review-history-storage.md`.
  **Further 2026-10-26 review-table schema check:** capture
  `release_review_schema` with both `ops` table column/type/nullability,
  timestamp timezone flags, primary keys and named indexes. A missing pair
  yields `null` only on the YouTube-first branch. The comparator requires the
  exact historic reference definitions on the review-first branch and after
  both branches merge; it rejects dropped/wrong indexes, primary keys or
  changed column definitions even if all row hashes are unchanged. Legacy
  snapshots lacking this new field must also be recaptured. This initial
  bounded contract did not attest defaults or non-primary-key constraints.
  See `docs/reviews/2026-10-26-0038-review-table-structure-preservation.md`.
  **Additional 2026-10-26 default/constraint safeguard:** each column also
  records `server_default`, and each review table records CHECK, foreign-key
  and UNIQUE constraints. The historical 0038 reference defines no server
  defaults or such non-primary-key constraints. Source, clone-before and
  clone-after must be recaptured; unexpected defaults or constraints block the
  comparison even if all stored row fingerprints match.
  See `docs/reviews/2026-10-26-0038-review-constraints-and-defaults.md`.
  **Further 2026-10-26 PostgreSQL index safeguard:** the reader now records
  `index_definitions` from `pg_get_indexdef()` for all non-PK indexes on the
  two review tables in the same READ ONLY snapshot, cross-checking names
  against SQLAlchemy reflection and refusing invalid, unready or non-live
  indexes. The comparator requires the exact historical B-tree event index,
  including access method, key expression/order/opclasses, INCLUDE and WHERE
  options; snapshots without this field must be recaptured. PostgreSQL DDL
  formatting is version/session-sensitive and differences fail closed for
  review. This index safeguard alone does not attest triggers or ownership.
  See `docs/reviews/2026-10-26-0038-full-index-ddl-preservation.md`.
  **Additional 2026-10-26 security safeguard:** all three snapshots now
  require `release_review_security`: hashed table owner identity, default
  table ACL state, row-level-security flags and counts of user-defined
  triggers and RLS policies. Missing metadata or unexpected grants,
  ownership changes, triggers or policies blocks comparison. The two new
  review tables must have the same owner; an existing review-first owner
  must be preserved through the clone and upgrade. Recapture old evidence.
  The table-level safeguard alone does not establish inherited/schema
  privileges, column grants, service effective access or other ownership.
  See `docs/reviews/2026-10-26-0038-review-table-security-preservation.md`.
  **Additional 2026-10-26 schema/column ACL safeguard:** snapshots with
  review tables now require `release_review_access` from the same read-only
  PostgreSQL snapshot. It enforces the expected `ops` schema default ACL,
  zero schema-local default table ACL entries and zero explicit column ACLs.
  A YouTube-first snapshot without review tables must use `null` evidence.
  Unexpected privileges, missing metadata or changed clone ACL evidence
  block reconciliation. Recapture all existing snapshots; this does not
  attest inherited/global privileges or effective service-role access.
  See `docs/reviews/2026-10-26-0038-review-schema-column-acl.md`.
  The comparator rejects duplicate/nonfinite JSON evidence and inputs larger
  than 64 KB, but it does not create/authorize databases or run Alembic.
  See `docs/reviews/2026-10-25-migration-preservation-baseline.md`.
- Confirm exactly one Alembic head, no unexplained extra branch row, and
  idempotence of upgrading an already-converged clone. Review downgrade
  behavior in the disposable database only, never the shared DB.
- **0038 isolated clone READ ONLY preflight (2026-10-26):** before any
  separately approved clone migration, the new
  `python -m tests.support.migration_clone_preflight` command requires
  the exact disposable database name and a starting revision of
  `0038_release_reviews` or `0038_youtube_candidate_selection`.
  It refuses mismatched or shared `DATABASE_URL` names **before connecting**.
  With an eligible configured target, it collects READ ONLY, REPEATABLE READ
  evidence using the same settings object and checks the full historical
  branch-specific snapshot. It never changes a DB or authorizes an upgrade:
  its JSON always includes `"authorizes_migration": false`, even on pass.
  Real provenance, clone setup, three-way preservation, backup/restore and
  owner approval remain separate gates. See
  `docs/reviews/2026-10-26-0038-disposable-clone-readonly-preflight.md`.
- **Guard added:** the destructive round-trip in
  `tests/integration/test_migrations.py` now skips unless the configured
  database name matches `plm_migration_disposable_[a-z0-9_]+`, the explicit
  process environment variable `PLM_DESTRUCTIVE_MIGRATION_TEST_DB` equals
  that same database name, and PostgreSQL `SELECT current_database()`
  confirms it. It additionally requires an empty database: no existing
  PLM phase/`ops` schemas and no `public` tables (including any Alembic
  version table). An already-used disposable DB is held instead of reset.
  The ordinary shared `plm` DB is not an eligible target.
  Guard decision unit tests are synthetic; the **separate** approved
  0038 sibling-merge rehearsal on three fresh disposable databases
  passed on 2026-10-27. This does not authorize the shared-database
  migration or imply that the destructive base-round-trip test ran.
  That destructive-test guard alone does not cover direct Alembic, but
  the new `migrations/env.py` code-only hold separately refuses direct
  online Alembic commands for non-disposable targets. See
  `docs/reviews/2026-10-27-0039-direct-alembic-code-only-guard.md`.
- Only after these checks and separate owner approval should a reviewed
  schema migration be considered on shared data. The P6 archive backfill and
  publication gates remain independent.

## Out of scope / unverified

A real 0038-to-0039 migration passed on disposable PostgreSQL 16 copies
for both historical starting states on 2026-10-27. **Still unverified:**
actual YouTube-first external installations (the second test branch was
constructed on a disposable copy), other environments, persistent/off-site
backup recovery, WAL/crash recovery, an ADR permitting the temporary branch
merge to be installed, and separate production deployment approval. No
third-party account, commercial rights, music originality or production
deployment was verified.
