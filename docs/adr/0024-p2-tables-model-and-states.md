# ADR-0024: P2's tables, its statistical model, and how SHADOW stays harmless

- Status: `ratified` — 2026-09-20
- Ruling: R3, ADR-0023 #5; seams C1, C2
- Seam: C2
- Supersedes / superseded by: —

## Decision

1. **Tables.** PHASE-P2 §44's names take the monorepo's `<schema>_` prefix: the twelve
   §44 tables become `ar_*`, plus three tables §44's field lists require but never name —
   `ar_posterior_update_log` (§74's persisted update state), `ar_channel_state` (§117's
   activation state), `ar_manual_override` (§107/§108's explicit, expiring event).
2. **Model.** The posterior is a stdlib normal–normal conjugate (`math`/`random`); no
   numpy/scipy dependency is added. §112 explicitly permits a lightweight custom
   implementation, §11 recommends Normal–Normal, and determinism is simpler without a
   numeric stack. `RewardModel` (§66) keeps the seam so the model is replaceable.
3. **SHADOW serving rule.** Directives are persisted for every cycle, but the actionable
   surface (`GET /ar/directives` and `list_queued_directives`) serves only cycles whose
   persisted `activation_state` is `AUTONOMOUS`. In SHADOW/DRY_RUN the compute, the
   decisions and the directives are auditable; nothing is actionable (§115/§116/ADR-0023 #5).
4. **Coverage rule.** Because ADR-0022 froze the C1 field list and P1's
   `insufficient_coverage`/`basis_weight` do not cross the wire, P2's ADR-0023 #5 threshold
   is operationalised as **2+ non-null components on the C1 payload**; under-covered arms
   are still persisted (state + snapshot) and are returned with `EVIDENCE_INSUFFICIENT`
   rather than ranked.

## Consequences

A C1 wire change to carry `basis_weight` would need a registry amendment (R2) — out of
P2's remit; recorded in the plan's open items. The effective-value threshold remains a
config value (`PolicyConfig.min_measured_components`), not a wire change.

### P2.3 addendum (implementer deviation, reported to the controller)

The plan's DDL stored `normalized_reward` as a nullable float alone, which collapses a
`not_ready` measurement into an absent one — exactly the defect P1's `not_ready_metrics`
column was added to fix. `ar_reward_observation` and `ar_experiment_result` therefore
carry `normalized_reward_state` (`known` | `unknown` | `not_ready`, from `TriState`):
the float is non-NULL exactly when the state is `known`, so an absent measurement, a
measured zero and a `not_ready` stay distinguishable in storage and on read. Raw metrics
keep their tagged `Tri` JSON form for the same reason.
