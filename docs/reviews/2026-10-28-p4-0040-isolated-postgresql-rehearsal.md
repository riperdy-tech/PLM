# B03: PostgreSQL 0040 P4 monetary provenance rehearsal — 2026-10-28

**Scope:** User-authorized A–B audit code work; one **disposable PostgreSQL 16 clone only**. No actual upgrade, stamp, downgrade, write, MinIO mutation or publication on the connected local `plm` DB; no staging/production operation.

## Root cause and implementation

The original P4 generation engine multiplied a provider's credit count by the invented exchange `0.05` and stored it as `actual_cost`, while `music.generation_attempts` and `music.provider_cost_records` required non-null numeric monetary fields and defaulted missing values to zero / USD. This was false money provenance.

The new **code-only** `0040_p4_unpriced_costs` revision descends from `0039_merge_0038_branches` and only affects the two `music` tables. It permits null actual/estimated amounts and currency, adds an explicit `money_basis` and a nullable `cost_receipt_ref` (plus `generation_attempts.cost_currency`), labels existing records `legacy_unverified`, and drops the temporary backfill defaults so future writes must name their basis. Database CHECK constraints reject unpriced records carrying a value/receipt and require priced records to have a currency/receipt reference. Historical numeric amounts are preserved, **not** reinterpreted as true expenses; source receipt strings still require future independent provider verification. The downgrade refuses when it would erase new unknowns or priced provenance. Domain validators reject monetary `0.0` as an unknown, invalid/nonfinite priced values and missing currency/receipts.

`plm migrate` refuses a pending code-only 0040, and the normal direct Alembic environment guards shared development connections. A reviewed and separately approved source-backup/restore is needed before any real DB rollout.

## Real isolated PostgreSQL test

- Request: `plm-AB-B03-20261028-p4-0040-real-isolated-rehearsal-01`
- Job: `149d222c7d5b32885970d8b1a4ac0228`; **exit 0**, `passed: true`.
- Explicit runner: `tests/support/migration_p4_costs_isolated_rehearsal.py --approved-isolated-test-only`. The source was positively identified as the connected local PostgreSQL 16 `plm-postgres-1` at `127.0.0.1:5432/plm`, at revision `0039_merge_0038_branches`. The source was only accessed via read-only repeatable-read snapshots and `pg_dump`. The temporary custom-format archive was not saved as a permanent recovery backup.
- Created an independent, uniquely named Docker `postgres:16` container on an automatically assigned non-shared loopback port and new anonymous volume, plus a database named `plm_migration_disposable_*`. It restored the entire source archive with `pg_restore`.
- Exact **pre-migration fingerprint match** for every historical column of **341 `music.generation_attempts` and 341 `music.provider_cost_records` rows**; the earlier R5 review/YouTube source snapshot also matched the restored clone.
- Upgraded **only the disposable clone** from 0039 to 0040 via active installed Alembic code (not a draft), verified one revision row at 0040; existing row hashes and values unchanged; every historical row marked `legacy_unverified`, new receipt fields null, new nullability/field contract correct, no lingering server default, unaffected R5 data and security/structure fingerprints unchanged. Re-upgrade to 0040 was idempotent.
- Rechecked the shared DB against the original source fingerprints after the clone test; both remained equal. The disposable container was stopped and automatically removed.
- Independent fresh read-only postcheck request `plm-AB-B03-20261028-p4-0040-independent-postcheck-01`, job `5c40b2611171582c2fc8e26a41dfc726`, exit 0: shared DB still **one `0039_merge_0038_branches`** row and exactly **341 + 341** historical P4 records; no disposable test containers listed; `git diff --check` passed.

## Additional testing and limitations

- B03 focused migration/domain/CLI checks: **75 passed, 1 skipped** after fixing historical 0039 tests to target the immutable merge rather than the newer `head`. Post-constraint P4 focused tests: **21 passed**, including direct SQLite SQL writes that try to evade model validation and are rejected by database constraints.
- Final **post-constraint** full non-integration suite: **3,044 passed, 1 skipped, 80 integration tests deselected, 15 warnings**, in **292.89 seconds**, exit 0 (request `plm-AB-B03-20261028-final-postconstraints-full-02`, job `bfc7d9569b015a0a803b2cfeb71d96aa`). Includes direct-write DB constraint tests and isolated-runner opt-in tests. Compose R5 full-chain and production-length worker-restart evidence remain unverified.
- Repository Ruff passed after all constraints and test changes (request `plm-AB-B03-20261028-after-rehearsal-ruff-06`, exit 0); mypy passed **463 packages/services files** after those changes (request `plm-AB-B03-20261028-after-rehearsal-mypy-04`, exit 0). Neither validates provider invoices or billing.
- **Remaining B03 gaps:** Suno currently reports a hard-coded credit figure; it must not be treated as provider-verified. There is no verified currency/rate-card-to-invoice reconciliation, full plan-level cap and concurrency proof, or privileged exception control sign-off. Existing currency columns are float; changing to governed `NUMERIC`/Decimal requires separate migration and contract. This is a narrower fix, **not B03 completion**.
- The connected local DB **cannot run new 0040-dependent P4 writes** until an environment-specific migration has been separately approved and applied after a retained backup and independent restoration. No Git commit/push was made and A02 working-tree consolidation is still unfinished.
