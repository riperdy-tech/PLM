# Draft ADR — Preserve both 0038 sibling revisions with one explicit 0039 merge

- Status: **PROPOSED FOR OWNER REVIEW — NOT ACCEPTED**
- Prepared: 2026-10-27
- Scope: Alembic migration lineage reconciliation only; not a production deployment or remediation-stage approval
- Draft location: `docs/proposals/` intentionally, **not** an accepted `docs/adr/NNNN-*.md`
- Governing rules: `migrations/README.md` single-writer/one-chain requirement; `AGENTS.md` no unapproved remediation stage or external write
- Historical siblings: `0038_release_reviews` and `0038_youtube_candidate_selection`
- Proposed convergence: `0039_merge_0038_branches`
- Required decision: owner accepts or rejects a **one-time exception** allowing a two-parent Alembic merge node, with one final head

## Problem and observed state

Two independent development branches produced migrations based on `0037_dashboard_views`:

- The historical `0038_release_reviews` creates the two `ops` approval-history tables and event package index. The connected local development DB **was on this revision until the separately approved 2026-10-27 upgrade**, with **2 release-review and 4 event rows**. The historical source is byte-for-byte preserved in `docs/proposals/plm_0038_release_reviews_reference.py` (SHA256 `58ba6d410af2ff96ad2faf0e9e915e945df12b773677cdae4cd5b89d722b65a6`).
- The current active revision `0038_youtube_candidate_selection` adds nullable `youtube.publish_manifests.selection_json` to the historical manifest schema. The connected local development DB had **8 manifests** before the approved merge; this missing sibling was subsequently applied on 2026-10-27, preserving them.
- Originally, only `0038_youtube_candidate_selection` was the active checkout head. With separate user approval **for code installation only**, the exact historical review revision and no-op `0039` have now been installed under `migrations/versions/plm/`. The active graph has a single `0039_merge_0038_branches` head.
- The **local** development DB was subsequently upgraded, with separate explicit owner approval, to `0039_merge_0038_branches` on 2026-10-27. A full archive was retained on the development host; independent PostgreSQL restore and merged-clone data/schema/security fingerprints matched the actual migrated DB. This **does not authorise** staging/production deployments or constitute a formally accepted architecture ADR. See `docs/reviews/2026-10-27-0039-approved-local-dev-upgrade.md`.

## Proposed decision — NOT YET APPROVED

After reviewing the environment inventory and release gates below:

1. Preserve **both** existing `0038` revision IDs and their original SQL operations and ancestry. Do not change an already-applied historical revision.
2. **Code installation completed with explicit owner approval.** The SHA256-pinned historical review migration and the no-op `plm_0039_merge_0038_branches.py` are now under `migrations/versions/plm/` beside the existing YouTube sibling. The installed node has `down_revision = ("0038_release_reviews", "0038_youtube_candidate_selection")` and **no schema operations** of its own. The two parent upgrades each own their separate SQL. **This approval did not authorize migration of any database.**
3. Permit this **documented exception** to the strictly linear historical graph because it rejoins both previously-applied histories at exactly **one active head**. There must be no new divergence. All future schema changes must descend from the approved `0039` head under the existing single-writer rule.
4. Continue to refuse unknown, empty-but-not-clean or otherwise unverified database states. Apply only the missing sibling SQL on a known branch. Do not use `stamp`, an improvised schema patch, or downgrade on the shared DB.
5. **Do not deploy by accepting this draft.** Require a second explicit approval for applying the reviewed revision to each shared/staging/production environment. External publication, commercial rights and other program gates are entirely separate.

## Evidence supporting this choice

- The offline graph tests identified exactly one missing parent upgrade from either `0038` state followed by the no-op `0039` merge; the historical review source SHA256 is pinned.
- A **real isolated PostgreSQL 16 rehearsal on 2026-10-27** used an independently created container with different port, network and volume from the shared PLM PostgreSQL. A read-only custom-format backup was restored to disposable clones. From `0038_release_reviews`, the missing YouTube sibling and `0039` succeeded, preserving the current two approval reviews, four events and eight manifest records.
- From a separately reconstructed `0038_youtube_candidate_selection` state, the missing review sibling and `0039` succeeded. An existing manifest with a **synthetic, non-NULL selection** preserved its selection value. The YouTube-first source was backed up and restored to a second disposable clone before upgrading.
- Both test branches had one `0039_merge_0038_branches` revision row, passed strict three-way fingerprints for rows, schemas, indexes, security and storage metadata, and passed re-upgrade idempotence. Temporary containers and backup archives were removed.
- **At the time of this earlier isolated rehearsal**, the local DB still reported `0038_release_reviews` with 2 reviews, 4 events and 8 manifests, and no active code migration had been applied to it. **Subsequently**, the locally approved upgrade reached `0039_merge_0038_branches` with the same preserved row counts; see the separate local upgrade report.
- Focused tests **64 passed**, Ruff passed, mypy **463 files passed**, and the full non-integration suite **2,993 passed, 1 skipped, 80 deselected, 15 warnings**. This is evidence for the isolated tested paths, **not** permission to deploy.
- Reproducible report: `docs/reviews/2026-10-27-0038-isolated-postgresql-merge-rehearsal.md`.

