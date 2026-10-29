# 0038 review schema and column ACL safety evidence

**2026-10-26.** This work strengthened offline evidence review for the competing Alembic 0038 branches without applying a migration or writing to PostgreSQL.

## Failure mode

The preceding review-table security snapshot attested table ownership, table-level explicit grants, user triggers and RLS, but did not inspect explicit privileges on individual columns, the enclosing `ops` schema ACL or default ACL rules for future tables in that schema. Those omissions could allow a cloned database to pass the row and schema checks despite differing access semantics.

## Changes

- `tests/support/migration_review_snapshot.py`: `review_access_contract` gathers one strict `ops`-schema record, verifies exactly two review tables, `schema_acl_default` as a true bool, and integer counters for schema-local table default-ACL entries and explicit per-column ACLs. It rejects missing, extra, partial, duplicated or wrong-typed data. If the historical YouTube-first branch has no review tables, the evidence must be `null`; unexpected access evidence is rejected. The source query uses PostgreSQL catalogs, remains inside the existing `REPEATABLE READ, READ ONLY` snapshot and returns no role names or privileges themselves.
- `tests/support/migration_review_compare.py`: the `release_review_access` field is mandatory in each snapshot. Both review tables must meet the historical no-explicit-schema-ACL / no-schema-local-table-default-ACL / no-explicit-column-ACL contract. Review-first source-to-clone and clone-before-to-clone-after comparisons require unchanged evidence. On the YouTube-first path the newly created review tables must meet the contract.
- Unit tests exercise correct and malformed catalog rows, missing evidence, wrong types, explicit grants/default ACLs, historical starting branches, and changed cloned permissions. Synthetic fixtures are not database migration evidence.

## Verified

- Focused snapshot/comparator/PostgreSQL offline DDL suite: **134 passed**, exit 0.
- Live connected development PostgreSQL read-only snapshot: `0038_release_reviews`; expected schema, ownership/security and access checks `True`; review rows **2**, event rows **4**, YouTube manifests **8**. No record values or credentials printed.
- Full non-integration regression: **2,789 passed, 1 skipped, 80 integration tests deselected, 15 warnings** in 293.58s (exit 0).
- Repository-wide `ruff check .`: **passed**, exit 0; mypy on `packages services`: **462 source files passed**, exit 0; change-scoped `git diff --check`: **passed**, exit 0. Previous unrelated whole-worktree whitespace findings were not modified.

## Not verified / safeguards

The proposed 0039 merge remains uninstalled; no isolated clone, real migration, single-head reconciliation, restore test, database/MinIO writes or publication were performed. A future approved clone test must recapture all three snapshots using the required `release_review_access` field; old JSON evidence must never be fabricated or silently amended. This bounded test is not a proof of effective privileges across inherited roles, global default ACLs, schema ownership or grants on unrelated objects. The comparator also does not independently authenticate clone identity. Unexpected evidence intentionally fails closed for human inspection.
