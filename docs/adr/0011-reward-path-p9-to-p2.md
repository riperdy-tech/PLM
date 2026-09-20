# ADR-0011: P9 posts normalized rewards to P2's POST /ar/results

- Status: `ratified` — 2026-09-20
- Ruling: `R11` (`C7`)
- Seam: `C7` — PHASE-P9 → PHASE-P2 (the loop-closing leg)
- Supersedes / superseded by: `—`

## Context

P2 §71/§72 defines `POST /ar/results` and a reward-observation payload — and no
phase was ever cast as its producer. Without it, P2's posteriors never see the
outside world: the allocation engine keeps sampling its initial priors forever.
P9, meanwhile, computes exactly the reward arithmetic the endpoint expects. The
program's own success criterion ("that outcome updates the allocation policy that
chose the arm") is false until this wire exists.

## Decision

**P9 posts normalized rewards to P2's `POST /ar/results`.** P9 owns the reward
arithmetic — `normalized_reward`, `reward_version`, window, maturity, attribution
confidence, quality status. P2 owns posterior updates, idempotency and
concurrency (§73–§75).

## Evidence

- `PHASE-P2` §71 (endpoint exists), §72 (result ingestion contract example:
  `experiment_id`, `arm_id`, `content_id`, `observation_window`, `status`,
  `observed_at`, `raw_metrics`, `normalized_reward`, `reward_version`,
  `attribution_confidence`), §74 (posterior update must be idempotent on
  `experiment_id + observation_window` or an upstream observation id).
- `PHASE-P9` §38/§39, §153 (experiment outcome schema with `window`, `maturity`,
  `value`, `measurement_status`, `confidence`), §155 (facts and derived metrics;
  decisions stay out of P9).
- The master plan's success criterion (§1) and Wave 6 closure (W6.1).

## Consequences

Wire names are P2 §72's (the endpoint is P2's): window → `observation_window`;
maturity → `status`; attribution confidence → `attribution_confidence`.
R11's term **"quality status" has no wire field name in either source — recorded
in registry C7 as `unspecified - must be resolved at the PHASE-P2 freeze`**, never
guessed. Wave 6 implements and tests the loop end to end: a second P2 cycle must
allocate differently.

## Alternatives considered

- *P2 pulls results from P9* — rejected: P2 should not poll analytics; the push
  contract already exists in P2's own API surface.
- *An event broker between P9 and P2* — rejected: an HTTP contract is simpler,
  already half-specified (P2 §72), and testable with the seam template.

## Cost if wrong

If the wire misaligns, rewards fail to update posteriors — the exact silent
learning failure P0 §1 calls severity-equal to a crash. The guard is the C7 seam
test plus W6.1's assertion that allocation changes; the unresolved name is
flagged in the registry so the freeze cannot miss it.
