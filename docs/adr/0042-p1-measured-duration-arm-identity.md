# ADR-0042 — P1 duration arm identity derives from observed length

- Status: accepted for A–B local code development; **no connected DB rollout or historical score/arm rewrite authorized**
- Date: 2026-10-28
- Owner: P1 market taxonomy, with P2/P3 consumer impact to verify
- Related: ADR-0014, ADR-0015, ADR-0036, ADR-0037; R5-DUR original audit

## Context

P1 normalizes and stores YouTube `video_duration_seconds` from observed
`contentDetails.duration`, but its classifier accepted only the title and
description. The old synonym table classified phrases such as `45_75m`
as an identity-bearing `duration_bucket` even when the video actually
lasted two minutes. The classification cache was keyed on text alone, so
identical titles for differently sized videos reused one incorrect arm
identity. That identity flows through frozen C1/C2 to P2 allocation and
P3's playlist duration policy. Changing historical arms in place would
silently rewrite experiment/economic attribution.

## Decision

1. P1 taxonomy **v2** derives `duration_bucket` solely from the
   normalized measured video duration, not from titles/descriptions.
   Missing/NOT_READY duration carries **no duration dimension**; it is
   not assumed to be the P3 playlist default. Negative, NaN, infinite or
   nonnumeric measurements are refused by the classifier and must be
   handled as invalid ingestion evidence.
2. Use these non-overlapping measured-second boundaries:
   `lt_60s` [0,60), `60_180s` [60,180), `3_10m` [180,600),
   `10_45m` [600,2700), `45_75m` [2700,4500], and
   `gt_75m` (4500,infinity). Boundary membership is explicit.
   Existing controlled value tokens and frozen C1/C2 fields do not change.
3. Classification input now includes the measured seconds, and its
   content fingerprint includes the **measured duration bucket** when
   known. Equal title+description in different duration bins cannot
   share a cached result; equal measured bins can. Old text-only
   fingerprint shape stays stable, but `MarketSettings.taxonomy_version`
   advances from `p1-taxonomy-v1` to `p1-taxonomy-v2`; the rule-based
   classifier version also advances. No v1 cached classification is read
   under v2. The P1 classification worker explicitly selects observed
   duration from `market_observations`, rather than inferring it.
4. The P1 canonical arm profile includes `duration_bucket`, but
   **profile identity alone is not enough** to protect historical v1
   arms when a v2 measurement happens to match an old title guess.
   Persist v2 arm fingerprints with the explicit namespace
   `p1-taxonomy-v2|<canonical profile>` and deterministic readable
   IDs prefixed `p1_taxonomy_v2_`. The existing 13-dimension C1
   vocabulary and wire object remain unchanged. Direct historical
   non-namespaced callers preserve their prior identity algorithm.
   A v2 upsert therefore **cannot refresh a v1 arm's support or
   evidence**, even if its dimension values happen to be identical.
   Existing v1 rows, P2 decisions, releases, feedback and scores
   are not deleted, renamed or backfilled by this code-only change.
   The readable ID does **not** encode all identity dimensions. If a
   different fingerprint claims a previously persisted slug during
   v2 recomputation, the storage adapter opts into
   `refuse_slug_collision=True` and raises
   `arm_slug_identity_conflict` **before any new arm ID/score mapping
   is returned**. Legacy direct inserts retain their established
   incumbent-first behavior to avoid silently changing older callers.
   The existing v2 scheme stays unchanged for compatibility, so this
   fail-closed guard remains necessary for already persisted v2 arms.

   **Optional v3 implementation, not an approved rollout:** when P1 is
   explicitly configured with `p1-taxonomy-v3`, newly generated IDs
   use `p1_taxonomy_v3_<up-to-8-char-slug>_<40-hex-digest>`, always
   **at most 64 characters**. The digest is the first 160 bits of SHA-256
   over the complete `p1-taxonomy-v3|<canonical 13-dimensional profile>`
   fingerprint. That makes independent daily batches, classification
   ordering, hidden mood dimensions and slug truncation irrelevant to
   identity with high collision resistance. The initial full 64-hex digest
   design was **too long** for existing downstream P5/P6/P7/P8/P9
   `VARCHAR(64)` arm-ID columns (up to about 124 characters), even though
   P1/P2 `Text` storage and SQLite tests accepted it. The v3 format was
   corrected **before activation**, without altering v1/v2 IDs.
   A unit regression now introspects actual downstream ORM/SQLAlchemy
   column widths, alongside isolated PostgreSQL persisted-arm checks.
   The code **does not** change the default v2 taxonomy setting, its
   existing arm IDs, mock R5 outcome keys, database rows, historical
   P2 allocations, scores or releases. This engineering option is
   deliberately held behind the new namespace until a versioned
   migration/mapping and full R5 consumer audit receive approval.
   SHA-256 provides practical collision resistance, not a mathematical
   guarantee of unique inputs for an arbitrary finite hash.
