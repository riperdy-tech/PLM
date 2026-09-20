# ADR-0012: P10 is deferred, not cancelled — keep the feature seam

- Status: `ratified` — 2026-09-20
- Ruling: `R12` (`C8`)
- Seam: `C8` — PHASE-P9 → PHASE-P10
- Supersedes / superseded by: `—`

## Context

Architecture part XXVI keeps a roadmap phase P10 ("Recursive learning / autonomous
allocation") and P9 §155/§156 defines a P10-facing contract while saying "P10
decides" who explores, allocates and retires. In this build, P2 owns allocation
and the recursive update loop (R11), so P10 has no work — but deleting its seam
would lose the upgrade path and force P9's feature view to be redesigned later.

## Decision

Keep `GET /analytics/p10/features` and the §155 envelope as a frozen seam named
for its future consumer. Record P10 as deferred, not cancelled.

## Evidence

- `PHASE-P9` §86 (P10 feature view), §88 (the endpoint), §155 (minimum contract),
  §156 (P9 provides facts; P10 decides).
- `architecture.md` PART XXVI (roadmap keeps P10), PART XXVII (P1 → P10 build
  order); P0 §6 (the build stops at P9 + loop closure; P2 absorbs allocation).
- P0 §1: `ARCH-P10` recursive optimization is explicitly out of scope as a
  separate phase.

## Consequences

P9 builds the feature view and endpoint anyway (P9.10), so the seam exists and is
tested (C8's pending seam test) even though no consumer ships. The registry entry
records the field list so a future P10 build starts from a frozen contract rather
than a reconstruction.

## Alternatives considered

- *Delete the P10 seam* — rejected: P9's feature view would then be reshaped
  against an imaginary consumer later; freezing it now costs almost nothing.
- *Build P10 in this program* — rejected: scope; P2 already implements the
  allocation loop that P10 would generalize, and overlapping the two would create
  the worst kind of duplicated decision logic.

## Cost if wrong

If P10 is never built, P9 carries one extra endpoint — cheap. If P10 arrives with
different needs, the seam is amended by ADR as usual; the §86 note that "exact
field shape may evolve" anticipates this under explicit semantics.
