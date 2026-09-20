# ADR-0009: Carry experiment identity end to end

- Status: `ratified` — 2026-09-20
- Ruling: `R9` (`C6`)
- Seam: `C6` — PHASE-P6 → PHASE-P7 → PHASE-P8
- Supersedes / superseded by: `—`

## Context

P9 §38 requires every P7 video and P8 release to link back to `experiment_id`,
`experiment_family_id`, `content_arm_id`, `content_genome_id` and
`production_directive_id` — "do not retroactively guess the experiment
assignment". Today neither P7's `youtube_video_published` event (§65) nor P8's
`P8 → P9` record (§71) carries the full set, and `media_package_id` (the P6
package identity) is absent from both. The learning loop — the entire point of
the program — would silently break at the last hop, turning every result into
`experiment_context = UNKNOWN`.

## Decision

Experiment identity is carried end to end. P7's `youtube_video_published` event
and P8's `P8 → P9` record must both carry `experiment_id`,
`experiment_family_id`, `content_arm_id`, `content_genome_id`,
`production_directive_id` — plus `media_package_id` so the published artifact is
attributable to the package that produced it.

## Evidence

- `PHASE-P7` §65 (event JSON — carries `experiment_id`, `content_arm_id` only),
  §12 (state machine; also gains `PROCESSING_TIMEOUT` under this ruling).
- `PHASE-P8` §71 (record JSON — no experiment fields at all), §39 (provenance
  list).
- `PHASE-P9` §38 (required linkage), §39/§153 (outcome records keyed by it),
  §83 (experiment metadata integrity metrics).

## Consequences

P7 §65 gains four fields; P8 §71 gains six; P8's canonical release package and
provenance chain keep `p5_manifest_id`/`p6_manifest_id` references (§11, §39).
P7's §12 state list gains `PROCESSING_TIMEOUT`. Freeze at W5.0; Wave 6's
end-to-end chain asserts the second P2 cycle allocates differently *because* the
linkage survived.

## Alternatives considered

- *P9 joins identities afterwards from video/release ids* — rejected: P9 §38
  bans retroactive guessing, and the join would be untestable guesswork.
- *Only carry `experiment_id`* — rejected: the feedback loop's attribution needs
  the family/genome/directive ids to attribute to arms and directives, not just
  to the experiment.

## Cost if wrong

A missing identity field on a real provider flow lands as `experiment_context =
UNKNOWN` (visible, counted, not silent) rather than corrupting attribution. The
C6 seam test pins both payloads; any extension is an ADR plus registry amendment.
