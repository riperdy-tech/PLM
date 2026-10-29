# Approved local development PLM database upgrade to Alembic 0039 — 2026-10-27

**Scope of approval:** User explicitly approved proceeding after the earlier 0039 **code-only** installation and verified disposable PostgreSQL rehearsals. This execution was limited to the **connected local development** PostgreSQL 16 cluster (`plm-postgres-1`, `127.0.0.1:5432/plm`, `ENV=local`). No staging/production database, MinIO data, YouTube provider, distribution platform, or unrelated environment was changed. Approval does not waive the project's independent rollout gates for other environments.

## Preflight and backup

- The original database was positively identified using the configured URL **and** a direct PostgreSQL `current_database()` read: database `plm`, server PostgreSQL 16.15, starting revision `0038_release_reviews`; available LOGIN role `plm`. The Docker container name, official postgres:16 image and loopback port were independently checked. No other environment was found/claimed to have been inventoried.
- The original historical review migration file's SHA256 was checked against the pinned applied revision `58ba6d410af2ff96ad2faf0e9e915e945df12b773677cdae4cd5b89d722b65a6`.
- A **full PostgreSQL custom-format pg_dump** (not `--no-owner`/`--no-acl`, so database object owner/grant information remains in the archive) was saved in the Git-ignored local host folder:

  `data/migration_backups/0039_local_plm_20261027T141743Z_c3e08a9cd61f/source.pgdump`

  Archive size: **11,438,027 bytes**. SHA256: **`4934842cf5e0554c080f4f204bd7601e1e428452dbeeea4abe62092d33277310`** (rechecked independently after migration). The archive includes database objects and records; treat it as sensitive and do not commit, upload, email or print its contents.
- The Git ignore status of the backup was verified. Digest-only `manifest.json`, `post_upgrade_snapshot.json` and `applied.json` are saved in the same backup directory; source and post-upgrade digests are kept without raw row values.
- The backup is retained on the Windows development host and is **off the PostgreSQL Docker data volume**, but it is **not independently off-site/off-host** or proven safe against loss of the entire development PC. Role/global cluster backup and independent disaster/WAL recovery were **not** verified. An earlier backup attempt left a different folder with an **unverified** raw archive after a transient isolated connection error; do not use it as recovery evidence.

## Independent restoration and pre-commit safety checks

- A fresh, separately named PostgreSQL 16 Docker container and disposable database on a distinct loopback port and volume restored the retained archive using `pg_restore --no-owner --no-acl`. The archive's full owner/ACL records remain in the retained backup; this restoration used normalised ownership in the separate cluster.
- The restored disposable database snapshot exactly matched the source's fresh read-only repeatable-read snapshot, including the 2 existing release reviews, 4 review events, 8 YouTube manifests and their SHA256 fingerprints, expected table/index/column metadata and security/storage contracts.
- The **installed** Alembic revisions (not proposal files) successfully applied the missing `0038_youtube_candidate_selection` sibling and the no-op `0039_merge_0038_branches` on the restored clone. The established three-way comparator passed and a re-upgrade had no effect. The temporary container was removed.
- Immediately before the actual shared development upgrade, the approved runner checked both the backup SHA256 and that the source read-only snapshot still exactly matched the backed-up baseline. The migration used a short `NOWAIT` PostgreSQL table lock on the three protected tables within one explicit transaction. The only special Alembic execution permission was an **already-open, validated Connection** passed to the active Alembic environment via an in-process Config attribute; normal raw Alembic and `plm migrate` still use their own controls.

## Actual database upgrade and independent verification

- Approved command (local development only): `python -m tests.support.migration_local_0039_rollout --apply-approved-local-plm-0039 0039_local_plm_20261027T141743Z_c3e08a9cd61f`
- Actual job: `55bbcad91d67665dfa2113cb922e6958`, request `plm-20261027-0039-approved-local-apply-01`; exit code **0**.
- Executed in a single transaction: `0037_dashboard_views -> 0038_youtube_candidate_selection`, then the two-parent `0039_merge_0038_branches` join. The already-applied `0038_release_reviews` SQL was **not rerun**, no stamp or downgrade was performed on shared data, and there was no destructive table operation.
- Post-migration snapshot of the real development DB exactly matched the **independently upgraded disposable clone** after normalising only the database identifier. A separate, fresh read-only connection confirmed **exactly one** `public.alembic_version` row of `0039_merge_0038_branches`; **2 existing review rows**, **4 existing event rows**, and **8 YouTube manifests**. The new YouTube `selection_json` column exists and is nullable.
- The retained backup SHA256 was independently checked again and still matched. No disposable test containers remained.
- `python -m plm_platform.cli doctor` ran against the connected local stack: **16 PASS, 0 FAIL**. Migration head is current; PostgreSQL, MinIO, Temporal, workers, queues and all nine phase API readiness probes passed. This proves readiness checks, **not** publishing, rights or end-to-end autonomy.

## Code tests and deployment limits

- Focused command and migration safety tests: **61 passed**, exit code 0. Repository-wide Ruff passed, mypy `packages services` passed on **463 files**.
- Full non-integration regression: **3,022 passed, 1 skipped, 80 integration tests deselected, 15 warnings** in **307.46 seconds**, exit code **0** (request `plm-20261027-0039-local-applied-regression-01`, job `39ab577747eff8274c4923412232d94b`). Destructive integration tests and independent WAL/whole-host disaster-recovery drills were not run.
- `migrations/env.py` preserves the normal code-only online guard for all direct Alembic invocations; it only permits an explicitly selected local migration through the ephemeral, source-verified in-process connection. This is not a standing environment-wide permit.
- Other staging/production deployments, real external YouTube-first databases, role/global cluster recovery, retained off-site copies, WAL/crash restoration, downtime/failover, application end-to-end publication and the pending formal architectural ADR are **not** verified/approved. None of these should be marked complete based on this local dev upgrade.
- Git commit/push were not performed.

**Conclusion:** The requested local development database transition from historical review-only 0038 to combined head 0039 was successfully executed with retained host backup, independent disposable PostgreSQL restore, post-upgrade preservation proof and live service doctor green. Other environment rollouts require separate explicit scope/authorization and verified recovery storage.
