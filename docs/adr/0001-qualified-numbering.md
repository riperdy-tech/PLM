# ADR-0001: Qualify the numbering — PHASE-Pn, ARCH-Pnn, Pn.m

- Status: `ratified` — 2026-09-20
- Ruling: `R1`
- Seam: `all`
- Supersedes / superseded by: `—`

## Context

Three numbering systems collide across the corpus. The nine phase files are named
`P1…P9`, so "P7" naturally means the YouTube phase; `architecture.md` numbers its
capability items `P1…P30` inside parts I–XVII, so "P7" also means "Learn what
'success' actually means"; and each phase file numbers its own implementation
steps `Pn.m`, so "P7.3" looks like a section reference but is a step. Every
phase brief flagged the collision, and P0 §5 C6 shows the damage: "P7 §9 tolerates
field-name drift" is nonsense if the reader picks the architecture item.

## Decision

From Wave 0 onward, all new documents, code comments, ADRs and commit messages
use the qualified forms: `PHASE-Pn` for build phases, `ARCH-Pnn` for architecture
capability items, `Pn.m` for a phase's own steps. The mapping is recorded once, in
`docs/contracts/PHASE_MAP.md`. Existing phase files are not rewritten.

## Evidence

- `docs/architecture.md` parts I–XVII carry items `P1`–`P30`; the nine build
  phases are also called `P1`–`P9`; step lists `P1.1`–`P9.12` exist in the phase
  files' recommended sequences (e.g. `PHASE-P7` §74, `PHASE-P9` §203).
- P0 master plan §0 documents the collision and this ruling; §5 C6 shows a
  sentence that is ambiguous without it.

## Consequences

Readers pay a few characters of verbosity; in exchange, `PHASE-P7 §65` and
`ARCH-P17` are unambiguous. `PHASE_MAP.md` becomes the single mapping document —
do not restate it elsewhere. The build-phase ↔ ARCH-item mapping is derived there
and marked stated/inferred per row.

## Alternatives considered

- *Rewrite the phase files with qualified numbering* — rejected: the files are
  contract text under frozen review; rewriting them churns citations and is a
  bigger change than the problem.
- *Keep unqualified numbers plus a glossary* — rejected: the briefs already had
  prose warnings and still collided; the fix must be in the reference form
  itself.

## Cost if wrong

If the qualified forms still confuse (e.g. someone reads `ARCH-P7` as a phase),
one document — `PHASE_MAP.md` §2 — answers it in one table; if a future document
needs yet another namespace, this ADR is amended via a new ADR, not by editing
the phase files.
