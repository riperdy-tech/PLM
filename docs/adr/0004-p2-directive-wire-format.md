# ADR-0004: P2's directive is the wire format; P3 adapts

- Status: `ratified` — 2026-09-20
- Ruling: `R4` (`C2`)
- Seam: `C2` — PHASE-P2 → PHASE-P3 (production directive)
- Supersedes / superseded by: `—`

## Context

As written, P2 and P3 cannot connect. P2's directive (`PHASE-P2` §32, §120) names
`quantity`, `allocation_type`, `brief`, `expires_at`, `reason_codes`,
`feature_snapshot_id`; P3's expected input (`PHASE-P3` §10) names
`requested_experiment_count`, `exploration_level`, `required_constraints`,
`recommended_attributes`, `production_deadline`, `selection_reason`,
`market_context` — a field-level mismatch on six of twelve fields. P2's directive
also lacks `budget_limit`, which P3 requires, and P3 §4.5 wants
`expected_value`/`uncertainty`, which P2 already emits.

## Decision

P2's directive is the wire format. P2 extends it with `budget_limit`,
`market_context`, `selection_reason` and a versioned `directive_version`
(`expected_value`/`uncertainty` are already present). P3's adapter (P3.4) maps
onto its internal names.

## Evidence

- `PHASE-P2` §32 (directive JSON), §52 (`production_directive` table), §120
  ("P2 output expected by P3") versus `PHASE-P3` §10 ("P2 input contract").
- `PHASE-P3` §4.5 ("Explicit uncertainty"), §11 (translation: soft/fixed/
  experimental split).

## Consequences

The mapping is frozen in registry entry C2: `requested_experiment_count ←
quantity`; `exploration_level ← allocation_type` (enum map); `production_deadline
← expires_at`; constraints/attributes ← arm hard constraints + creative search
region. P2 must now compute `market_context` and `selection_reason` (it already
has the decision-cycle inputs in §30). Freeze before either P2.1 or P3.4 starts
(pre-step W2.0).

## Alternatives considered

- *P3's shape becomes the wire* — rejected: P2 is the allocation engine; its
  directive is the domain record of the decision. Renaming P3's adapter targets
  is mechanical; recomputing P2's decision record would not be.
- *Define a third neutral schema* — rejected: two adapters instead of one, and
  the frozen-directive record in P2 would drift from the wire.

## Cost if wrong

If the added fields are the wrong extensions (e.g. real directives need richer
`market_context`), both phases rework at the W2.0 freeze — early and contained.
The C2 seam test (`tests/contract/test_c2_ar_to_experiments.py`) fails on any
later rename; the remedy is an ADR and a registry amendment, never a producer-side
patch.
