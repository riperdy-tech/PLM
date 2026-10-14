# ADR-0035: C4 carries the full P28 provenance of the producing attempt

- Status: `accepted`
- Date: `2026-10-15`
- Remediation stage: R4
- Seam: C4 (P4 → P5), schema 2.0 → 3.0
- Supersedes / superseded by: `—`

## Context

R4 makes P5 require all twelve P28 provenance fields before clearance (architecture PART
XV). C4 2.0 carried eight of them: the artifact, the generator and model ids, the prompt,
and the declared rights snapshot (account, plan, license terms, commercial-use
eligibility). Four were missing:

- model version
- generation timestamp
- source inputs
- human contribution

The first R4 implementation had P5 take those four as a separate engine parameter that
callers had to supply. Nothing in the running system supplied it, so every real candidate
was quarantined. The facts belong to the attempt that produced the candidate, and only
P4 knows them.

P4 also always exported `lyrics_text=None`, so every vocal track's lyrics were unknown
to P5 and could never be screened.

## Decision

- **New block.** C4 3.0 adds a required `generation_provenance` block
  (`GenerationProvenancePayload`) with four required, nullable fields:
  - `model_version` is what the provider reports for this output. P4 stores it on the
    attempt; the mock reports its synthesis algorithm (`mock-sine-synth-1`); Suno and
    ElevenLabs report nothing yet (R6).
  - `generation_timestamp` is the attempt's completion time.
  - `source_inputs` is `()`: P4 sends providers a prompt and settings only. That is a
    declared fact, distinct from `null` (unknown).
  - `human_contribution` states that the pipeline has no human editing step.
- **Major version.** The bump is 3.0 because the block is required. ADR-0025 allows only
  optional additions in a minor version. A 2.x event is refused.
- **Lyrics.** Providers return lyrics with the output (`GenerationResponse.lyrics_text`),
  P4 stores them on the attempt, and C4 carries them. The mock writes deterministic
  placeholder lyrics for vocal requests, so P5's lyric screening runs on mock output.
- **One source.** P5 reads all twelve fields from the event. The separate engine
  parameter is removed, so there is one source of truth for provenance.
- **Unknown stays unknown.** A fact P4 states as unknown quarantines the candidate with
  `INCOMPLETE_P28_PROVENANCE`. It is never defaulted.

## Consequences

- Migration `0032_music_attempt_provenance` adds `model_version` and `lyrics_text` to
  attempts.
- Attempts stored earlier export as unknown and quarantine; that is accurate about them.
- Real providers must report a model version and lyrics before their candidates can
  clear (R6).
