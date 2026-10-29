# Release-review history storage preservation — 2026-10-26

The 0038 reconciliation evidence now includes PostgreSQL relation storage metadata for both `ops.release_reviews` and `ops.release_review_events`. The snapshot is gathered using the same REPEATABLE READ, READ ONLY transaction as the row digests.

The comparator requires permanent ordinary relations (`r`, `p`), default replica identity (`d`), no partition and zero rewrite rules, both before and after the proposed merge. On the YouTube-first branch, both approval-history tables and their storage evidence must be absent before the missing sibling migration.

Focused snapshot, comparator and offline-DDL suite: **272 passed** (exit code 0). Ruff passed; mypy passed for **463 source files**. Full non-integration regression: **2,943 passed, 1 skipped, 80 integration tests deselected, 15 warnings** in **296.62s**, exit code 0 (existing job `e34009971c914338f8aaeee51b848ade`; it was not restarted). Destructive/integration tests were not run. The connected PostgreSQL read-only inspection saw revision `0038_release_reviews`; both review tables matched the storage contract. Existing records were not printed or changed.

This does not prove WAL recovery, crash durability, backup/restore or successful 0039 Alembic migration. Previous three-way JSON evidence requires real recapture. No database migration, shared DB or MinIO writing, publishing or Git commit occurred.
