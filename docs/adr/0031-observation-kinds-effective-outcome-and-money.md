# ADR-0031: Observation kinds, one effective outcome per experiment, and decimal money

- Status: `accepted`
- Date: `2026-10-14`
- Ruling: `R10`, `R11`; remediation stage R2
- Seams: C2 (P2 → P3, schema 2.0), C7 leg 2 (P9 → P2, semantics only)
- Supersedes / superseded by: `—`

## Context

The R0 review found that the system's numbers had no declared meaning, so correct
arithmetic on them was impossible.

- **Overlapping windows were summed.** P9's economics endpoint added the 24h, 7d and
  lifetime snapshots of one video, counting the first day three times.
- **Every window updated the posterior.** P2 applied each observation window of the same
  content as a separate full-weight update, including 24h results that P2 §18 says are
  never final.
- **Decay was frozen at update time.** P2 weighted an observation by its age when the
  update ran, then baked that weight into the stored posterior. Two cycles at different
  times drew from a posterior aged to whenever the last update happened.
- **Maturity was asserted, not derived.** Callers sent `maturity`, and P9 defaulted it to
  mature.
- **Money was a float with an implied currency.** Every amount was `*_usd: float`, P9
  §23's KRW reporting currency did not exist, and there was no FX table.
- **The reward was unlabelled.** P9's bounded engagement/contribution score was easy to
  read as expected economic value.

## Decision

### Observation kinds (`plm_contracts.measurement`)

Every metric record declares one `ObservationKind`:

| Kind | Meaning | Valid aggregation |
|---|---|---|
| `incremental` | An amount for a non-overlapping period (a statement line, a cost) | `sum_incremental`. Two live records for the same (entity, metric, period) are refused as an overlap. |
| `cumulative_snapshot` | Running total since publication, for one window, at `as_of` | `latest_snapshot` of one window. Never summed. |
| `rolling_snapshot` | Trailing-window value at `as_of` | `latest_snapshot`. Never summed. |
| `experiment_outcome` | One experiment's measured result at a window | `effective_outcome` |
| `revision` | Replaces the record named in `supersedes` | Applied by every helper before aggregating |

P9 stores YouTube video and DSP track metrics as cumulative snapshots per window.
Statement lines and costs are incremental per period. The economics endpoint takes the
latest snapshot of one window (default `lifetime`) and sums only incremental amounts.

### Maturity is derived

`maturity_at(published_at, window, as_of)` is `mature` once `published_at + window`
has passed, and `provisional` before that. `lifetime` has no end, so it stays
provisional until the source closes it (`closed`). P9 derives maturity from the
publication time and no longer accepts it from callers.

### One effective outcome per (experiment, content)

- **P2's policy declares the outcome horizon.** `PolicyConfig.outcome_horizon` defaults
  to `28d`, P2 §19's "mature for normal comparison". It is versioned with
  `policy_version`.
  - The allocator owns the definition of the reward it learns from.
  - P3 has no channel back to P2. So P2 declares the horizon, and P3 records it.
- **The C2 directive carries the horizon** in a new required field `outcome_horizon`
  (schema 1.0 → 2.0; ADR-0025 allows only optional additions in a minor). P3 records it in the experiment's measurement plan at design
  time, before any content exists, so the horizon is predeclared and cannot be chosen
  after the results are seen.
- **P2 applies exactly one outcome per (experiment, content)**: the one whose window
  equals the declared horizon and whose maturity is `mature` or `closed`.
- **Other outcomes are recorded but never move the posterior.** Earlier windows,
  provisional results, and windows past the horizon are recorded with a reason
  (`provisional`, `off_horizon`).
- **A second mature outcome at the horizon for the same key is refused as a duplicate.**
  Accepting it would need a revision protocol, and none exists yet.

### The posterior is computed at draw time

The policy draws from `posterior(as_of)`. This is the Normal–Normal combination of the
prior with every effective outcome, each weighted by `recency_weight(observed_at,
as_of)`. Because it is computed from stored facts and the cycle's own `as_of`, two cycles
over the same evidence at the same instant draw identically, and age discounting is
always measured from the draw. The stored posterior row is a cache of the last
computation; it is not the source of truth.

### Allocation fractions

The exploit, explore and radical fractions must sum to 1, within `1e-9`. If they sum to
less, the leftover slots are allocated to nobody.

### Money (`plm_contracts.money`)

- Every amount is a `Money` (`Decimal` amount + ISO 4217 currency). Floats are refused,
  and combining two currencies raises.
- Ledger precision is 6 decimal places, rounded half-even. Rounding to the currency's
  minor unit happens only for display.
- Conversion uses an `FxTable` of recorded rates (rate, source, as_of). The latest rate
  at or before the requested instant is used, either directly or inverted (flagged as
  inverted).
  - A missing rate raises; it is never treated as 1.
  - Conflicting rates recorded at the same instant raise.
- The P9 reporting currency defaults to KRW (P9 §23). Unit economics carry the rate
  provenance of every amount converted into it.

### The reward is a labelled proxy

P9's reward is `engagement-contribution-proxy-v1`. It is a bounded proxy, **not**
expected economic value. How each kind of input is treated:

| Input | Treatment |
|---|---|
| Immature (provisional) outcome | Still produced and posted, but P2 records it without applying it. |
| Missing views or watch time | Refused; never computed from zeros. |
| Estimated revenue (YouTube estimates) | Kept apart from reported or final statement revenue, and labelled as estimated. |
| Low attribution confidence | Passed through; P2 quarantines anything below its policy floor. |

Estimated subscriber or catalog value is never booked as realized revenue.
`economic_terms` on C7 stays reserved for the economics gate.

## Consequences

- C2 producers must send `outcome_horizon`. Schema 1.x directives are refused, because
  the field is required and has no safe default.
- The C7 leg-2 wire is unchanged. Its `raw_metrics.estimated_revenue` is still an
  unlabelled float. Its currency is the P9 reporting currency, and moving it to `Money`
  is deferred to the economics gate.
- A revision protocol for experiment outcomes (posting a corrected value at the horizon)
  is not implemented. Duplicates are refused and recorded.
- Gate R2 is checked by these tests:
  - three windows of one content give one effective observation;
  - 24h + 7d + lifetime snapshots count revenue once;
  - money converts reproducibly from the same FX table.
