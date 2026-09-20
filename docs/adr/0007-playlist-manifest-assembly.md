# ADR-0007: P5 stays per-candidate; P6 assembles the playlist-level manifest

- Status: `ratified` — 2026-09-20
- Ruling: `R7` (`C5`)
- Seam: `C5` — PHASE-P5 → PHASE-P6 (release manifest)
- Supersedes / superseded by: `—`

## Context

The most severe break in the corpus. P6 §4 expects one playlist-level manifest
(`release_manifest_id`, `tracks[]`, `brand_id`, `required_duration_minutes`, four
`*_profile` versions). P5 §77/§138 emits one manifest *per candidate* with none of
those. No phase owned assembling the playlist-level manifest, so the handoff had
no producer at all — a structural gap, not a naming one.

## Decision

P5 remains per-candidate and gains a stable `p5_manifest_id`. **P6 assembles the
playlist-level manifest**: it reads P3's `playlist_spec` and `brand_profiles`,
and for each track calls `GET /qa/release-manifests/{candidate_id}`, accepting
only `PASSED` / `PASSED_WITH_WARNINGS` and rejecting any track that is
`QUARANTINED` / `REJECTED` (audited override excepted).

## Evidence

- `PHASE-P6` §4 (the expected playlist-level manifest) versus `PHASE-P5` §77
  (per-candidate JSON), §138 (minimum per-candidate fields), §94 (the per-candidate
  read endpoint it already defines).
- `PHASE-P3` §19 (`playlist_spec`), §26 (`brand_profile_id`).
- P5's output is inherently per-candidate: it evaluates one artifact at a time
  (§9 lifecycle), and its gate semantics (§46–§51) are per candidate.

## Consequences

P6 §4 is amended: `required_duration_minutes` comes from the playlist spec; the
four `*_profile` versions come from P3's brand/spec tables, **not** from P5. P6
gains an assembly responsibility (it already owns sequencing and packaging).
P5 must expose `p5_manifest_id` and P6 must record which per-candidate manifests
it consumed, so an assembled playlist is traceable track by track.

## Alternatives considered

- *P5 emits playlist manifests* — rejected: P5 would need P3's playlist context
  and brand profiles, which it does not consume, and it would blur the gate's
  per-candidate semantics.
- *A new assembly phase between P5 and P6* — rejected: scope; P6 already exists
  to package, and the assembly is packaging.

## Cost if wrong

If P6's assembly policy mis-handles a mixed playlist (some tracks warned), the
error surfaces at Gate P6 (P6 §52 experiment-integrity scenarios A/B/C, §53 DoD)
— before publish. If the split itself is wrong, the fix is an ADR re-scoping,
paid once at W4.0 rather than per seam.
