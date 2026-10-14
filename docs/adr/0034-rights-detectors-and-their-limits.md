# ADR-0034: P5 rights detectors are declared, injected, and UNKNOWN when they cannot conclude

- Status: `accepted`
- Date: `2026-10-15`
- Ruling: stage R4 ("P5 rights and policy", `docs/reviews/2026-10-13-system-review-and-remediation-plan.md`)
- Seam: none (C4 is read, not changed; the P28 gap on C4 is recorded below)
- Supersedes / superseded by: `—`

## Context

Before R4, P5 computed no audio or lyric similarity (`similarity_score` was never set),
screened prompts against a ten-name list hardcoded in `evaluators/policy.py`, and judged
only the R6 rights snapshot of P28's twelve provenance fields. P4 had no artist screen at
all. A conditional or expired license was reported as the reviewable
`UNKNOWN_COMMERCIAL_LICENSE` instead of the P29 hard stop `COMMERCIAL_LICENSE_UNCERTAIN`.
R4 requires a fingerprint match against the internal catalog and a reference set,
shingled lyric similarity, one protected-artist policy shared by P4 and P5, all twelve
P28 fields, every P29 category mapped to a classified reason code, and that an
unavailable or inconclusive detector is `UNKNOWN`, which quarantines.

## Decision

**Audio fingerprint** (`services/quality_gate/detectors/fingerprint.py`). A spectral
landmark (constellation, peak-pair) fingerprint written in numpy/scipy rather than
chromaprint/`fpcalc`, so it needs no native tool and gives the same bytes on every host
with the same numpy/scipy versions. Mono mix, polyphase resample to 11 025 Hz,
peak-normalise (gain invariance), Hann STFT (1024/256), local maxima within 40 dB of the
loudest bin, at most 30 per second, each paired with up to 5 later peaks within 63
frames and 127 bins; hash = `(f1, f2, dt)`. Matching counts candidate hashes that agree
on one time offset (±1 frame) per reference.
- MATCH: aligned hashes >= 20 **and** aligned / min(candidate, reference hashes) >= 0.05.
- UNKNOWN: audio unreadable; fewer than 30 candidate hashes (silence, a 1 s pure tone);
  a source not configured, unreadable or raising; an empty **reference set**; any entry
  stored under another fingerprint version.
- An empty **internal catalog** is NO_MATCH: before anything is released it is legitimately
  empty, and it is read from P5's own tables.
- The fingerprint version is `algorithm_version` plus a hash of every extraction
  parameter (`p5-landmark-v1+96f2c31ecf04` today), so editing a parameter can never
  reuse stored fingerprints silently.

**Internal catalog** = `qa.candidate_audio_fingerprints` rows (written with every
evaluation) whose evaluation has a row in `qa.release_manifests`. A candidate joins the
catalog exactly when a manifest exists, including one issued later by an audited
override, and is excluded when it is itself re-evaluated. **Reference set** =
`qa.reference_audio_fingerprints` (registered with a required `source_note`) or a
directory of WAV/FLAC/OGG files.

**Lyric similarity** (`detectors/lyric_similarity.py`). Normalisation is the shared one
(`plm_platform.protected_artists.normalise_for_screening`: NFKD, combining marks dropped,
casefold, non-alphanumerics to single spaces). 5-word shingles, hashed with blake2b-64;
the corpus (`qa.reference_lyrics` or a directory of `.txt`) stores hashes only, never
lyric text. `score = max(|C∩R|/|C|, |C∩R|/|R|)`; MATCH when score >= 0.2 or >= 8 shared
shingles (a copied chorus inside a long new lyric). UNKNOWN: corpus not configured,
unreadable or empty; a corpus-version mismatch; fewer than 3 candidate shingles; a
vocal track whose lyric text P5 was not given. NOT_APPLICABLE: an instrumental with no
lyrics (distinct from UNKNOWN, and not a hold).

**Thresholds are data**: `services/quality_gate/detectors/detector_policy.json`
(`rights-detectors-v1`), validated on load. They were set on deterministic synthetic
fixtures, not calibrated on real music.

**"No references configured" is UNKNOWN.** There is no configured-empty state and no
operator attestation that an empty reference set is acceptable: an empty set proves
nothing, so it can never contribute to clearance.

**Protected artists**: `plm_platform/policy/protected_artists.json` (`protected-artists-v1`)
with one loader, `plm_platform.protected_artists`. P4's `build_generation_prompt` screens
the final prompt and style tags and raises `ProtectedArtistPromptRefused` (reason code
attached) before a prompt exists, so nothing reaches a provider. P5 screens the prompt,
each genre tag and the lyrics. A name is `PROTECTED_ARTIST_REFERENCE_DETECTED`; a
voice-clone or impersonation marker is `AI_POLICY_CONFLICT`.

**P28 provenance**: all twelve fields are required. Eight come from C4 (`artifact_id`,
`provider_id`, `model_id`, `prompt_text`, and from `rights`: `account_identity_ref`,
`plan_or_tier`, `{basis, license_reference, terms_version}`, `commercial_use_status`). C4
does not carry `model_version`, `generation_timestamp`, `source_inputs` or
`human_contribution`; they enter as `GenerationProvenanceFacts`, declared by P4, every
field required and nullable. Nothing is defaulted. Missing rights fields are
`INCOMPLETE_R6_PROVENANCE`; any other missing field is `INCOMPLETE_P28_PROVENANCE`. A
declared source input without a license reference is `SAMPLE_PROVENANCE_UNCERTAIN`, or
`UNAUTHORIZED_VOICE_CLONE_DETECTED` for a voice input.

