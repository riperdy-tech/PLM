# 0038 sibling migration: preserve existing YouTube manifest structure

**2026-10-26 — READ ONLY source evidence and offline comparator hardening.** No migration, clone, downgrade, stamp, or database write occurred.

## Risk

The three-way 0038/0039 migration preservation comparator previously verified all *existing row values* in `youtube.publish_manifests` with full-column SHA256 digests, plus the newly introduced `selection_json` type/NULL/default contract. However, it did not compare schema properties of **the columns predating both 0038 revisions**. A nullable/type/default/primary-key change on a common manifest column could leave old rows unchanged and pass all hashes while corrupting behavior for new records.

## Implementation

- `tests/support/migration_review_snapshot.py`: `manifest_common_schema_contract` collects a sorted, bounded list of every pre-existing `publish_manifests` column and its SQL type, nullability, server default, timestamp timezone flag, computed/identity presence, plus its `manifest_id` primary key. It excludes only `selection_json`, already separately reviewed. Missing/duplicate/incorrectly typed reflection data rejects collection. It uses the existing **single PostgreSQL REPEATABLE READ, READ ONLY transaction** and never prints row contents. The snapshot adds mandatory `youtube_manifest_schema` evidence.
- `tests/support/migration_review_compare.py`: each of the source/clone-before/clone-after snapshots must provide an exactly typed, ordered, duplicate-free common schema contract with the non-null `manifest_id` primary key. The contract must remain exactly equal between all three points. Missing, malformed and drifted metadata fails even when all manifest row hashes match. The new `selection_json` contract remains a separate requirement.
- `tests/unit/test_migration_review_snapshot.py` and `tests/unit/test_migration_review_compare.py` include synthetic evidence collection and changed-type, NULL, default, computed/identity, primary-key and missing-evidence refusal tests for **both historical 0038 starting revisions**.

## Verified evidence

- Focused snapshot/comparator/offline PostgreSQL DDL tests: **161 passed**, exit 0.
- Actual connected development PostgreSQL, READ ONLY: revision `0038_release_reviews`; **29 common manifest columns**, typed common schema validator `True`; review schema/security/access validators `True`; selection column absent as expected. Existing row counts: **2 reviews, 4 review events, 8 YouTube manifests**. No raw row values or credentials printed.
- Repository-wide Ruff: **passed**, exit 0; mypy on `packages services`: **463 source files passed**, exit 0; change-scoped `git diff --check`: **passed**, exit 0.
- Whole-project non-integration regression: **2,832 passed, 1 skipped, 80 deselected, 15 warnings** in 297.75s (exit 0). The destructive PostgreSQL integration round-trip remained excluded.

## Boundaries

This is a structural evidence comparison, **not** authentication of the original/clone DB identities or the all-object PostgreSQL schema. It does not verify indexes, constraints, triggers or grants on the YouTube manifest table, beyond the primary key and column properties stated above. Only a separately approved isolated PostgreSQL clone can prove actual branch merge behavior and restoration. Existing digest-only evidence JSON files lack `youtube_manifest_schema` and **must be recaptured with the new reader**; never fabricate historical values. The currently deployed development DB still has neither an installed 0039 merge nor the candidate-selection column. No shared DB/MinIO write, external publication or Git commit occurred.
