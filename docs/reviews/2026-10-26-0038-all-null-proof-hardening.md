# 0038 migration preservation: independent all-NULL-column proof

**2026-10-26. Implemented offline evaluator hardening; no migrations or schema changes performed.** Approval for isolated PostgreSQL clones and eventual application remains separate.

## Concrete false-pass found

For a database starting at `0038_release_reviews`, the sibling
`0038_youtube_candidate_selection` adds a nullable JSON column. A correct
migration leaves **all existing YouTube manifest rows at NULL**. The prior
`tests.support.migration_review_compare.compare_preservation` checked that
`youtube_selection_nonnull == 0` and that a syntactically valid selection digest
had the expected row count, but did **not** require the digest to equal the
digest of those same manifest IDs paired with NULL values. Consequently, a
synthetic report could report zero non-NULL values alongside a different
full-row hash and still be accepted. This is a validation gap in the
digest-only comparator, **not evidence that the real DB contains bad data**.

## Corrected proof

- `tests/support/migration_review_snapshot.py` now independently computes
  `youtube_all_null_selection` using every read-only sampled manifest ID and
  literal `selection_json=None`, regardless of whether the column yet exists.
- The comparator requires that field with a valid count/hash in all **three**
  source, disposable-clone-before and disposable-clone-after reports. It checks
  that it remains unchanged and matches the common manifest row count.
- On the `0038_release_reviews` starting branch, the actual post-upgrade
  `youtube_selection` full digest must equal the **pre-upgrade expected NULL**
  digest in addition to an explicit zero non-NULL count.
- The YouTube-first branch preserves the existing selection digest unchanged;
  its independently expected all-NULL hash provides additional source/clone
  identity consistency without replacing that existing-data comparison.
- CLI snapshot JSON inputs are now bounded to 64,000 bytes, reject duplicate
  keys (including nested duplicates), reject NaN/Infinity and require a JSON
  object. Untrusted, missing or malformed evidence cannot silently become a
  successful comparator result.
- These are still operator-provided SHA256 fingerprints, **not signed records
  or proof of actual clone isolation**. Legacy snapshots lacking the new
  required field are deliberately rejected. Re-capture all three reports with
  the upgraded collector; do not invent the field from old JSON.

## Connected source observation (read only)

A real `plm` development-DB snapshot with `REPEATABLE READ, READ ONLY`
returned the existing revision `0038_release_reviews`, 2 review records,
4 review events and 8 common-column YouTube manifest rows. Their SHA256
fingerprints remain unchanged against the 2026-10-25 baseline. It newly
measured the eight-manifest independent all-NULL digest:

`ac7f2a22637f35cccde6df83001a4ededc09282fea7a660982db3ab142ea1ac8`

The `youtube_selection` column is still absent. No original row content or
credentials were logged to this report.

## Validation and boundaries

The focused snapshot, comparator and PostgreSQL offline DDL group completed
**62 passed, 0 failed**. After the final type-specific exception adjustment,
the snapshot/comparator focused set was rerun: **56 passed, 0 failed**.
Repository-wide Ruff **passed**; mypy found no issues in **462 source files**;
the full non-integration regression **2,717 passed, 1 skipped, 80 integration
deselected, 15 warnings** (exit code 0). Real PostgreSQL clone upgrade and
human-reviewed release approval remain unverified.

This does not show that an approved isolated PostgreSQL clone has been
migrated or that six existing review rows were protected under a real upgrade.
No database/object-store writes, backup restore, Alembic stamp, downgrade,
migration installation, external publishing, or Git commit were executed.
