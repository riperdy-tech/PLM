# ADR-0005: The track spec is the union; P3 emits every P4 §7 key

- Status: `ratified` — 2026-09-20
- Ruling: `R5` (`C3`)
- Seam: `C3` — PHASE-P3 → PHASE-P4 (track spec)
- Supersedes / superseded by: `—`

## Context

P3 hands P4 a track specification, but the two sides name the same concepts
differently and each is missing keys the other side reads: `role` vs `track_role`,
`duration_range` vs `requested_duration`, `vocal_profile`/`instrument_profile`
vs `*_spec`; and P4 additionally needs `candidate_target`, `priority`,
`budget_limit` and `provider_policy`, none of which P3 emits. As written, P4
cannot start a generation request from a P3 package without guessing.

## Decision

The canonical `music_specs[]` payload is the union, with P4 §7's key names
frozen as the wire names. P3 emits every P4 §7 key, deriving the ones it does not
naturally carry.

## Evidence

- `PHASE-P4` §7 ("Inputs from P3") — the required keys, and its reject list.
- `PHASE-P3` §20 (`role`, `duration_range`, `vocal_profile`,
  `instrument_profile`, `creative_latitude`), §44 (content package fields),
  §58 (`track_specs`).

## Consequences

Documented derivations (P3 side): `candidate_target`, `priority`, `budget_limit`
from the budget envelope (P3 §34, §44); `requested_duration` from `duration_range`
by a documented rule; `provider_policy` from the experiment's provider
constraints. P4 must reject only what its §7 list says it rejects — no extra
strictness — or the derived values will bounce.

## Alternatives considered

- *P4 derives the missing keys from P3's package itself* — rejected: P4 would
  need P3 internals (budget envelope, experiment constraints), violating the
  black-box boundary both specs describe.
- *Rename P3's fields to match P4* — rejected: phase files are not rewritten;
  the adapter layer exists for exactly this.

## Cost if wrong

If a derivation rule is wrong (e.g. duration buckets map badly), candidates are
rejected at P4 with a clear reason, at the W3.0 freeze — contained. If the wire
names chosen are wrong, the C3 seam test pins the drift and the registry (C3) is
amended.