5. Rebuild scored-arm membership from all stored observation records
   at the requested `as_of` time, matching each row's text and measured
   duration bucket to its cached classification. Multiple observations
   sharing the same classification contribute all distinct evidence IDs
   and all support counts, not just the one observation ID attached to
   the cached classification row. Future observations must not enter
   the earlier score. Mean confidence must use the same versioned
   arm fingerprint key as the computed features.
6. Transition/rollout must separately review any persisted arm
   allocation, duplicate hypotheses and score/reward comparability,
   with reversible mapping and version-scoped evidence.
7. Other services keep using the frozen taxonomy strings. P3 continues
   to refuse a directive outside its declared 45–75-minute policy; a
   measured short-video arm is NOT license to silently change P3 policy.

## Acceptance and limits

Run tests for exact boundaries, missing/invalid durations, title-value
conflicts, cache isolation/replay and arm fingerprint differences.
Run P1/P2/P3 contract tests on the same code and an isolated PostgreSQL
test to verify stored normalized measurements reach classification.
Before promotion, review historical v1 arm identities and their P2/P3
lineage and run a controlled rematerialization/identity migration plan.
No connected development/production DB migration or autonomous P2 budget
reallocation is authorized by this ADR. A code PASS is not approval of
historical score mutation or proof of an end-to-end live observation.

**Isolation requirement for Gate R5:** `tests/integration/test_r5_full_chain.py`
activates a P2 allocation policy, deletes/recreates learning state for tested
arm IDs, registers P7 channel and QA detector references, and runs real
pipeline HTTP writes. Running it against the configured shared development
DB would mutate unrelated state, even with mock providers. It must be run
only on an explicitly disposable, isolated Compose/Temporal/MinIO/PostgreSQL
stack after confirming its endpoint and test-data boundary.
The R5 test module now includes an **autouse pre-write isolation gate**:
`PLM_R5_ISOLATED_STACK=1`, a loopback PostgreSQL URL whose database
name begins `plm_r5_`, and exclusively loopback phase API endpoints.
Without explicit opt-in the suite skips; invalid opt-in configuration
fails before its first state mutation. This protects the default
shared development DB from the test's destructive cleanup, but the
operator must still verify that the isolated HTTP services use the
same disposable database and resource namespace.

*Amendment (2026-10-29, Option A):* the database part of this gate is now
the shared rule `plm_platform.migration_safety.is_disposable_migration_target`,
also used by `plm migrate` and raw Alembic: a `plm_r5_*` or
`plm_migration_disposable_*` name, `127.0.0.1`/`localhost` and an explicit
port other than 5432 (`::1` and an implicit port are no longer accepted).
CI runs R5 on `plm_r5_ci` at `127.0.0.1:55432`, with
`PLM_R5_ISOLATED_STACK=1` on the step, and the compose services receive the
same name through `PLM_POSTGRES_DB`.
The isolated single-P1 PostgreSQL test does **not** replace that
full-chain gate.
