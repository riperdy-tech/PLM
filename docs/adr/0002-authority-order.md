# ADR-0002: Authority order — registry, phase plan, architecture, P0 plan

- Status: `ratified` — 2026-09-20
- Ruling: `R2`
- Seam: `all`
- Supersedes / superseded by: `—`

## Context

Nine phase specs were written independently and disagree in places (every seam in
P0 §5 is a concrete instance). Without a stated priority, each reader resolves a
disagreement by whichever document they happen to have open, which is how seam
drift becomes permanent. The registry exists precisely to be the single frozen
answer for cross-phase seams, but nothing said it outranks a phase file.

## Decision

When documents disagree: `docs/contracts/CONTRACT_REGISTRY.md` wins for cross-phase
seams; the owning phase's plan file wins for that phase's internals (including its
own table names and endpoints); `docs/architecture.md` wins for cross-cutting
principles, guardrails and vocabulary; the P0 master plan wins for sequencing and
for seam resolutions until superseded by a registry amendment.

## Evidence

- P0 master plan §0 states the order; §9 R2 records it.
- The conflicts it resolves are catalogued seam by seam in P0 §5 (C1–C10) and in
  the registry entries they became.

## Consequences

Any future conflict has a mechanical fix path: amend the registry (via an ADR)
first, then producer and consumer in the same change. "The phase plan says so" is
no longer a valid answer for a cross-phase field. Registry entries must be kept
current, because they override prose.

## Alternatives considered

- *Newest document wins* — rejected: silently rewrites the frozen wire whenever a
  phase file is touched.
- *Phase plans outrank the registry* — rejected: that is the pre-registry status
  quo that produced the drift the registry exists to stop.

## Cost if wrong

If the registry lags the real intent, a phase could build against a stale field
list. The remedy is the standing one: an ADR correcting the entry, then both
sides move together; the seam test for that entry fails loudly until they do.
