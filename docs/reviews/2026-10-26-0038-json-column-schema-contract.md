# P6/YouTube 0038 sibling merge: read-only JSON column schema contract

**2026-10-26 — source-schema capture and offline comparator implementation complete; no PostgreSQL clone migration approved or applied.**

## Gap found

The three-way source/clone-before/clone-after digest-only migration comparator previously checked that the YouTube `selection_json` column existed and that all expected old row values survived. It did **not** inspect whether the column had the SQL type and nullability required by `migrations/versions/youtube/youtube_0038_candidate_selection.py`: **PostgreSQL JSON, nullable**.

A wrongly constructed or manually altered clone column could produce matching row digests while breaking downstream use. A new test also showed that comparing Python dictionaries directly treated a submitted numeric `nullable: 1` as equal to `nullable: true`, which is unacceptable as a schema attestation.

## Fix

- `tests/support/migration_review_snapshot.py` uses SQLAlchemy's PostgreSQL column inspector during the existing `REPEATABLE READ, READ ONLY` snapshot to capture **`youtube_selection_column_contract`** as `{"type":"JSON","nullable":true}` when present, or JSON `null` when absent. Malformed inspector evidence fails rather than being guessed.
- `tests/support/migration_review_compare.py` requires the new field in **all three** evidence snapshots, demands an exact typed PostgreSQL JSON/nullable contract when the column exists and `null` evidence when absent, and requires the merged clone to have the correct definition. Boolean typing is checked by identity rather than equality with integers.
- The existing independently calculated `youtube_all_null_selection` full-row checksum, manifest fingerprints, revision checks and refusal of malformed JSON remain in effect.
- Extended `tests/unit/test_migration_review_compare.py` and `tests/unit/test_migration_review_snapshot.py` to cover non-JSON types, JSONB, NOT NULL, numeric booleans, missing definitions, forbidden definitions on a column-absent branch, duplicate inspector records, and successful expected shapes.

## Actual development DB observation

A real connected development DB snapshot using the enhanced reader exited successfully without writing. It reported:

| Detail | Value |
| --- | --- |
| Database revision | `0038_release_reviews` |
| Review table records | 2 (unchanged fingerprint) |
| Review event records | 4 (unchanged fingerprint) |
| YouTube manifest records | 8 (unchanged fingerprint) |
| `selection_json` present | false |
| `youtube_selection_column_contract` | `null` |
| Independently expected all-NULL fingerprint | `ac7f2a22637f35cccde6df83001a4ededc09282fea7a660982db3ab142ea1ac8` |

This corroborates that the shared development DB is still on the review-first sibling and lacks the YouTube 0038 column. It does not test an existing or newly migrated YouTube-first clone.

## Evidence and outstanding gate

The targeted comparator/snapshot/PostgreSQL offline DDL set completed **73 passed, 0 failed** (exit 0). Repository-wide Ruff **passed**, mypy **462 source files passed**, and full non-integration regression **2,728 passed, 1 skipped, 80 integration deselected, 15 warnings** (exit 0).

The schema evidence format changed: old source/before/after JSON snapshots are now **intentionally rejected** and must be recaptured with the upgraded reader, not hand-filled. The comparator evaluates operator-supplied data and does not cryptographically authenticate database host, isolation, backups or permissions. It verifies only this specific column's type/nullability and the recorded data fingerprints; it does **not** yet check every constraint/index in the full schema.

**Not performed:** actual Alembic merge, stamp, downgrade, schema write, disposable-clone creation, object-store write, external publication, Git commit or human rights/quality review. A separately approved isolated PostgreSQL clone experiment remains required.