**Commercial use**: declared `conditional` / `expired_or_unverified` is
`COMMERCIAL_LICENSE_UNCERTAIN` (hard stop); no snapshot or declared `unknown` is
`UNKNOWN_COMMERCIAL_LICENSE` (reviewable).

**Gate**: every code in `detectors/reason_codes.P5_HOLD_REASON_CODES` is a hold. With no
blocking finding, any hold quarantines and is raised into the exception queue. P29 hard
stops (artist reference, AI policy conflict) are now quarantined with a hard-stop hold,
not rejected, so the case exists and resolves only through its external process.
Detector results are stored as a `RightsCheck` (score, matched reference, full result)
and an `EvidenceItem` (SHA-256 of the canonical result JSON; the JSON as summary).

**New reason codes** (classified in `hold_classes.json` v4):

| Code | Hold class | Why |
|---|---|---|
| `REFERENCE_AUDIO_MATCH` | HARD_STOP (`p29_sample_provenance`) | re-use of a third-party recording; rights counsel decides outside PLM |
| `INTERNAL_CATALOG_AUDIO_MATCH` | REVIEWABLE (rights_reviewer) | duplicate of PLM's own release; PLM owns both, so a reviewer can justify a re-release |
| `AUDIO_FINGERPRINT_UNKNOWN`, `LYRIC_SIMILARITY_UNKNOWN` | REVIEWABLE (rights_reviewer; evidence: `similarity_review_ref`, `reference_set_ref`) | P5 §49: quarantine is re-evaluable; a reviewer may substitute a documented review, never a bare approve |
| `INCOMPLETE_P28_PROVENANCE` | REVIEWABLE (rights_reviewer) | like `INCOMPLETE_R6_PROVENANCE`: the missing record can be recovered |

## Evidence

- `docs/architecture.md` PART XV P28 (lines 883-901) and P29 (906-921).
- `docs/reviews/2026-10-13-system-review-and-remediation-plan.md` R4 "P5 rights and policy".
- `docs/P5_quality_rights_provenance_release_gate_implementation_plan.md` §30 (lyric
  similarity, 938-961), §40 (near-duplicates, 1196-1215), §49 (quarantine, 1420-1437).

## Consequences

- **C4 gap.** Until C4 carries the four P28 generation facts (a C4 schema change needs its
  own ADR), a real P4 → P5 run has `provenance_status = PARTIAL` and is quarantined with
  `INCOMPLETE_P28_PROVENANCE`. The `generation_facts` parameter exists so the route can
  pass them once P4 serves them; the contract and e2e tests that need a cleared manifest
  declare them explicitly and say so.
- **Unconfigured deployments quarantine everything.** With no reference set or corpus
  configured, every candidate is held with an UNKNOWN code. That is intended.
- P4's mock declares vocal tracks without returning lyric text; such a candidate is
  `LYRIC_SIMILARITY_UNKNOWN`.
- An algorithm or parameter change makes every stored fingerprint stale: the catalog
  and reference set are UNKNOWN until re-fingerprinted from their artifacts.

## Limits (also in `docs/STATUS.md` "Detector limitations")

These are risk signals, not proof of originality or of infringement.
- The fingerprint finds re-use of a *recording* (same audio, re-cut, re-gained, lightly
  noised, resampled). It does not find a re-performance, a cover, heavy processing,
  time-stretch or pitch-shift, or a composition copied into new audio.
- Lyric similarity is lexical. It misses paraphrase, translation and a different
  tokenisation of the same words. Short common phrases can overlap by chance.
- Both know only their references: an empty or narrow corpus hides everything outside it.
- There is no acoustic voice-similarity detector. Voice cloning is caught only when a
  voice input is declared without authorization or the prompt asks for impersonation.
- The protected-artist list is a screening list, not a register; single-word names
  (e.g. "drake") also match ordinary words, and non-Latin names must be listed in Latin
  transliteration.
- Thresholds were chosen on synthetic fixtures; no false-positive or false-negative rate
  on real music has been measured.

## Alternatives considered

- *chromaprint / `fpcalc`*: a native binary per worker image, with output that depends on
  its build; the plan named it, but a host-independent numpy implementation can be tested
  in the unit tier on every machine. It can be added later as a second source behind the
  same `AudioFingerprintSource` protocol.
- *An operator-attested "configured empty" reference set*: rejected; an attestation does
  not make an empty set evidence of originality.
- *Detector UNKNOWN as a hard stop*: rejected; P5 §49 makes quarantine re-evaluable, and a
  rights reviewer can perform the comparison the detector could not. The hold still
  demands the review and the reference set it was checked against.

## Cost if wrong

If the thresholds are too loose, matches are missed and candidates clear: the remedy is
a new `detector_policy.json` version (decision thresholds do not change the fingerprint
version; extraction parameters do and force re-fingerprinting). If too strict, clean
candidates are quarantined and reviewers clear them with evidence; nothing is published
in error.
