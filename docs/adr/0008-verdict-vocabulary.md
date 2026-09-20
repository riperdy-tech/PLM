# ADR-0008: "cleared" is not a state — the canonical verdict vocabulary

- Status: `ratified` — 2026-09-20
- Ruling: `R8` (`C5`)
- Seam: `C5` (and the P6/P8 prose that consumes it)
- Supersedes / superseded by: `—`

## Context

P5 defines four dispositions — `PASSED`, `PASSED_WITH_WARNINGS`, `QUARANTINED`,
`REJECTED` (§51) — plus an audited override path (§79). P6's manifest example
instead says `provenance_status: "cleared"`, and P8's prose consumes "cleared"
too. A word that is not a state leaking into seam payloads is exactly the drift
class the registry exists to stop; and "cleared" over-promises legal certainty
that P5 explicitly disclaims (§164).

## Decision

Canonical verdict vocabulary is P5 §51's four dispositions plus
`CLEARED_BY_OVERRIDE` (the audited override outcome). "Cleared" in P6/P8 prose
means "`PASSED`, or `PASSED_WITH_WARNINGS` where that phase's own policy permits
warnings" — it is not a wire value.

## Evidence

- `PHASE-P5` §51 (four dispositions), §78 (downstream gating: P6 may consume only
  `PASSED`/`PASSED_WITH_WARNINGS`), §79 (override must be explicit and auditable),
  §164 ("No false claim of legal clearance").
- `PHASE-P6` §4 (`"provenance_status": "cleared"`), P6/P8 prose.

## Consequences

P6 and P8 validators accept exactly the four dispositions (and the override
record where present); any "cleared" literal in code or payloads is a bug. The
override remains a first-class auditable action, never a `force=true` bypass
(P5 §79). Registry C5's required-amendments line carries this vocabulary.

## Alternatives considered

- *Add `cleared` as a fifth state* — rejected: a synonym for an existing state
  is drift by construction, and it implies a legal certainty the program cannot
  claim.
- *Rename P5's dispositions to match P6's word* — rejected: four states carry
  real distinctions (warned vs clean vs quarantined vs rejected); collapsing them
  loses the gate semantics.

## Cost if wrong

If operators find the four-state vocabulary insufficient, an additional
disposition is an ADR plus a registry amendment — additive and cheap. Until then,
prose that says "cleared" keeps its meaning via this mapping, which the registry
states once.
