# YouTube 0038 selection column default / generation preservation

**2026-10-26 — offline comparison guard, no Alembic migration or database writes.**

## Risk

The original `0038_youtube_candidate_selection` adds `youtube.publish_manifests.selection_json` as a nullable PostgreSQL `JSON` column **without a server default, computed expression or identity property**. The prior digest-only source/clone comparator checked only the type and nullability. Existing rows may remain identical even if someone adds a server default or generated-column definition, yet subsequent manifests could get silently assigned values, violating the NULL-means-unknown rule.

## Change

- `tests/support/migration_review_snapshot.py`: the existing `REPEATABLE READ, READ ONLY` snapshot now records the reflected `server_default` (SQL expression or `null`), `has_computed` and `has_identity` booleans for `selection_json`, alongside `type` and `nullable`. Absent selection columns still have a `null` contract; duplicate/malformed columns and non-string defaults refuse collection.
- `tests/support/migration_review_compare.py`: the exact reference contract is now `{"type":"JSON","nullable":true,"server_default":null,"has_computed":false,"has_identity":false}`. It refuses missing/extra fields, type impersonators, unexpected defaults, computed columns or identity metadata even when all stored data hashes match. An invalid contract is rejected even if source, clone-before and clone-after all repeat it.
- Extended the two unit-test modules with positive inspector evidence, invalid default types, unexpected default/computed/identity changes on **both** historical starting branches, and missing contract fields.

## Verified

- Targeted snapshot/comparator/PostgreSQL-offline-DDL tests: **148 passed** (exit 0).
- Whole-project non-integration regression: **2,819 passed, 1 skipped, 80 deselected, 15 warnings** (exit 0, 296.73s).
- Repository-wide Ruff: **passed**, exit 0; mypy on `packages services`: **463 source files passed**, exit 0; changed-file `git diff --check`: **passed**, exit 0.
- Connected development PostgreSQL: a fresh **REPEATABLE READ, READ ONLY** snapshot succeeded at `0038_release_reviews`; the selection column is absent, and the recorded review schema/security/access contracts all passed. Existing rows counted **2 reviews, 4 events, 8 YouTube manifests**; no raw records or secrets were printed. This validates the column-absent historical branch, **not** a post-upgrade default/generated-column definition.
- The new rejection tests use synthetic schema/reflection evidence and Alembic offline compilation, **not** a successfully migrated PostgreSQL clone.

## Remaining obligations

All three old evidence snapshots must be **recaptured** with the expanded selection contract; do not insert assumed fields into prior evidence files. Proposed `0039_merge_0038_branches` and historical `0038_release_reviews` revisions remain outside the active Alembic migration tree. Isolated PostgreSQL upgrades from both siblings, data preservation, backup/restore and single-head convergence remain **unperformed**. This bounded evidence check does not inspect every property of `youtube.publish_manifests` or authenticate snapshot provenance. No shared DB/MinIO writes, schema patch, migration, external publication or Git commit occurred.