## Preconditions to accepting and implementing the decision

**Ownership and inventory.** Name the schema owner and single migration operator. Inventory *every target environment*, each current `public.alembic_version` row, expected schema objects, custom role grants, and whether the selection column has ever been written. An environment at a different revision, partially applied state or unknown setup is **blocked** until separately investigated; do not assume the local `plm` DB represents all environments. In particular the simulated YouTube-first history does not prove the condition of any actual YouTube-first installation.

**Revision and code review.** An owner reviews the pinned original sibling source and the no-op merge revision; confirm two parents, one final head, the exact `0037` ancestry and no accidental duplicate SQL. Confirm the resulting graph and all intended consumers under the same code revision. Record the owner, date, target environments, approved migration revision IDs and source hashes in the eventual accepted ADR. Do not auto-promote this proposal into `docs/adr/`.

**Backup and independent restore.** For each eligible non-disposable target, require a dated, durable **off-target** backup with retained location, checksum, PostgreSQL version, credentials/roles/ACLs, extension requirements and restore instructions. Independently restore and verify it **before** allowing upgrade. The rehearsal's successful temporary backup restoration did **not** create a durable recovery artifact or prove WAL/crash recovery.

**Snapshot proof.** Capture fresh source, clone-before and clone-after digest-only snapshots using `tests.support.migration_review_snapshot` under READ ONLY / REPEATABLE READ. Run the fail-closed `tests.support.migration_review_compare` checker; include each table's row counts/fingerprints, all historical manifest columns and indexes, table owners/ACL/security/storage, the nullable selection field, and exact branch-specific expectations. Three-way evidence from 2026-10-26 is obsolete and must not be reused.

**Operational controls.** Choose a maintenance window; stop new writes or otherwise coordinate a reliable consistent backup/cutover point. Confirm application backward/forward compatibility and a freeze on competing revisions. The migration's target DSN and `SELECT current_database()` must match an explicit allowlisted deployment target; require a fresh human approval at the operation boundary, not merely this ADR. Never call raw Alembic blindly through the shared checkout.

**Post-upgrade acceptance.** Verify exactly one `0039_merge_0038_branches` row, correct `ops` review tables, expected `selection_json`, preserved row fingerprints and access controls; test that reapplying the same revision is idempotent. If a check fails, hold consumers and **recover from the verified backup** under a reviewed recovery procedure; do not attempt a destructive sibling downgrade or a blind Alembic stamp.

## Alternatives considered

- **Rewrite one 0038 into a linear chain.** Rejected for now: environments may have applied either sibling. Changing `down_revision` of an applied revision silently invalidates deployment histories.
- **Stamp the foreign revision or manually add/drop tables.** Rejected: erases lineage evidence and may lose review history or mask incomplete DDL.
- **Ignore the review revision and deploy only YouTube.** Rejected: the existing approval tables and six history records are real data, not disposable.
- **Do nothing indefinitely.** Preserves current DB but keeps `plm migrate` intentionally blocked and diverges source and schema. Acceptable until the required review/approval gates are satisfied.
- **Branch-merge 0039 with both parents preserved (implemented on local development).** Requires a bounded exception to the migration repo's single *linear ancestry* rule; yields a single head thereafter. Both directions were exercised on isolated PostgreSQL, and the review-first direction was separately approved and applied to the local development DB. Other environments remain unexamined.

## Consequences and failure cost

- Accepted decision would require one-time revision-graph branching (with a single `0039` head), explicit documentation of the exception and future single-writer enforcement. Tests expecting *strictly linear ancestry* must be reviewed, not silently relaxed; tests requiring one head must remain.
- Merge upgrade from review-first adds the YouTube selection column; from YouTube-first it creates the review tables and index. The no-op merge itself creates nothing. Neither update should silently overwrite historical rows.
- A wrong decision could make existing histories unreachable, drop approval records, lose candidate selections, block the application or leave active services mismatched to the DB. The remedy is a held deployment, verified recovery backup and new reviewed migration—not forced downgrade/stamp.
- **The user subsequently approved the connected local development DB upgrade, which completed successfully on 2026-10-27.** This specific approval does not automatically authorise any staging/production migration. This draft is still not a formally accepted cross-environment architecture ADR.

## Outstanding items for other environments and formal architecture acceptance

- [ ] Owner formally accepts or rejects this proposed *cross-environment* exception and assigns the official ADR number.
- [ ] Inventory every intended staging/production environment; independently verify any real YouTube-first deployment.
- [ ] Review pinned migration code, migration ownership, consumer compatibility and release plan for those environments.
- [ ] Obtain and verify off-host/offsite recovery backups and independent restoration for each production target.
- [ ] Review production write freeze, access, fresh three-way preservation and disaster recovery procedure.
- [x] **Code-only approval completed:** both 0038 revisions retained and merged into one 0039 code head.
- [x] **Connected local development upgrade separately approved and executed:** retained host backup, isolated restoration and real 0039 merge preservation verified; no staging/production rollout.
- [ ] Obtain a **new explicit environment-specific approval** before any other real target is migrated.

**The local development upgrade is complete. This proposed architecture ADR does not authorize staging/production database writes.**
