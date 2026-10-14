# ADR-0030: C7 feedback loop on a shared vocabulary; revenue attributed through ISRC links

- Status: `accepted`
- Date: `2026-10-14`
- Ruling: `R9`, `R10`, `R11`; remediation stage R1
- Seam: C7 (leg 1 P8 → P9, leg 2 P9 → P2)
- Supersedes / superseded by: `—`

## Context

The R0 review found three gaps in the loop.

**Leg 1 had no consumer.** P8 built its distribution record and nothing ingested it.
P9's statement ingestion invented values for missing data:
- an ISRC when the row had none (`USPLM26{row}`);
- `trk_{isrc}` and `rel_{provider}_{period}` as track and release ids;
- `0` for missing streams and revenue.

**Leg 2's producer used a vocabulary P2 refuses.**
- Windows: P9 had `1h` and `14d` and lacked `72h`; P2 rejects the first two.
- Maturity: P9 defaulted to `mature` in one place and `MATURE` in another.
- Quality: P9 defaulted to `eligible`, which is not a `QualityStatus` value.
- Raw metrics: unmeasured values were sent as `0.0`, so P2's null-means-unknown handling
  never applied.
- Reward version: the generic default `v1`.

## Decision

- **Leg 1.** `plm_contracts.seams.c7.DistributionRecordPayload` (schema 1.0) is P8's
  record. It carries `lineage` instead of five loose identifiers, and ISRCs are
  validated. P9's new consumer, `analytics.ingestion.releases`, links each
  (release, ISRC) to the lineage. Replaying a record is idempotent; a conflicting
  lineage is refused.
- **Statement rows.** A row with an ISRC is stored, and its release is taken from the
  link when the row does not name one. A row with no ISRC is counted in the
  statement's `unattributed_revenue_usd` / `unattributed_row_count`. No identifier is
  invented.
- **Leg 2.** `RewardResultPayload` (schema 1.0) is the wire, and P2's
  `ResultPayload` is that model plus P2's reserved-term check. The shared vocabulary:
  - `observation_window` uses `plm_contracts.ObservationWindow`, which P9 now uses too;
  - `status` uses `ObservationMaturity`, now defined in `plm_contracts`;
  - `quality_status` uses `QualityStatus`, or null when not assessed;
  - `raw_metrics` must carry the five registry keys, null when unmeasured.
- **P9 producer inputs.** `evaluate_experiment_outcome` takes the lineage plus explicit
  window, maturity, attribution confidence, quality status and clock.
  `generate_and_post_reward` takes every raw metric explicitly, together with the
  clock. The reward is versioned `engagement-contribution-proxy-v1`. It is a proxy,
  not expected economic value.

## Consequences

- P9 still does not deliver the reward over HTTP reliably. A transactional outbox is
  R3.
- Maturity is the caller's input. Deriving it from publish time and window is R2.
- Migration `0020_analytics_c7_links` adds the links table and the unattributed
  columns, and makes the unknown-capable columns nullable.

## Cost if wrong

If one recording comes to appear on several releases, `lineage_for_isrc` returns
nothing for it rather than guessing. Attribution across releases then needs an explicit
rule, which is a new ADR.
