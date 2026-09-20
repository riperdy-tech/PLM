# ADR-0019: brand_id is a first-class field everywhere; one brand seeded

- Status: `ratified` — 2026-09-20
- Ruling: `R19` (`PHASE-P1`–`PHASE-P8`)
- Seam: `all` (vocabulary)
- Supersedes / superseded by: `—`

## Context

Brand identity is named three ways across the corpus: `active_channel_id`
(P2 §30 decision-cycle inputs), `brand_profile_id` (P3 §26/§44; P7 §9),
`brand_id` (P4 §29 storage path; P6 §4/§5 manifests; P7 §8.1 channel profile;
P8 §9 artists). Multi-brand scaling is a roadmap goal (architecture PART XXIII,
roadmap P12), so inconsistent identity early means a cross-cutting rename later,
with every wire in between.

## Decision

`brand_id` is a first-class field everywhere. One brand is seeded for the first
build. Where a phase speaks of a *profile record* rather than the brand itself,
`brand_profile_id` remains the correct name for that record.

## Evidence

- `PHASE-P2` §30 (`active_channel_id`), `PHASE-P3` §26 and §44
  (`brand_profile_id`), `PHASE-P4` §29 (`{brand_id}` path segment),
  `PHASE-P6` §4/§5 (`brand_id` in both contracts), `PHASE-P7` §8.1
  (`youtube_channel_profile.brand_id`) and §9 (`brand_profile_id`),
  `PHASE-P8` §9 (`artists.brand_id`).
- `architecture.md` PART XXIII (Brand A…D with independent brand/content
  genomes).

## Consequences

Seam payloads that carry brand identity use `brand_id`; `brand_profile_id`
refers to the versioned profile record owned by P3 (R7 already sends the four
`*_profile` versions from P3). P2's `active_channel_id` is an adapter input, not
a wire name. The first build ships one seeded brand; nothing assumes more.

## Alternatives considered

- *Keep per-phase names with a mapping table* — rejected: every seam pays the
  mapping tax, and the mapping would live in the registry anyway.
- *Rename in the phase files* — rejected: R1; adapters map instead.

## Cost if wrong

If a brand ever needs multiple channels with different identities, that is an
additional dimension (channel vs brand), not a rename — additive. If a phase
genuinely needs a profile-level identity on a wire, it asks for
`brand_profile_id` explicitly, which this ADR already distinguishes.
