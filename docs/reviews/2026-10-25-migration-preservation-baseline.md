# Local development Alembic 0038: read-only preservation baseline

**Captured 2026-10-25, local development PostgreSQL only.** This document
contains no user records or credentials. It is evidence for a future **approved
isolated-clone migration test**, not permission to migrate the shared database.

The baseline was generated with:

```powershell
.\.venv\Scripts\python.exe -m tests.support.migration_review_snapshot
```

The command uses one PostgreSQL `REPEATABLE READ, READ ONLY` transaction,
reads `current_database()`, the single `public.alembic_version` record,
then both `ops` review tables when present and the common columns of
`youtube.publish_manifests`. When `selection_json` exists, it fingerprints
that column separately. A missing review table pair is represented as
`null` for the YouTube-first sibling; only one missing table is an error.
It never invokes Alembic upgrade/downgrade, writes SQL, or exposes raw rows.
Its output holds only source database identity, current revision, row counts,
digest values and an explicit read-only indicator.

| Field | Baseline |
| --- | --- |
| Database | `plm` (local developer instance; **not** a disposable test target) |
| Revision | `0038_release_reviews` |
| `ops.release_reviews` count | 2 |
| `ops.release_reviews` SHA256 | `37d0323eac48569d50dc58e608c6d5ef3b1e2109bc0c1a855203fca6a5a9e1cc` |
| `ops.release_review_events` count | 4 |
| `ops.release_review_events` SHA256 | `2e9b174a65eb0ec2c0731fb08b8a15819057fd407dfbe73a9fac84f663fdfbef` |
| `youtube.publish_manifests` count | 8 |
| YouTube **common-column** SHA256 (excludes new column) | `495808fcbaf39a88490e3768eed4a68f33483111c62b0f1b465ddb197f2a2460` |
| `youtube.publish_manifests.selection_json` | Absent; no selection fingerprint in this starting branch |
| Transaction mode | `REPEATABLE READ, READ ONLY` |
| DB writes | None |

## 2026-10-26 strengthening: independently expected all-NULL selection hash

The snapshot format now also includes `youtube_all_null_selection` for **both**
historical branches. It hashes the actual ordered manifest IDs paired with an
explicit `selection_json: null`, even when the source database lacks that
column. The connected development database was read again under
`REPEATABLE READ, READ ONLY`: the eight-manifest expected-NULL SHA256 is
`ac7f2a22637f35cccde6df83001a4ededc09282fea7a660982db3ab142ea1ac8`.
All three previous record digests and counts remained unchanged.

**Further 2026-10-26 schema-proof extension:** the read-only inspector now includes
`youtube_selection_column_contract` in each new evidence snapshot. It is
`null` when the column is absent, or exactly
`{"type":"JSON","nullable":true}` when the expected PostgreSQL nullable JSON
column exists. The connected review-first development snapshot confirmed
`null`. The offline comparator refuses wrong SQL types, NOT NULL and numeric
instead of boolean nullability, even when record hashes match. All snapshots
must be recaptured with the current collector. Details:
`docs/reviews/2026-10-26-0038-json-column-schema-contract.md`.

For the review-first branch, the post-merge `youtube_selection` digest must
**equal this independently expected all-NULL hash**, not merely report zero
non-NULL rows. This closes a false-pass path where a fabricated digest with a
valid row count could previously be accepted as an all-NULL addition.
For the YouTube-first branch, the new expected-NULL digest is required as
consistent source/clone metadata, while the pre-existing selection digest
still must remain unchanged. New snapshots for source, clone-before and
clone-after **must all use the upgraded format**. Old reports lack this field
and deliberately fail the stricter comparator; do not fabricate it from an
old report. Each new JSON input is also bounded to 64,000 bytes and rejects
duplicate JSON fields and nonfinite JSON numbers.

This is still operator-supplied digest evidence, **not** an authenticated
database identity, a backup or a successful PostgreSQL migration test.

## How to use this evidence on an approved isolated clone

1. **Starting at `0038_release_reviews`:** Determine whether the source
   records changed since this snapshot. If so, establish a fresh read-only
   snapshot at cloning time; this fingerprint is historical evidence, not
   an eternal expected value. Compare source and isolated clone digests
   **before** migration; a mismatch means the clone is not faithful.
2. After an approved isolated upgrade from `0038_release_reviews`, re-run
   this tool on the clone. Both counts and **complete SHA256 digests** must
   be identical to the pre-upgrade clone. Also verify the nullable
   `youtube.publish_manifests.selection_json` column exists and the
   single `public.alembic_version` is the reviewed merged head.
3. **Starting at `0038_youtube_candidate_selection`:** The `ops`
   release-review tables do not exist yet, so the snapshot reports them as
   `null`. The tool can still capture all common YouTube manifest columns
   and separately the existing `selection_json` values; both fingerprints
   must be identical after migrating the isolated clone. The newly created
   `ops` review tables must contain **zero rows**. This code path is covered
   by synthetic digest tests but has **not been run against a real database
   at the YouTube-first revision**.
4. Compare three saved digest-only snapshots with:
   `python -m tests.support.migration_review_compare source.json clone_before.json clone_after.json`.
   The **pure offline comparator** returns a nonzero exit code and non-secret
   failure reasons if the clone is not an explicitly named disposable database,
   its original revision/data differs from the source, the merged revision is
   absent, a required field is omitted, an empty-table hash is noncanonical,
   row/selection counts contradict each other, a digest is malformed, common
   YouTube rows change, or the branch-specific preservation checks fail.
   It requires exactly the expected snapshot fields, including explicit
   `null` values for inapplicable sibling-branch tables or columns. For the
   review-first branch, existing selection metadata must start absent and the
   new nullable column must contain no non-NULL values. For the YouTube-first
   branch, prior selection content must not change and new review tables
   must be empty. This comparison does not run migrations or authorize them.
5. Reject mismatches or unexpected extra rows. Never forcibly overwrite
   source data or `alembic_version`. Validate both alternative starting
   states with suitable evidence and restore from a tested backup as needed.

The digest algorithm serializes **every column** in each record with sorted
JSON keys, normalizes timezone-aware timestamps to UTC, sorts rows by their
primary key and frames each encoded row with an 8-byte length before SHA256.
It rejects duplicate/missing IDs, unsupported values and samples exceeding
10,000 rows rather than returning misleading partial digests. Each source SQL
query is bounded to 10,001 ordered rows (the extra row detects truncation);
a report with more than 10,000 rows is refused, never accepted as complete.
Unit tests
cover reordering, timezone equivalence, content changes and invalid inputs.

**Limit:** SHA256 equality only establishes equality of the serialized records
read by this tool. The three JSON reports are not signed or independently
attested, and the comparator trusts their recorded database names and revision
values; it cannot prove the actual host identity, prevent fabricated reports,
or establish that source writes did not occur between snapshot and cloning.
An operator must verify the real database target, controlled report capture,
backup provenance and clone isolation independently. It does not validate
business approval, rights, external state or the legality of a release.
