# ADR-0014: P1's 13 taxonomy dimensions are authoritative

- Status: `ratified` — 2026-09-20
- Ruling: `R14` (`C1`)
- Seam: `C1` (producer-side correctness)
- Supersedes / superseded by: `—`

## Context

P1 disagrees with itself. §14.2 defines 13 controlled taxonomy dimensions
(Genre, Subgenre, Mood, UseCase, Language, VocalMode, VocalGender, Energy,
BPMRange, DurationBucket, VisualWorld, AudienceIntent, TitleArchetype). AC-05
validates only 10 — it omits `VocalGender`, `BPMRange` and `AudienceIntent`. The
arm fingerprints that P2 keys its bandit on include these dimensions (§7 example:
`…|vocal|low_medium|45_75m|…`), so the smaller set would collapse genuinely
distinct arms.

## Decision

P1 §14.2's 13 dimensions are authoritative. AC-05 is corrected to match.

## Evidence

- `PHASE-P1` §14.2 (13 dimensions) versus AC-05 (10 dimensions, three absent).
- `PHASE-P2` §7 (canonical arm fingerprint uses normalized P1 dimensions
  including vocal mode and BPM range).
- P0 §5 C1 flags the disagreement as one of the seam's two gaps.

## Consequences

P1.7 (taxonomy) builds 13 enumerated dimensions with an `other_candidate` bucket
(P1 §14.3); classification validates against all 13. The AC-05 correction is
recorded here and in registry C1's required-amendments line — the phase file is
not rewritten (R1).

## Alternatives considered

- *The 10-dimension set (AC-05) wins* — rejected: it drops dimensions P2's arm
  identity depends on; near-neighbor detection (P2 §8) would conflate arms.
- *Make the extra three optional* — rejected: optional dimensions reintroduce
  the same collapse whenever a classifier skips them, and the tri-state rule
  ("unknown ≠ zero") already covers genuinely missing values.

## Cost if wrong

Three extra classifier dimensions cost LLM runs, bounded by P1 §28's cost
controls. If one dimension turns out useless, retiring it is an ADR plus a
taxonomy-version bump (P1 §29) — additive and versioned, not a re-key.
