# ADR-0027: The C4 event is built from stored facts; rights are what the provider declared

- Status: `accepted`
- Date: `2026-10-14`
- Ruling: `R6` (P4 read endpoints and rights snapshot), `R8`; remediation stage R1
- Seam: C4
- Supersedes / superseded by: `—`

## Context

The R0 review found the P4 → P5 handoff failing open in five ways.

1. P4 wrote every rights snapshot as `PERMITTED` / `pro_tier` / `plm-service-account`,
   whichever provider produced the audio.
2. P4's tokens (`permitted`, `pro_tier`) were not P5's (`ELIGIBLE`, `pro`), so P5
   classed every real candidate `UNKNOWN`. Separately, P5 let a plan-tier label such as
   `pro` upgrade an undeclared status to `ELIGIBLE`.
3. The export used `content_arm_id="arm_placeholder"` and `provenance_complete=True`.
   It took the rights of the plan's first attempt rather than the attempt that
   produced the candidate.
4. P5's real intake (`POST /qa/evaluations`) did not take the C4 event at all. Its
   callers supplied loose fields with defaults. `vocal_mode="instrumental"` silently
   skipped lyric checks, and an empty `rights_snapshot` stood in for "no rights".
5. P5 imported P4's event class directly.

## Decision

- **One wire model.** `plm_contracts.seams.c4.CandidateReadyPayload` (schema 1.0) is
  the only C4 model, and P5 parses with it. The payload carries:
  - `lineage` (ADR-0026) and `artifact_sha256`;
  - the facts P5 judges on: `requested_duration_seconds`, `vocal_mode`, `genre_tags`,
    `prompt_text` and `lyrics_text` (null means no lyrics were returned);
  - `rights`: the declared snapshot, or null when the provider declared none.

  `event_version`, `rights_status` and `provenance_complete` are dropped. The snapshot
  replaces the last two.
- **Rights are declared by the provider adapter** through
  `MusicProviderProtocol.license_terms()`. P4 records exactly that.
  - The in-process mock declares PLM-owned internal synthesis.
  - Suno and ElevenLabs declare nothing until contract terms are on file (P0 §10).
- **One commercial-use vocabulary.** `plm_contracts.CommercialUseEligibility` (P5
  §34) is used on both sides. Migration `0017_music_c4_facts` maps P4's stored tokens.
- **P5 rights rules.**
  - No snapshot means provenance `MISSING` and commercial use unknown, which
    quarantines.
  - A free or trial tier makes the output ineligible, which rejects.
  - A tier label never upgrades a status.
- **P4 builds the event only from stored facts.** These are the plan's C3 spec, the
  candidate's own producing attempt (`source_attempt_id`) and its variant, the single
  full-mix artifact, and that attempt's snapshot. A missing fact refuses the export
  (HTTP 409) before the candidate is marked exported.

## Consequences

- `POST /qa/evaluations` and `plm qa evaluate` take a C4 event plus an audio path.
  Fetching the artifact by URI and checksum is R4.
- P4 still ignores the spec's `provider_policy` when choosing an adapter: its default
  (mock) serves every spec. Under `ProviderMode.MOCK` that is the intended behaviour,
  but it is implicit. Selecting among allowed real providers belongs to R6 and is
  recorded in `docs/STATUS.md`.
- A real P4 → P5 run with the mock provider still fails P5's duration check when the
  spec asks for more than 30 s. That duration seam belongs to R2.

## Alternatives considered

- *Keep `rights_status` as a string and normalise case in P5.* Rejected: it would leave
  P4's hardcoded `PERMITTED` in place. The fault was the invented value, not the case.

## Cost if wrong

If the snapshot carried on the event diverges from P4's R6 `/license` endpoint, the
event wins for gate decisions, because it is what P5 judged. The endpoints remain for
audit.
