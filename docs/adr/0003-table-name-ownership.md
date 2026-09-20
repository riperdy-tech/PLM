# ADR-0003: Phase plans own table names; architecture PART XX is a sketch

- Status: `ratified` — 2026-09-20
- Ruling: `R3`
- Seam: `C9` (neighbouring), affects `PHASE-P2`, `PHASE-P5`, `PHASE-P8`, `PHASE-P9`
- Supersedes / superseded by: `—`

## Context

`architecture.md` PART XX lists "core tables" (`market_sources`, `content_arms`,
`songs`, `song_qc`, `royalty_records`, `decisions`, …) as an illustrative sketch.
The four phases with the heaviest persistence each define their own concrete
schemas, and the names do not match the sketch — P2's `content_arm_state`,
`arm_posterior`, `allocation_cycle`, `production_directive`; P5's
`candidate_evaluations`, `evaluation_snapshots`, `release_manifests`; P8's
`distribution_releases`, `distribution_tracks`; P9's metric fact model. A reader
who takes PART XX literally would rename four phases' tables before writing a
line of code.

## Decision

Phase plans own table names. PART XX is illustrative and may not be used to
rename or type another phase's schema. Cross-phase reads never touch tables
anyway (HTTP only), so no cross-phase query depends on a shared table name.

## Evidence

- `architecture.md` PART XX (sketch list) versus `PHASE-P2` §44–§52,
  `PHASE-P5` §81–§92, `PHASE-P8` §6–§7, `PHASE-P9` §8 (concrete schemas).
- The same class of conflict was flagged for P2, P5, P8 and P9 in P0 §5 and
  resolved once here rather than four times.

## Consequences

Migrations follow the owning phase's names; vocabulary mappings (e.g. "song_qc"
in conversation → P5's evaluation tables) go through this ADR and
`PHASE_MAP.md`-style cross-references, not through renames. A genuinely shared
table name would require a new ADR.

## Alternatives considered

- *Adopt PART XX as canonical* — rejected: it would rename four phases'
  schemas retroactively for no functional gain.
- *Merge sketch and plans into a new master schema* — rejected: a large design
  task, and cross-phase reads are API-only by rule.

## Cost if wrong

If two phases turn out to need the same entity under the same name (e.g. a shared
brand table), the fix is an ADR defining the shared name; until then each phase's
schema stands alone, which is the conservative direction.
