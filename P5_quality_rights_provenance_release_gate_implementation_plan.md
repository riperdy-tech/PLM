# P5 — Music Quality, Rights, Provenance & Release-Gate Engine
## Detailed Implementation Plan

**Document status:** Implementation-ready specification  
**Phase:** P5 of the Autonomous AI Music Media Engine  
**Upstream:** P4 — Music Generation Factory  
**Downstream:** P6 — Playlist / Visual / Video Factory and later P7/P8 distribution/publishing  
**Primary objective:** Evaluate generated music candidates for technical quality, musical usability, provenance completeness, rights/commercial-use eligibility, policy risk, duplication/similarity risk, and release readiness — then expose only sufficiently cleared candidates to downstream production.

---

# 1. Purpose

P5 is the **quality-control and release-gating layer** of the autonomous music system.

P4 is intentionally allowed to create a large number of imperfect candidates. P5 determines what can continue through the pipeline.

P5 answers:

> **Is this candidate technically usable, musically usable, sufficiently documented, rights-compliant for the intended use, consistent with our provenance requirements, and safe enough to hand to downstream production?**

P5 must not answer:

> “Will this song go viral?”

That is an A&R/performance question and belongs to P2/P9/P10.

P5 should also not pretend to provide a legal conclusion merely from automated similarity analysis. It creates **evidence-backed risk classifications and deterministic release gates**. Human review remains available for ambiguous or high-risk cases.

The intended operating principle is:

```text
P4
  ↓
large candidate set
  ↓
P5
  ├─ technical QA
  ├─ musical QA
  ├─ structural QA
  ├─ provenance QA
  ├─ license/commercial-use QA
  ├─ similarity / rights-risk screening
  ├─ policy screening
  └─ release decision
  ↓
P6 receives only candidates that satisfy its contract
```

---

# 2. Strategic role in the overall architecture

The master pipeline is:

```text
P1 Market Intelligence
        ↓
P2 Autonomous A&R
        ↓
P3 Content Genome / Experiment
        ↓
P4 Music Generation
        ↓
P5 QUALITY / RIGHTS / RELEASE GATE
        ↓
P6 Media Factory
        ↓
P7 YouTube Publishing
        ↓
P8 Streaming Distribution
        ↓
P9 Analytics / Economics
        ↓
P10 Recursive Optimization
        ↺
```

P5 is deliberately positioned before P6 because P6 should never spend substantial rendering/distribution resources on candidates that are obviously unusable or insufficiently documented.

---

# 3. Scope

## 3.1 In scope

P5 must implement:

1. P4 candidate ingestion.
2. Immutable evaluation snapshots.
3. Technical audio validation.
4. Audio loudness and dynamic-range measurements.
5. File/container integrity checks.
6. Audio artifact consistency checks.
7. Basic musical structure analysis.
8. Silence and malformed-output detection.
9. Automatic artifact anomaly detection.
10. Vocal/audio quality screening where technically feasible.
11. Lyrics integrity screening when lyrics are present.
12. Provenance completeness checks.
13. Provider/model/license snapshot validation.
14. Commercial-use eligibility evaluation.
15. Prompt/input rights-risk screening.
16. Artist/track reference detection in prompts and metadata.
17. Unauthorized voice-clone risk screening where feasible.
18. Lyric similarity screening.
19. Audio/melodic similarity screening using permitted local or internally derived representations.
20. Duplicate and near-duplicate candidate detection.
21. Cross-catalog duplicate detection.
22. Policy-risk rule engine.
23. Release-gate policy engine.
24. Quarantine state and reason codes.
25. Human-review queue for exceptions.
26. Evidence bundle generation.
27. P5 decision/audit log.
28. P5 → P6 handoff contract.
29. P5 → P7/P8 policy metadata requirements.
30. Cost and compute accounting.
31. Monitoring and alerting.
32. Re-evaluation support when provider rights or policy metadata change.
33. Unit/property/integration/fixture tests.
34. Capped and shadow rollout modes.

## 3.2 Explicitly out of scope

Do not implement in P5:

- autonomous music generation;
- choosing which genre to make;
- selecting the next experiment;
- YouTube publishing;
- Spotify distribution;
- playlist sequencing as a creative product;
- final video rendering;
- thumbnails;
- monetization optimization;
- financial forecasting;
- audience-performance prediction;
- automatic legal advice;
- legal registration of copyright;
- asserting ownership of third-party material solely from a similarity score;
- scraping prohibited/private source data;
- buying or manufacturing streams;
- Content ID claims against third parties;
- automated copyright dispute submissions without explicit later authorization.

---

# 4. Current platform constraints that P5 must respect

P5 is designed around current platform guidance and must keep provider/platform rules configurable because they can change.

## 4.1 YouTube AI disclosure

YouTube currently requires disclosure for AI-generated music. YouTube's current guidance explicitly lists “AI generated music” among content creators need to disclose. The disclosure itself does not automatically limit audience reach or eligibility to earn money. ([YouTube Help](https://support.google.com/youtube/answer/14328491?hl=en))

Therefore P5 must record whether a candidate contains AI-generated music and expose a required disclosure flag downstream.

P5 must not treat disclosure as a quality failure.

## 4.2 YouTube monetization and repetitive content

YouTube's current monetization policy says repetitive or mass-produced content can be ineligible for monetization, and specifically warns against AI-generated content made with generic or unoriginal templates that create the impression of mass production. ([YouTube Help](https://support.google.com/youtube/answer/1311392?hl=en))

P5 therefore needs a **content-level risk signal**, but it must not attempt to determine YPP eligibility for the entire channel. Channel-level assessment belongs to later operational/publishing policy layers.

P5 should nevertheless expose useful flags such as:

```text
visual_template_risk = low/medium/high
track_reuse_risk = low/medium/high
candidate_family_similarity = low/medium/high
```

P6/P7 can combine those signals with channel-level information.

## 4.3 YouTube copyright checks

YouTube's upload workflow includes copyright checks, but YouTube explicitly warns that those checks are not final and later manual claims, strikes, or settings changes can affect a video. ([YouTube Help](https://support.google.com/youtube/answer/57407?hl=en))

Therefore P5 must never represent its own automated screening as equivalent to a final platform copyright clearance.

Use state semantics such as:

```text
internal_screening = passed
platform_check = pending
```

rather than:

```text
copyright_safe = true
```

## 4.4 YouTube claims

YouTube explains that Content ID can create claims when matching copyrighted content is detected; a Content ID claim differs from a copyright removal request/takedown and usually does not itself constitute a copyright strike. ([YouTube Help](https://support.google.com/youtube/answer/7002106?hl=en))

P5 must therefore distinguish:

```text
similarity risk
platform claim
copyright takedown
likeness claim
```

They are not interchangeable states.

## 4.5 Suno commercial-use rights

As of the current September 2026 Suno support documentation, songs downloaded while subscribed to a paid plan are granted commercial-use rights. Suno also explicitly says commercial-use rights do not guarantee copyright protection. ([Suno Help](https://help.suno.com/en/articles/9601665))

P5 must therefore store two separate concepts:

```text
commercial_use_eligibility
copyright_protection_status
```

The first can sometimes be evidenced from provider subscription/licensing records; the second may remain jurisdiction-dependent/unknown.

---

# 5. Core design principles

## 5.1 P5 is a gate, not a judge of artistic taste

Automated quality scoring is inherently imperfect. P5 should provide multiple measurable signals rather than one opaque “song quality = 82” value.

## 5.2 Every decision must have evidence

A candidate cannot be rejected with only:

```text
quality_score < 70
```

It must expose the failed checks:

```text
vocal_artifact_score = high
clipping_detected = true
lyrics_complete = false
```

## 5.3 Hard gates and soft signals are different

Example:

```text
hard gate:
commercial_use_eligibility = ineligible

soft signal:
musical_coherence = 0.68
```

A soft signal can influence prioritization; a hard gate can stop release.

## 5.4 Unknown is not pass

If a commercial-use license cannot be established, do not silently mark the candidate eligible.

Use:

```text
unknown
```

and route according to policy.

## 5.5 Unknown is not necessarily reject

An ambiguous similarity signal should not automatically destroy a candidate.

Use quarantine/review where the evidence is insufficient.

## 5.6 Preserve the original artifact

Never “fix” the source artifact in place.

Any transformation produces a new artifact with lineage.

## 5.7 Re-evaluation must be possible

A candidate may be blocked today because a provider license status is unknown, then become eligible later after evidence is added.

Therefore P5 decisions are versioned evaluations, not permanent labels.

## 5.8 Provider/platform rules are configuration

Do not hard-code every rule in Python branches.

Store policy configuration with versions and effective dates where practical.

---

# 6. Core concepts

P5 should use these domain objects.

## 6.1 CandidateEvaluation

One evaluation run against one P4-generated candidate.

## 6.2 EvaluationSnapshot

Immutable copy of the facts used by the evaluation.

## 6.3 TechnicalCheck

One deterministic media/technical validation.

## 6.4 MusicalCheck

One musical/audio analysis signal.

## 6.5 RightsCheck

One check related to provenance, license, ownership evidence or similarity risk.

## 6.6 PolicyCheck

One check against internal/platform policy rules.

## 6.7 RiskFinding

A normalized problem or uncertainty finding.

## 6.8 EvidenceItem

A source-backed artifact supporting a finding.

## 6.9 GateDecision

The deterministic final disposition for the candidate.

## 6.10 QuarantineCase

A candidate that requires additional evidence or human review before release.

## 6.11 ReleaseManifest

The downstream package containing P5 decision metadata and all evidence identifiers required by P6/P7/P8.

---

# 7. P4 input contract

P5 must consume the P4 handoff without reconstructing generation history.

Expected event shape:

```json
{
  "event_type": "music_candidate_ready_for_p5",
  "event_version": "1",
  "candidate_id": "candidate-123",
  "artifact_id": "artifact-456",
  "track_spec_id": "track-spec-789",
  "experiment_id": "experiment-001",
  "content_arm_id": "arm-123",
  "provider_id": "suno",
  "model_id": "model-version",
  "measured_audio": {
    "duration_seconds": 198.37,
    "sample_rate": 44100,
    "channels": 2
  },
  "rights_status": "unknown",
  "provenance_complete": true
}
```

P5 must also be able to retrieve, through internal APIs, the full candidate record, artifact manifest, provenance chain, prompt metadata, generation attempts and license snapshot.

---

# 8. P5 request boundary

Internal service/API request:

```json
{
  "candidate_id": "candidate-123",
  "evaluation_policy_version": "p5-v1",
  "requested_checks": "default",
  "priority": "normal",
  "trigger": "candidate_ready"
}
```

Allowed `trigger` values:

```text
candidate_ready
manual_recheck
policy_change
license_update
provider_change
artifact_replacement
scheduled_recheck
```

P5 must not mutate the P4 candidate artifact.

---

# 9. Evaluation lifecycle

```text
READY
  ↓
INGESTED
  ↓
SNAPSHOT_CREATED
  ↓
TECHNICAL_CHECKS
  ↓
MUSICAL_CHECKS
  ↓
LYRIC_CHECKS
  ↓
PROVENANCE_CHECKS
  ↓
RIGHTS_CHECKS
  ↓
POLICY_CHECKS
  ↓
AGGREGATE
  ↓
GATE_DECISION
  ├── PASSED
  ├── PASSED_WITH_WARNINGS
  ├── QUARANTINED
  └── REJECTED
```

Possible terminal statuses should remain explicit.

Do not combine `QUARANTINED` and `REJECTED`.

---

# 10. Evaluation snapshot

At evaluation start, persist a snapshot containing:

```text
candidate metadata
artifact hash
artifact metadata
P3 TrackSpec version
experiment version
provider snapshot
model snapshot
license snapshot
policy version
classifier versions
existing-catalog snapshot reference
```

The goal is to make the decision reproducible even if providers or policies change later.

---

# 11. Technical audio validation

This is the first gate.

Required checks:

```text
file exists
file readable
container valid
codec valid
sample rate valid
channel count valid
duration valid
sample count valid
no truncated frame data
no malformed metadata
```

Supported formats should be configurable, but the canonical internal master should use a lossless format appropriate for later distribution.

Do not unnecessarily transcode before evaluating the original file.

---

# 12. Audio file integrity

Use cryptographic checksums:

```text
SHA-256
```

Store:

```text
artifact_id
sha256
byte_size
mime_type
container
codec
```

Re-reading the file later must produce the same checksum unless a new artifact version is intentionally created.

---

# 13. Duration validation

Compare three sources where available:

```text
P4 measured duration
container metadata duration
decoded duration
```

Allow a small provider/decoder-dependent tolerance.

Do not hard-code an exact zero tolerance because floating-point and container metadata can differ slightly.

But flag material discrepancy.

Example:

```text
P4 = 198.37s
container = 198.36s
decoded = 198.37s
status = pass
```

versus:

```text
P4 = 198.37s
container = 198.37s
decoded = 12.1s
status = fail
```

---

# 14. Sample-rate/channel validation

Ensure the candidate meets the P3/P4 requested profile.

P5 may accept additional technically sound values when policy allows, but it must record the deviation.

Example:

```text
requested_sample_rate = 44100
actual_sample_rate = 48000
```

Do not automatically reject solely because the value differs if downstream distribution can legally/technically accept it.

Instead:

```text
TECHNICAL_VARIANCE
```

and allow policy to decide.

---

# 15. Loudness analysis

Measure:

```text
integrated LUFS
short-term loudness
momentary loudness
true peak
sample peak
```

P5 should not force one universal loudness target for all genres.

A loudness target must be policy/profile driven.

Store:

```text
measured_lufs
true_peak_dbtp
loudness_profile
```

Use these values for quality and downstream mastering decisions rather than automatically applying heavy compression.

---

# 16. Clipping detection

Detect:

```text
sample clipping
true-peak clipping
sustained saturation
```

Use a multi-level classification:

```text
none
minor
material
severe
```

A single sample near 0 dBFS should not automatically reject the track.

Material repeated clipping can be a hard or soft gate depending on profile.

---

# 17. Silence analysis

Detect:

```text
leading silence
trailing silence
internal silence
unexpected dropouts
```

Do not assume every silence is bad. Ambient music can intentionally contain low-energy passages.

Use context:

```text
expected_intro_silence
expected_outro_silence
internal_dropout
```

An unexplained multi-second total silence in the middle of a non-ambient track is materially different from a 0.4-second intentional transition.

---

# 18. Channel/balance analysis

Measure:

```text
left/right RMS difference
correlation
phase issues
mono compatibility proxy
```

Flag potentially problematic conditions:

```text
extreme imbalance
near-total phase cancellation
unexpected mono collapse
```

Do not reject every negative-correlation segment because some stereo processing can legitimately create phase relationships.

Use severity thresholds and duration.

---

# 19. Dynamic-range / crest-factor analysis

Calculate a basic profile:

```text
crest factor
short-term loudness variance
dynamic range proxy
```

These values are quality signals rather than universal release rules.

Genre-specific profiles should later be configurable.

---

# 20. Spectral anomaly detection

Detect gross anomalies such as:

```text
ultrasonic spikes
unexpected high-frequency noise
spectral holes
codec-like artifacts
broadband hiss
abrupt spectrum discontinuities
```

Use a classifier/severity approach rather than a single hard threshold.

P5 should focus on clearly abnormal generated-audio artifacts, not enforce one “correct” spectrum.

---

# 21. Generation artifact detection

AI music can exhibit artifacts that are not obvious from simple loudness measurements.

Possible detectors:

```text
vocal phoneme glitches
warbled syllables
unnatural note transitions
unstable stereo field
repeated micro-loops
instrument timbral collapse
unnatural reverberation changes
```

The implementation may use a specialized audio-quality model, embedding-based model, or a combination of deterministic DSP and ML.

The exact model is intentionally not fixed here.

P5 must expose:

```text
artifact_detector_name
model_version
score
confidence
segment_timestamps if available
```

---

# 22. Segment-level findings

Whenever possible, findings should point to timestamps.

Example:

```json
{
  "finding_type": "vocal_artifact",
  "severity": "high",
  "start_seconds": 71.42,
  "end_seconds": 74.11,
  "confidence": 0.89
}
```

This makes human review much faster and allows P4/P6 to decide whether a revision is cheaper than rejection.

---

# 23. Musical-structure analysis

P5 should infer approximate structure where technically possible:

```text
intro
verse
pre-chorus
chorus/drop
bridge
outro
```

For instrumental music, alternative labels may be used:

```text
intro
A
B
break
peak
outro
```

This is not required to be perfectly accurate.

It is used as a consistency signal and later for clip selection/playlist context.

---

# 24. Repetition analysis

Detect suspicious repetition:

```text
exact repeated segment
near-identical repeated segment
excessive loop reuse
```

Do not confuse intentional chorus repetition with generation failure.

The detector should operate on:

```text
mel-similarity
spectral similarity
embedding similarity
```

combined with structural position.

Repeated chorus structures are expected.

Repeated 4-bar artifacts appearing 12 times outside an intended loop structure may be problematic.

---

# 25. Musical coherence

Create signals such as:

```text
pitch stability
tempo stability
harmonic consistency proxy
section transition consistency
instrument continuity
```

These should remain **soft quality signals** unless severe failure is obvious.

AI-generated music can legitimately be unconventional.

The system must not reject experimental music simply because it deviates from common arrangements.

---

# 26. Vocal-quality evaluation

For vocal tracks where applicable, inspect:

```text
vocal intelligibility
syllable discontinuity
phoneme glitches
pitch instability
unintended double voices
voice timbre instability
abrupt identity shifts
```

If the track is instrumental:

```text
vocal_checks = not_applicable
```

Do not treat not-applicable as failed.

---

# 27. Lyrics extraction

If P4 has supplied lyrics, compare:

```text
stored intended lyrics
provider-returned lyrics if any
transcribed lyrics
```

Flag significant divergence.

Example:

```text
expected: 87% semantic/word alignment
actual: 52%
```

Thresholds must be configurable and language-aware.

---

# 28. Lyrics language consistency

Detect likely language and compare with P3 requested language.

Possible results:

```text
matches
mixed
unknown
mismatch
```

Mixed-language content is not inherently bad.

Only treat mismatch as material when the experiment requires a specific language.

---

# 29. Lyrics safety and policy screening

Screen lyrics for configurable internal policy categories.

At minimum identify:

```text
explicit sexual content
excessive graphic violence
targeted harassment
hate/slur risk
self-harm encouragement
illegal activity instructions
impersonation/deception risk
```

This is not a general moral judgement.

The objective is to prevent accidental publication of content incompatible with platform, advertiser or brand rules.

Use deterministic keyword/rule scanning plus an LLM/classifier for contextual interpretation.

The LLM output must be treated as a classification signal, not as an authoritative legal/policy verdict.

---

# 30. Lyrics similarity screening

Compare lyrics against a protected/reference-risk corpus that the system is authorized to use.

Detection layers:

```text
exact phrase matching
n-gram similarity
semantic similarity
sequence alignment
```

Do not use a single percentage as a legal conclusion.

Example classification:

```text
low similarity
medium similarity
high similarity
```

High similarity may trigger quarantine/rejection according to policy.

---

# 31. Prompt and metadata risk screening

Inspect:

```text
music prompt
lyrics prompt
negative prompt
reference text
seed notes
track title
description
artist identity
```

Flag protected-name references such as:

```text
specific artist names
specific track names
specific copyrighted lyrics
voice-cloning requests
```

The system should distinguish:

```text
reference present in user-authored/internal creative brief
```

from:

```text
reference automatically injected by a provider template
```

Both should be retained for audit, but the risk interpretation may differ.

---

# 32. Voice-cloning risk

The system should detect attempts to reproduce a named or identifiable real person's voice where authorization is not documented.

Required input fields:

```text
voice_identity_source
voice_authorization_status
voice_model_provider
voice_model_license
```

Possible states:

```text
none
synthetic_generic
own_voice_authorized
third_party_authorized
third_party_unauthorized
unknown
```

If `third_party_unauthorized` or a high-confidence unknown real-person voice risk is detected, quarantine.

Do not infer a person's identity solely from weak voice similarity.

---

# 33. Provider license snapshot

At candidate generation time, P4 should have stored the provider/license snapshot.

P5 must verify that snapshot contains, where applicable:

```text
provider
plan/tier
account identity reference
creation timestamp
commercial-use terms version
download timestamp
artifact association
```

For provider-specific systems such as Suno, commercial-use rights must be tied to the correct subscription/creation/download conditions rather than assumed from current account status. Current Suno documentation states that songs downloaded while subscribed to a paid plan receive commercial-use rights and that commercial rights do not guarantee copyright protection. ([Suno Help](https://help.suno.com/en/articles/9601665))

---

# 34. Commercial-use eligibility model

Use explicit values:

```text
eligible
ineligible
unknown
conditional
expired_or_unverified
```

Never collapse them to Boolean.

Example:

```text
eligible
```
means evidence exists under the current policy snapshot.

```text
conditional
```
means additional action/evidence is required.

---

# 35. Copyright protection status

Separate from commercial-use rights:

```text
unknown
potentially_protectable
human_contribution_documented
jurisdiction_review_required
```

P5 should not claim a definitive copyright status without authoritative legal/jurisdiction-specific evidence.

The system's business decision may still proceed where commercial-use rights are valid and policy permits, while recording that copyright protection is uncertain.

---

# 36. Provenance completeness

P5 should calculate:

```text
provenance_complete = true/false
```

Only set `true` when all mandatory evidence for the relevant provider/path exists.

Minimum expected lineage:

```text
P3 track spec
 ↓
P4 generation plan
 ↓
generation attempt
 ↓
provider/model/version
 ↓
prompt/input snapshot
 ↓
license snapshot
 ↓
output artifact
```

Missing any required link should produce a finding.

---

# 37. Provenance levels

Use:

```text
COMPLETE
SUFFICIENT
PARTIAL
MISSING
CONFLICTING
```

`SUFFICIENT` is useful for cases where a required record exists but some optional metadata is unavailable.

Do not reject solely because optional provenance metadata is absent.

---

# 38. Artifact lineage

A transformed artifact should link to its parent.

Example:

```text
raw Suno output
      ↓
trimmed master
      ↓
normalized master
      ↓
P6 video audio
```

Each node needs:

```text
artifact_id
parent_artifact_id
transform_type
transform_tool
transform_version
transform_timestamp
operator
```

---

# 39. Duplicate detection

Detect exact duplicates using SHA-256.

If the same bytes appear under another candidate ID:

```text
EXACT_DUPLICATE
```

Do not automatically delete either record.

Preserve both records, identify the canonical artifact, and mark the duplicate relationship.

---

# 40. Near-duplicate detection

Use audio fingerprints/embeddings to detect:

```text
same track with minor encoding changes
same track with silence trimmed
same track with loudness adjustment
near-identical generated variants
```

Similarity thresholds should be configuration-driven.

Example states:

```text
distinct
related_variant
near_duplicate
duplicate
```

Do not use one threshold across all genres and durations without validation.

---

# 41. Cross-catalog similarity

Compare new candidates against:

```text
internal released catalog
internal rejected catalog
candidate bank
licensed reference-risk corpus
```

The purpose is:

1. prevent accidentally releasing the same song repeatedly;
2. identify internal track reuse;
3. provide a rights-risk signal where authorized reference corpora exist.

It is not intended to reconstruct copyrighted songs.

---

# 42. External similarity strategy

The system may use locally computed embeddings, fingerprints or commercially licensed similarity services.

Do not automatically upload full private/unreleased tracks to an external provider unless:

```text
privacy terms allow it
commercial terms allow it
user/business policy allows it
```

The default should be local analysis for unreleased material.

---

# 43. Similarity false-positive policy

Similarity is a risk signal, not proof.

A finding should include:

```text
method
model_version
threshold_version
similarity_score
confidence
matched_reference_id
segment/timestamp if applicable
```

Potential false positive example:

```text
common four-chord progression
```

should not necessarily trigger a hard reject.

The system should prioritize distinctive melodic/lyrical/audio evidence over generic genre characteristics.

---

# 44. Policy engine

Implement a policy engine that consumes findings and outputs gate requirements.

Conceptually:

```text
Findings
   ↓
Policy Rules
   ↓
Gate requirements
   ↓
Disposition
```

Rules should have:

```text
rule_id
rule_version
condition
severity
action
effective_from
effective_to
scope
```

Actions:

```text
PASS
WARN
QUARANTINE
REJECT
REQUEST_RECHECK
```

---

# 45. Severity model

Use four severity levels:

```text
INFO
LOW
MEDIUM
HIGH
CRITICAL
```

Suggested semantics:

### INFO
Useful metadata or non-problem signal.

### LOW
Minor quality issue; not normally blocking.

### MEDIUM
May matter for release quality; policy-dependent.

### HIGH
Material risk or substantial quality defect; normally blocks until resolved.

### CRITICAL
Known disqualifying condition under current policy.

---

# 46. Gate types

P5 should maintain separate gate categories:

```text
TECHNICAL_GATE
QUALITY_GATE
PROVENANCE_GATE
COMMERCIAL_USE_GATE
RIGHTS_RISK_GATE
POLICY_GATE
EXPERIMENT_INTEGRITY_GATE
```

This makes downstream debugging much easier.

---

# 47. Hard release gates

Default hard gates should include conditions such as:

```text
artifact unreadable
artifact checksum mismatch
material truncation
commercial use explicitly ineligible
provenance materially missing
unauthorized third-party voice cloning detected
critical policy violation
high-confidence protected lyric reuse
```

The exact rule set must be configurable and versioned.

---

# 48. Soft release gates

Soft signals should include:

```text
minor clipping
moderate loudness variance
moderate vocal artifact score
ambiguous language mismatch
moderate structural repetition
uncertain similarity
creative unconventionality
```

They may yield:

```text
PASSED_WITH_WARNINGS
```

rather than automatic rejection.

---

# 49. Quarantine semantics

Quarantine means:

> The candidate is not cleared for downstream publication/distribution, but the evidence does not justify permanent rejection.

Examples:

```text
license evidence missing
provider API did not return commercial-use status
moderate similarity requiring review
voice identity uncertain
policy classification uncertain
technical issue may be repairable
```

A quarantined candidate remains in the system and can be re-evaluated.

---

# 50. Rejection semantics

Reject when:

```text
known hard failure
irreparable technical corruption
explicitly ineligible license
critical policy issue
high-confidence rights-risk condition under internal policy
```

Rejected candidates should remain immutable records because they are valuable for training QA classifiers and preventing repeated failures.

Do not physically delete them by default.

---

# 51. Candidate disposition

Use:

```text
PASSED
PASSED_WITH_WARNINGS
QUARANTINED
REJECTED
```

Recommended semantics:

### PASSED
No blocking findings.

### PASSED_WITH_WARNINGS
No blocking findings, but warnings are retained.

### QUARANTINED
Insufficient evidence or unresolved medium/high-risk finding.

### REJECTED
Known blocking failure under the current policy version.

---

# 52. P5 quality scoring

Do not collapse all checks into a single scalar.

Expose category scores:

```text
technical_quality_score
musical_quality_score
vocal_quality_score if applicable
lyric_quality_score if applicable
provenance_score
rights_confidence_score
policy_compliance_score
```

An optional composite quality index can exist for ranking candidates inside P6/P4 refinement, but it must never override hard gates.

---

# 53. Composite quality index

If implemented, use a configurable weighted model:

```text
25% technical
25% musical
15% vocal/lyric where applicable
15% structural consistency
10% artifact cleanliness
10% metadata/provenance quality
```

These weights are starting defaults, not universal truths.

Do not use the composite score as a predicted market-performance measure.

---

# 54. Explainability requirement

Every composite score must be decomposable.

Example:

```text
quality_index = 78.3

technical = 94
musical = 76
vocal = 71
structure = 82
artifact_cleanliness = 80
provenance = 100
```

The system must be able to produce the above breakdown from stored evidence.

---

# 55. Confidence vs score

Maintain confidence separately.

Example:

```text
musical_quality_score = 84
musical_quality_confidence = 0.58
```

This means the model believes the result is promising, but evidence quality is limited.

Never convert uncertainty into artificial precision.

---

# 56. Evaluation model selection

P5 may use:

```text
DSP algorithms
ML audio-quality models
embedding models
speech/lyrics models
LLM classifiers
rule engines
```

Use the cheapest reliable detector for each task.

A strong LLM should not be called to compute LUFS.

A DSP library should not be expected to understand lyric-context safety.

---

# 57. LLM responsibilities

LLMs may:

- classify lyrics context;
- summarize anomalies;
- classify genre-specific structural coherence;
- interpret ambiguous metadata;
- propose human-review explanations;
- normalize multilingual terminology;
- identify possible prompt-risk references.

LLMs must not:

- invent license facts;
- claim legal ownership;
- declare a song copyright-safe;
- replace deterministic file-integrity checks;
- silently modify evidence;
- overwrite provider terms;
- convert uncertain similarity into a definitive infringement finding.

---

# 58. LLM structured-output contract

Any LLM-based check must return a typed schema such as:

```json
{
  "classification": "medium_risk",
  "confidence": 0.81,
  "reason_codes": [
    "POSSIBLE_PROTECTED_REFERENCE"
  ],
  "evidence_spans": [
    {
      "text": "...",
      "start": 104,
      "end": 126
    }
  ],
  "model_version": "...",
  "prompt_version": "..."
}
```

Validate before persistence.

---

# 59. Evidence hierarchy

When facts conflict, prefer stronger evidence in this order:

```text
1. Original stored provider/license record
2. Provider contractual/licensing metadata
3. Immutable P4 provenance record
4. Deterministic artifact measurement
5. Authorized external service result
6. Model classification
7. LLM interpretation
```

Do not let an LLM override an actual provider license record.

---

# 60. Rights evidence matrix

For each candidate, create a matrix similar to:

| Evidence | Status | Source | Required? |
|---|---|---|---|
| Provider identity | verified | P4 | yes |
| Model/version | verified | P4 | yes |
| Creation timestamp | verified | P4 | yes |
| Commercial-use terms | verified/unknown | provider snapshot | yes |
| Download/subscription condition | verified/unknown | provider record | yes |
| Human contribution | documented/unknown | P3/P4 | policy dependent |
| Voice authorization | n/a/verified/unknown | P3/P4 | if vocal identity relevant |
| External similarity scan | passed/flagged/not-run | P5 | policy dependent |
| Platform check | pending/passed/claimed | downstream | later |

The downstream system should be able to see why a candidate is cleared or blocked.

---

# 61. Platform policy vs business policy

Maintain separate rule namespaces:

```text
PLATFORM_YOUTUBE
PLATFORM_DSP
PROVIDER_SUNO
BUSINESS_INTERNAL
BRAND
EXPERIMENT
```

This prevents accidental conflation.

Example:

```text
commercially licensed = true
```

does not mean:

```text
YouTube monetization guaranteed = true
```

And:

```text
copyright similarity low
```

does not mean:

```text
no future claim possible
```

---

# 62. Experiment-integrity checks

Because P5 sits between production and publishing, it must verify that the candidate still corresponds to the P3 experiment.

Check:

```text
track_spec_id matches
experiment_id matches
content_arm_id matches
prompt fingerprint within allowed variation
candidate role matches
language constraint matches
duration class matches
required vocal mode matches
```

Do not reject harmless creative deviations that P3 explicitly marked as flexible.

---

# 63. Hard vs flexible P3 constraints

P5 must read the P3 content package's constraint classification.

If P3 says:

```text
language = HARD
visual = CREATIVE_FREEDOM
BPM = SOFT
```

then:

```text
wrong language → gate
visual variation → allowed
BPM deviation → warning unless extreme
```

P5 must not invent constraints that were never specified by P3.

---

# 64. Track role validation

P3 may assign a role:

```text
opener
mid-energy
emotional_peak
cooldown
closer
instrumental_bridge
```

P5 may check whether the candidate approximately matches its assigned role.

Do not hard-fail merely because a track is creatively surprising unless the role was a hard requirement.

---

# 65. Duration validation against P3

Use P3 duration semantics.

Example:

```text
requested bucket = 3–4 min
actual = 3:52
pass
```

Do not enforce an exact duration if P3 defined a range/bucket.

For extension candidates, compare intended structure rather than simply rejecting tracks outside a single value.

---

# 66. Candidate-family diversity

P5 should provide diversity signals but should not perform P2's allocation.

Examples:

```text
same_artifact_as_existing = true
same_near_duplicate_family = true
same_prompt_family = true
same_audio_embedding_cluster = true
```

P6/P2 can use these signals to avoid publishing interchangeable tracks.

---

# 67. Catalog repetition risk

Calculate a candidate's similarity to recent internally released tracks.

If similarity exceeds a configurable threshold:

```text
CATALOG_NEAR_DUPLICATE
```

The result can be:

```text
warning
quarantine
reject
```

depending on channel/catalog strategy.

Do not automatically reject all same-genre tracks.

Genre recurrence is expected; near-identical audio is not.

---

# 68. Creative-quality review queue

Some candidates may pass hard gates but score poorly on creative quality.

P5 should return:

```text
PASSED_WITH_WARNINGS
```

with findings such as:

```text
weak_intro
long_unresolved_section
vocal_artifact
low_structural_coherence
```

P4 may later use the findings to request refinement.

P5 itself should not automatically trigger infinite regeneration loops.

---

# 69. Repairability classification

For each high-severity quality issue, estimate:

```text
repairable_by_trim
repairable_by_refinement
repairable_by_remaster
not_repairable
unknown
```

This is useful downstream.

It is not an instruction to automatically repair everything.

---

# 70. Refinement recommendation

P5 may provide a structured recommendation:

```json
{
  "action": "REFINE",
  "reason_codes": [
    "VOCAL_ARTIFACT",
    "LONG_INTRO"
  ],
  "target_sections": [
    {"start": 0, "end": 18},
    {"start": 72, "end": 74}
  ],
  "confidence": 0.82
}
```

P4 can consume this through a separate refinement path.

P5 must not perform the refinement itself.

---

# 71. Review queue model

Create a review queue for:

```text
rights ambiguity
license ambiguity
voice identity ambiguity
high similarity
policy ambiguity
technical anomaly requiring human hearing
```

Each review case contains:

```text
case_id
candidate_id
priority
findings
evidence
recommended_action
created_at
expires_at
```

---

# 72. Human-review priority

Suggested order:

```text
CRITICAL rights/policy
HIGH rights/policy
HIGH technical/audio
commercial license unknown
voice identity uncertain
medium similarity
quality-only warning
```

The exact ordering must be configurable.

---

# 73. Human review result model

Allowed results:

```text
APPROVE
REJECT
REQUEST_REFINEMENT
REQUEST_MORE_EVIDENCE
ESCALATE
```

Human review must not overwrite the automated findings.

Instead append a human decision event.

---

# 74. Human-review audit

Record:

```text
reviewer_identity
review_time
decision
reason
optional_comment
policy_version
```

Do not record secrets or irrelevant personal data.

---

# 75. Re-evaluation semantics

A candidate can be re-evaluated after:

```text
license metadata updated
policy version changed
new similarity detector deployed
P4 refinement created
artifact transformed
new catalog reference added
```

Every re-evaluation gets a new evaluation ID.

The old decision remains immutable.

---

# 76. Policy-change impact analysis

When a new policy version is activated, support a job:

```text
find affected candidates
→ re-evaluate
→ identify newly blocked assets
→ identify newly eligible assets
→ emit change report
```

Do not blindly modify historical decision records.

---

# 77. Release Manifest

A passed candidate must produce a downstream manifest:

```json
{
  "release_manifest_version": "1",
  "candidate_id": "...",
  "canonical_artifact_id": "...",
  "experiment_id": "...",
  "track_spec_id": "...",
  "disposition": "PASSED_WITH_WARNINGS",
  "technical_status": "passed",
  "commercial_use_status": "eligible",
  "provenance_status": "complete",
  "ai_disclosure_required": true,
  "rights_risk": "low",
  "policy_status": "passed",
  "evaluation_id": "...",
  "evaluation_policy_version": "p5-v1",
  "warning_codes": [],
  "evidence_bundle_id": "..."
}
```

P6 must not have to query low-level P5 tables to understand whether a candidate is usable.

---

# 78. Downstream gating contract

P6 may consume only:

```text
PASSED
PASSED_WITH_WARNINGS
```

provided its own downstream policy permits warnings.

P6 must not consume:

```text
QUARANTINED
REJECTED
```

without an explicit override mechanism.

---

# 79. Override mechanism

An override should exist only as an explicit, auditable action.

Required fields:

```text
override_id
candidate_id
original_gate_decision
new_decision
reason
approver
policy_scope
expires_at
```

Do not let developers add a hidden `force=true` path that bypasses audit.

---

# 80. Release eligibility vs platform publication eligibility

P5's output should mean:

> **This candidate has passed our internal content-quality, provenance and rights/policy screening under the current policy snapshot.**

It must not mean:

> **YouTube or Spotify will definitely accept or monetize it.**

Platform-level checks remain downstream and can produce new claims/rejections later.

---

# 81. Database schema

Recommended tables:

```text
candidate_evaluations
evaluation_snapshots
technical_checks
musical_checks
lyric_checks
provenance_checks
rights_checks
policy_checks
risk_findings
evidence_items
gate_decisions
quarantine_cases
review_cases
review_decisions
release_manifests
similarity_matches
artifact_quality_metrics
policy_rules
policy_versions
classifier_versions
catalog_fingerprints
catalog_similarity_matches
```

---

# 82. `candidate_evaluations`

Fields:

```text
id
candidate_id
artifact_id
evaluation_policy_version
trigger
started_at
finished_at
status
technical_status
quality_status
provenance_status
rights_status
policy_status
disposition
composite_quality_score
composite_quality_confidence
created_at
```

---

# 83. `evaluation_snapshots`

Fields:

```text
id
evaluation_id
candidate_snapshot_json
artifact_snapshot_json
provider_snapshot_json
license_snapshot_json
p3_snapshot_ref
p4_snapshot_ref
policy_snapshot_ref
catalog_snapshot_ref
snapshot_hash
created_at
```

---

# 84. `technical_checks`

Fields:

```text
id
evaluation_id
check_type
status
severity
measured_value
expected_value
unit
start_seconds
end_seconds
tool_name
tool_version
config_version
created_at
```

---

# 85. `musical_checks`

Fields:

```text
id
evaluation_id
check_type
score
confidence
severity
method
model_version
segment_json
created_at
```

---

# 86. `rights_checks`

Fields:

```text
id
evaluation_id
check_type
status
risk_level
confidence
source_type
source_ref
matched_reference_id
similarity_score
evidence_json
created_at
```

---

# 87. `policy_checks`

Fields:

```text
id
evaluation_id
policy_namespace
rule_id
rule_version
status
severity
evidence_json
created_at
```

---

# 88. `risk_findings`

Fields:

```text
id
evaluation_id
finding_code
category
severity
status
confidence
message
start_seconds
end_seconds
evidence_item_ids
blocking
repairability
created_at
```

---

# 89. `evidence_items`

Fields:

```text
id
evaluation_id
evidence_type
source
source_reference
content_hash
summary
raw_pointer if appropriate
created_at
```

Do not dump secrets or access tokens into evidence.

---

# 90. `gate_decisions`

Fields:

```text
id
evaluation_id
disposition
gate_results_json
blocking_finding_ids
warning_finding_ids
decision_version
decision_hash
created_at
```

The decision must be reproducible from the evaluation snapshot + policy version.

---

# 91. `quarantine_cases`

Fields:

```text
id
candidate_id
evaluation_id
reason_codes
priority
status
assigned_to
created_at
resolved_at
resolution
```

---

# 92. `release_manifests`

Fields:

```text
id
evaluation_id
candidate_id
artifact_id
manifest_version
manifest_json
manifest_hash
created_at
```

---

# 93. Database invariants

Must enforce where practical:

1. One evaluation ID maps to one candidate/artifact snapshot.
2. Evaluation results are immutable after completion.
3. Gate decision references the exact evaluation version.
4. A release manifest can only be created for a passed decision unless explicitly overridden.
5. Quarantined/rejected candidates cannot silently appear in the normal release queue.
6. Every finding belongs to an evaluation.
7. Every evidence item is attributable to an evaluation.
8. Artifact hashes are immutable.
9. Policy version is never null on final evaluation.
10. AI disclosure requirement is never omitted when AI-generated music is identified.

---

# 94. API surface

Implement an internal read/write API.

### `POST /qa/evaluations`

Starts an evaluation.

### `GET /qa/evaluations/{id}`

Returns evaluation summary.

### `GET /qa/evaluations/{id}/findings`

Returns all findings/evidence references.

### `GET /qa/candidates/{id}/status`

Returns current effective status.

### `POST /qa/candidates/{id}/recheck`

Requests a new evaluation.

### `GET /qa/review-queue`

Returns unresolved review cases.

### `POST /qa/review-cases/{id}/decision`

Records an authorized human review decision.

### `GET /qa/release-manifests/{candidate_id}`

Returns the latest eligible release manifest.

---

# 95. Event model

P5 should emit versioned events:

```text
music_candidate_ingested_for_p5
music_candidate_evaluation_started
music_candidate_evaluation_completed
music_candidate_quarantined
music_candidate_rejected
music_candidate_cleared
music_candidate_recheck_requested
music_candidate_policy_changed
music_candidate_license_updated
music_candidate_release_manifest_created
music_candidate_human_review_required
```

Events must be:

```text
append-only
versioned
idempotent
traceable
```

---

# 96. Idempotency

If the same P4 event is delivered twice:

```text
same candidate_id
same artifact_id
same evaluation policy
same trigger
same source event ID
```

P5 must not create two active evaluations accidentally.

Use an idempotency key derived from the source event and requested policy/version.

A deliberate recheck must always receive a new evaluation ID.

---

# 97. Concurrency

Prevent concurrent evaluations from racing over the same candidate.

Recommended rule:

```text
one active evaluation per candidate + policy version
```

A separate evaluation can run after the first completes or when an explicit supersession policy is used.

---

# 98. Worker architecture

Use specialized workers:

```text
TechnicalQAWorker
AudioAnalysisWorker
LyricsWorker
ProvenanceWorker
RightsWorker
PolicyWorker
AggregationWorker
EvidenceWorker
```

They should exchange structured results, not untyped free-form strings.

---

# 99. Durable workflow

Use a durable workflow engine consistent with the rest of the project.

A conceptual workflow:

```text
load candidate
 ↓
create snapshot
 ↓
run independent technical/audio checks in parallel
 ↓
run lyric checks if applicable
 ↓
run provenance/license checks
 ↓
run similarity/risk checks
 ↓
aggregate findings
 ↓
apply policy
 ↓
store decision
 ↓
create release manifest or quarantine case
 ↓
emit event
```

A failure in one non-critical detector should not destroy the entire workflow.

Instead classify:

```text
detector unavailable
```

and let policy decide whether that makes the evaluation incomplete or blocking.

---

# 100. Failure handling

Error classes:

```text
ARTIFACT_MISSING
ARTIFACT_CORRUPT
DECODER_FAILURE
MODEL_UNAVAILABLE
MODEL_TIMEOUT
LICENSE_LOOKUP_FAILED
PROVIDER_METADATA_MISSING
SIMILARITY_SERVICE_UNAVAILABLE
POLICY_CONFIG_ERROR
DATABASE_ERROR
UPSTREAM_EVENT_INVALID
```

Retry transient failures.

Quarantine when a required check cannot be completed and policy requires it.

Never silently convert a failed rights check into `pass`.

---

# 101. Detector availability semantics

Each check should return one of:

```text
PASS
WARN
FAIL
UNKNOWN
NOT_APPLICABLE
ERROR
```

These are not equivalent.

Example:

```text
voice_similarity = NOT_APPLICABLE
```

is fine for an instrumental track.

```text
license_verification = ERROR
```

may block if commercial-use evidence is mandatory.

---

# 102. Graceful degradation

P5 should degrade only according to policy.

Example:

```text
spectral_detector unavailable
```

may be non-blocking.

But:

```text
commercial-use license verification unavailable
```

may be blocking.

This must be represented in policy configuration rather than buried in worker code.

---

# 103. Timeout policy

Each detector gets:

```text
default timeout
maximum timeout
retry count
fallback behavior
```

Large audio files should not hold one worker indefinitely.

---

# 104. Resource management

Limit:

```text
CPU concurrency
GPU concurrency
memory usage
file descriptor count
storage reads
external API requests
```

Prefer local analysis for large unreleased artifacts when practical.

---

# 105. Temporary file security

Downloaded/generated analysis files should:

```text
use random names
be stored outside public directories
have restrictive permissions
be deleted after retention period
```

Do not derive filesystem paths directly from user/provider-supplied names.

---

# 106. Artifact security

Before processing any artifact:

```text
verify path is expected
verify file size within limits
verify extension is not trusted blindly
inspect MIME/container
validate decoder
reject unexpected file types
```

Avoid shell-command injection through filenames or metadata.

---

# 107. External service privacy

Before sending audio/lyrics to third-party analyzers:

1. verify terms permit the use;
2. verify business/privacy policy permits the transfer;
3. record the provider;
4. store a service/version identifier;
5. record that an external processor was used.

Do not silently send unreleased tracks to arbitrary public services.

---

# 108. Logging requirements

Every evaluation log should include:

```text
evaluation_id
candidate_id
artifact_id
workflow_id
worker
check_type
policy_version
model_version
status
latency
cost if available
```

Never log:

```text
API secrets
OAuth tokens
full private credentials
```

Avoid logging full lyrics/audio content unless explicitly necessary.

---

# 109. Observability metrics

Expose:

```text
evaluations_total
evaluations_passed_total
evaluations_warned_total
evaluations_quarantined_total
evaluations_rejected_total
technical_failure_rate
rights_unknown_rate
provenance_incomplete_rate
similarity_flag_rate
human_review_rate
detector_error_rate
detector_latency
average_evaluation_cost
GPU_hours
storage_read_bytes
```

Track these by provider/model/version.

---

# 110. QA drift monitoring

P5 classifiers themselves can drift.

Track:

```text
rejection_rate by detector version
false-positive review rate
human override rate
provider/model
language
genre
```

If a detector suddenly flags 40% of all tracks after a model update, alert and optionally disable autonomous hard-gating for that detector.

---

# 111. Detector calibration

Where labeled data becomes available, maintain calibration sets for:

```text
clipping
vocal artifacts
lyric similarity
near-duplicate detection
policy classification
```

Do not tune thresholds only on production outcomes without keeping a stable validation set.

---

# 112. Golden evaluation dataset

Create an internal fixture library containing:

```text
clean instrumental
clean vocal
intentional ambient silence
intentional chorus repetition
mild clipping
severe clipping
phase issue
long intro
vocal glitch
lyric mismatch
exact duplicate
near duplicate
unrelated song
known high-risk lyric fixture
missing provenance
valid commercial-license fixture
unknown license fixture
```

This dataset becomes the regression suite for P5.

---

# 113. Property tests

Required properties include:

```text
artifact hash is deterministic
re-running same analysis on same bytes gives same deterministic measurements
score remains within expected ranges
hard gate cannot be overridden by a soft warning
NOT_APPLICABLE does not become FAIL
UNKNOWN does not become PASS unless explicit policy permits it
rejected candidates remain auditable
policy version changes create a new evaluation
```

---

# 114. Similarity property tests

Test:

```text
same file → similarity = duplicate
same file re-encoded → near-duplicate
slightly trimmed file → near-duplicate
completely different file → not duplicate
same four-chord genre motif → not automatically duplicate
```

The thresholds should be tuned using golden fixtures, not intuition.

---

# 115. Integration test scenario — clean candidate

Given a P4 candidate with:

```text
valid WAV
complete provenance
commercial-use eligible
no prohibited prompt references
no known similarity
clean lyrics
```

Expected:

```text
technical = PASS
provenance = PASS
rights = PASS/LOW_RISK
policy = PASS
disposition = PASSED or PASSED_WITH_WARNINGS
release_manifest = created
```

---

# 116. Integration test scenario — corrupt candidate

Given:

```text
truncated WAV
```

Expected:

```text
technical = FAIL
blocking finding = MATERIAL_CORRUPTION
disposition = REJECTED
no release manifest
```

---

# 117. Integration test scenario — unknown commercial rights

Given:

```text
artifact valid
provenance partial
commercial use = unknown
```

Expected under default policy:

```text
rights = UNKNOWN
provenance = PARTIAL
disposition = QUARANTINED
```

unless an explicit provider policy says the candidate is eligible.

---

# 118. Integration test scenario — AI disclosure

Given:

```text
AI-generated music = true
```

Expected:

```text
ai_disclosure_required = true
```

This should not itself create a failure.

YouTube currently explicitly identifies AI-generated music as content requiring disclosure. ([YouTube Help](https://support.google.com/youtube/answer/14328491?hl=en))

---

# 119. Integration test scenario — high similarity

Given:

```text
high-confidence distinctive melodic/lyric similarity
```

Expected under default conservative rights policy:

```text
rights_risk = HIGH
blocking = true
disposition = QUARANTINED or REJECTED
```

The exact choice should depend on whether human review may resolve it.

---

# 120. Integration test scenario — generic genre similarity

Given:

```text
same BPM
same generic chord progression
same broad genre
```

Expected:

```text
not a hard copyright-reuse finding
```

This prevents over-rejection of normal genre conventions.

---

# 121. Integration test scenario — unauthorized voice risk

Given:

```text
third-party named voice target
no authorization record
```

Expected:

```text
rights/policy finding = HIGH
human review or rejection
no automatic downstream release
```

---

# 122. Integration test scenario — warning only

Given:

```text
mild loudness variance
minor intro silence
```

Expected:

```text
no hard gate
PASSED_WITH_WARNINGS
release manifest created if downstream policy allows warnings
```

---

# 123. Integration test scenario — detector unavailable

Given:

```text
optional artifact detector unavailable
```

Expected:

```text
detector status = ERROR
policy determines whether warning or quarantine
```

Never silently mark the failed detector as PASS.

---

# 124. Policy matrix testing

For each policy rule, build tests for:

```text
positive match
negative match
boundary value
unknown
missing evidence
policy version change
```

---

# 125. Human override test

Verify:

```text
automatic decision = QUARANTINED
human APPROVE
new effective status = CLEARED_BY_OVERRIDE
original automated finding retained
review identity retained
reason retained
```

The system must not rewrite the original evaluation.

---

# 126. Re-evaluation test

Scenario:

```text
v1 → license unknown → QUARANTINED
v2 → license evidence added → recheck
```

Expected:

```text
v1 evaluation remains unchanged
v2 evaluation references v1 lineage
v2 may become PASSED
```

---

# 127. Crash recovery test

Kill the worker:

```text
after technical checks
before aggregation
```

Restart.

Expected:

```text
completed detector results retained
unfinished work resumed or safely retried
no duplicate findings
single final decision
```

---

# 128. Idempotency test

Deliver the same P4 event 5 times.

Expected:

```text
one active evaluation
no duplicate release manifests
no duplicate review cases
```

---

# 129. Performance targets

P5 must define measurable operational targets but should not optimize premature microseconds.

Suggested initial targets for a standard 3–5 minute track on local infrastructure:

```text
technical checks: seconds
local DSP analysis: seconds
embedding analysis: seconds to low tens of seconds
total automated evaluation: target <2 minutes median
```

These are operational targets, not hard acceptance thresholds; hardware and model selection can change them.

Batch mode should prioritize throughput rather than interactive latency.

---

# 130. Cost accounting

Track per evaluation:

```text
CPU_seconds
GPU_seconds
external_API_cost
LLM_tokens
LLM_cost_estimate
storage_read_bytes
storage_write_bytes
```

Every cost must be associated with:

```text
evaluation_id
candidate_id
```

P9 can later include QA cost in unit economics.

---

# 131. Cost-aware detector routing

Use a tiered model:

```text
Tier 1 — cheap deterministic checks
Tier 2 — local DSP/embedding checks
Tier 3 — local ML quality models
Tier 4 — strong LLM/external analyzer for ambiguous cases
Tier 5 — human review
```

Do not run Tier 4 on every candidate by default.

---

# 132. Progressive evaluation

Recommended flow:

```text
cheap hard-failure checks
        ↓
technical quality
        ↓
cheap similarity
        ↓
rights/provenance
        ↓
advanced quality
        ↓
LLM contextual checks only if needed
```

A corrupted file should never consume expensive model inference.

---

# 133. Early termination

P5 may stop expensive evaluation if a definitive hard failure occurs.

Example:

```text
artifact unreadable
```

No reason to spend compute on lyric-quality inference.

However, preserve enough evidence to explain the rejection.

---

# 134. Evidence bundle

For every completed evaluation, build a compact evidence bundle containing:

```text
candidate metadata
artifact hash
technical measurements
quality scores
rights findings
license snapshot reference
provenance references
policy findings
final decision
model versions
policy version
```

Do not duplicate huge audio files into the evidence bundle.

Reference the canonical artifact.

---

# 135. Human-readable QA report

Generate a report such as:

```text
Track: After Midnight
Candidate: cand-123
Evaluation: eval-456

Disposition: PASSED_WITH_WARNINGS

Technical
✓ File integrity
✓ 44.1 kHz stereo
✓ Duration 3:18
✓ No material clipping
⚠ True peak above preferred profile

Musical
✓ Structure coherent
⚠ Vocal artifact detected at 02:11–02:13

Rights
✓ Commercial-use evidence present
✓ Provenance complete
⚠ Copyright protection status: unknown/jurisdiction-dependent

Policy
✓ No critical policy findings

AI disclosure
REQUIRED
```

The report must be generated from structured records rather than manually assembled free text.

---

# 136. Machine-readable report

Also generate JSON for downstream agents.

Example:

```json
{
  "candidate_id": "cand-123",
  "evaluation_id": "eval-456",
  "disposition": "PASSED_WITH_WARNINGS",
  "blocking": [],
  "warnings": [
    {
      "code": "VOCAL_ARTIFACT",
      "severity": "MEDIUM",
      "confidence": 0.84
    }
  ],
  "commercial_use": "eligible",
  "provenance": "complete",
  "ai_disclosure_required": true,
  "manifest_id": "manifest-789"
}
```

---

# 137. P5-to-P4 refinement interface

P5 should expose:

```http
POST /qa/candidates/{candidate_id}/refinement-request
```

Body:

```json
{
  "requested_action": "REFINE",
  "reason_codes": [
    "VOCAL_ARTIFACT"
  ],
  "priority": "normal",
  "evaluation_id": "eval-456"
}
```

P4 decides whether and how to generate a new revision.

P5 does not call the music provider directly.

---

# 138. P5-to-P6 interface

P6 receives only the release manifest and the approved artifacts.

Minimum fields:

```text
candidate_id
artifact_id
track_spec_id
experiment_id
content_arm_id
disposition
warnings
ai_disclosure_required
rights_status
provenance_status
evidence_bundle_id
```

---

# 139. P5-to-P7/P8 implications

The manifest must expose:

```text
AI disclosure requirement
commercial-use eligibility
license evidence reference
provenance reference
artist identity reference
ISRC/UPC if already assigned later
policy warnings
```

P7/P8 remain responsible for platform-specific checks.

---

# 140. Content-ID / platform-claim boundary

P5 does not create or manage Content ID claims.

After distribution/publication, later systems may receive claims.

Those claims should be represented as downstream events that can trigger:

```text
catalog quarantine
re-evaluation
human review
future release suppression
```

A future platform claim must not retroactively rewrite the historical P5 evaluation.

---

# 141. YouTube-specific downstream warning

YouTube's own copyright checks are not final; later claims or removals can still occur. ([YouTube Help](https://support.google.com/youtube/answer/57407?hl=en))

Therefore the release manifest should use wording/state such as:

```text
internal_rights_screening = passed
platform_check_status = pending
```

not:

```text
youtube_copyright_clearance = guaranteed
```

---

# 142. AI disclosure metadata

P5 must output a boolean/enum:

```text
ai_disclosure_required
```

Recommended values:

```text
required
not_required
unknown
```

For known AI-generated music under the current YouTube policy, use:

```text
required
```

This should be passed intact to the publisher.

---

# 143. Brand-quality checks

P5 may verify metadata-level brand consistency:

```text
artist naming policy
explicit-content labeling
brand language
required disclaimer
naming conventions
```

Do not duplicate all visual-brand checks from P6 here.

P5 can validate the existence and schema of a brand profile reference, while P6 should evaluate the actual rendered visual.

---

# 144. Anti-template signal

Because YouTube's monetization policy explicitly identifies mass-produced/repetitive content as a potential problem, P5 should expose reusable signals for P6/P7:

```text
audio_uniqueness_score
candidate_family_repetition
metadata_template_repetition
```

P5 should not make a channel-level YPP determination.

---

# 145. Catalog health signal

Provide aggregate P5 statistics to later systems:

```text
pass rate
warn rate
quarantine rate
reject rate
common failure codes
provider-specific failure rate
model-specific failure rate
```

These can feed:

```text
P4 provider selection
P2 production confidence
P9 cost modeling
P10 optimization
```

---

# 146. Provider quality feedback

For each music provider/model:

```text
candidate_count
auto_pass_rate
warning_rate
hard_reject_rate
artifact_rate
rights_unknown_rate
average_quality_score
human_override_rate
```

This should eventually help P4 choose providers, but P5 must not silently change provider allocation itself.

---

# 147. Continuous improvement loop

Use P5 outcomes to identify:

```text
prompt patterns causing defects
provider settings causing defects
models causing defects
language-specific issues
genre-specific issues
```

However, changes to P4 generation strategy belong in P4/P10 and must be made through versioned interfaces.

---

# 148. Feedback to P4

Emit structured feedback:

```json
{
  "candidate_id": "...",
  "disposition": "REJECTED",
  "feedback": [
    {
      "code": "VOCAL_ARTIFACT",
      "severity": "HIGH",
      "repairability": "refinement"
    }
  ]
}
```

P4 can aggregate these to improve future generation attempts.

---

# 149. Feedback to P3

Only emit P3 feedback when repeated failures indicate a specification problem.

Examples:

```text
requested duration routinely impossible
language requirement produces poor outputs
specified vocal mode fails provider capability
```

P5 should not alter P3 specifications automatically.

Instead create:

```text
p3_specification_feedback_event
```

for P3/P10 to evaluate.

---

# 150. Feedback to P2

P5 can publish aggregate quality-yield signals:

```text
arm
→ generation candidates
→ P5 pass rate
→ usable-track rate
```

This lets P2 eventually distinguish:

```text
market opportunity high
but production feasibility poor
```

Again, P2 consumes the evidence; P5 does not allocate production.

---

# 151. Avoid circular gating

Do not create:

```text
P5 sees an arm has low pass rate
→ P5 changes arm selection
→ P2 reads changed result
→ P2 changes generation
→ P5 changes rule
```

without explicit versioning.

All feedback must be events/data, not hidden behavior.

---

# 152. Policy configuration

Recommended YAML/JSON configuration categories:

```text
technical_profiles
loudness_profiles
quality_thresholds
similarity_thresholds
rights_rules
license_rules
voice_rules
lyric_rules
ai_disclosure_rules
review_rules
warning_policy
catalog_duplication_rules
```

Example conceptual configuration:

```yaml
rights:
  commercial_use_unknown:
    action: quarantine
  commercial_use_ineligible:
    action: reject
```

The exact schema is implementation-flexible.

---

# 153. Policy versioning

Every policy configuration must have:

```text
policy_version
created_at
effective_at
created_by
change_summary
checksum
```

Old evaluations retain their original policy version.

---

# 154. Change-management discipline

Changing a hard gate requires:

```text
new policy version
regression tests
simulation/replay against golden dataset
decision-log entry
rollout mode
```

Do not modify a YAML threshold in production and call it done.

---

# 155. Shadow mode

Before a new detector changes release decisions, run it in shadow mode:

```text
old gate remains authoritative
new detector produces predicted findings
compare results
```

Track:

```text
new positives
new negatives
false-positive review rate
false-negative discoveries
```

---

# 156. Capped mode

After shadow mode:

```text
new detector may gate only low-risk categories
```

Limit its impact to a configurable percentage of candidates until validated.

---

# 157. Autonomous mode

Only promote a detector to hard-gating after:

```text
regression tests pass
shadow results acceptable
human-review override rate acceptable
no unexplained production anomaly
```

The promotion itself should be a versioned configuration change.

---

# 158. Rollback

Every detector/policy version must be rollbackable.

Rollback must mean:

```text
future evaluations use previous version
```

not:

```text
delete historical evaluations
```

---

# 159. Metrics to expose to the operator

Dashboard should show:

```text
Today's candidates
Passed
Passed with warnings
Quarantined
Rejected

Top rejection reasons
Top quality defects
License unknown count
Human review backlog
Provider/model failure rates
Average evaluation time
Average evaluation cost
```

---

# 160. Alert rules

Raise alerts for:

```text
pass rate drops dramatically
reject rate spikes
license unknown rate spikes
provider metadata completeness drops
one detector suddenly errors
GPU memory exhaustion
storage checksum failures
review queue backlog exceeds threshold
policy configuration mismatch
```

---

# 161. No silent fallback for rights

This is a critical rule.

If the preferred license verifier fails:

```text
DO NOT
→ assume commercial rights
```

Instead:

```text
license_status = UNKNOWN
```

and apply policy.

---

# 162. No silent fallback for artifact integrity

If a master is corrupt:

```text
DO NOT
→ automatically substitute a different candidate
```

Create a clear failure linked to the original candidate.

A separate retry/refinement workflow may produce a replacement candidate.

---

# 163. No silent artifact mutation

P5 may create derived analysis files, but the canonical P4 source artifact is immutable.

Do not:

```text
normalize audio in place
trim file in place
rename and overwrite
replace content under same checksum identity
```

---

# 164. No false claim of legal clearance

The system language should use:

```text
screened
risk assessed
commercial-use evidence present
provenance complete
no high-risk match detected
```

Avoid:

```text
legally guaranteed
copyright-proof
100% infringement-free
```

---

# 165. No false claim of copyright ownership

Even when the provider grants commercial-use rights, P5 must not represent this as automatic proof of copyright ownership/protection.

For example, current Suno documentation explicitly distinguishes commercial-use rights from copyright protection. ([Suno Help](https://help.suno.com/en/articles/9601665))

---

# 166. No artist impersonation system

The P5 system must not contain a general-purpose “does this sound exactly like artist X?” evaluator as a creative target.

Similarity analysis should focus on rights-risk detection and internal duplication, not optimizing imitation.

---

# 167. Test strategy overview

Use four test layers:

```text
unit
property
integration
production replay
```

---

# 168. Unit tests — technical DSP

Cover:

```text
LUFS calculation fixture
true-peak calculation fixture
silence detection
clipping detection
channel imbalance
phase/correlation
spectral anomaly
exact duration tolerance
```

---

# 169. Unit tests — provenance

Cover:

```text
complete lineage
missing provider record
missing license snapshot
mismatched artifact hash
broken parent chain
wrong experiment ID
```

---

# 170. Unit tests — policy engine

Cover every rule and action:

```text
pass
warn
quarantine
reject
unknown
conditional
```

---

# 171. Unit tests — similarity

Cover:

```text
exact duplicate
near duplicate
different encoding
trimmed version
same genre unrelated
same chord progression unrelated
high lyric overlap
low lyric overlap
```

---

# 172. Unit tests — AI disclosure

Examples:

```text
AI music → required
non-AI recording → policy determined
ambiguous provenance → unknown
```

---

# 173. Integration test — complete clean flow

```text
P4 candidate
 ↓
P5 ingest
 ↓
snapshot
 ↓
technical
 ↓
musical
 ↓
rights
 ↓
policy
 ↓
PASSED
 ↓
manifest
```

Verify all IDs and hashes line up.

---

# 174. Integration test — warning flow

Ensure warnings survive into the release manifest.

```text
PASSED_WITH_WARNINGS
```

must not be accidentally serialized as `PASSED`.

---

# 175. Integration test — quarantine flow

Ensure:

```text
QUARANTINED
```

candidate is absent from the ordinary P6 release queue.

---

# 176. Integration test — rejection flow

Ensure:

```text
REJECTED
```

candidate cannot produce a normal release manifest.

---

# 177. Integration test — recheck

Ensure new evidence creates a new evaluation, not a mutation of history.

---

# 178. End-to-end simulation

Run a batch of at least:

```text
100 candidates
```

using fixtures/mocks with a known mixture of:

```text
60 clean
15 warning
10 quarantine
10 reject
5 malformed
```

The exact observed output should match the expected class within explicitly defined detector tolerance where probabilistic models are involved.

---

# 179. Production-like load test

Run concurrent evaluation against a representative batch while monitoring:

```text
CPU
GPU VRAM
RAM
I/O
queue latency
failure rate
```

The implementation must not deadlock or exhaust resources indefinitely.

---

# 180. Acceptance criteria

P5 is complete only when all of the following are true.

## AC-01 — P4 ingestion

A valid P4 candidate can enter P5 without manual file handling.

## AC-02 — Snapshot

P5 creates a reproducible evaluation snapshot.

## AC-03 — Technical checks

The system detects corrupt, truncated, unreadable and materially malformed audio.

## AC-04 — Audio measurements

The system records duration, sample rate, channels, loudness, peak and other configured technical metrics.

## AC-05 — Artifact detection

The system produces configurable audio-quality/artifact signals.

## AC-06 — Musical checks

The system performs at least basic structure/repetition/coherence checks.

## AC-07 — Lyrics

When lyrics exist, the system can validate language, expected-vs-actual text and configured policy risks.

## AC-08 — Provenance

Every passed candidate has complete/approved provenance under the active policy.

## AC-09 — Commercial-use rights

The system distinguishes `eligible`, `ineligible`, `unknown`, `conditional` and other required states.

## AC-10 — Copyright distinction

Commercial-use eligibility is never equated with copyright protection.

## AC-11 — Similarity

The system detects exact and near duplicates and emits rights-risk signals without claiming legal certainty.

## AC-12 — Voice risk

Unauthorized/uncertain third-party voice risks can block or quarantine.

## AC-13 — Policy engine

Gate decisions are produced from versioned rules rather than hidden code assumptions.

## AC-14 — AI disclosure

Known AI-generated music produces `ai_disclosure_required = true` for downstream YouTube publishing under the current policy. ([YouTube Help](https://support.google.com/youtube/answer/14328491?hl=en))

## AC-15 — Release gating

Only passed candidates produce ordinary release manifests.

## AC-16 — Quarantine

Ambiguous candidates are retained with actionable review reasons.

## AC-17 — Re-evaluation

A policy/license update can create a new evaluation without mutating history.

## AC-18 — Auditability

Every final decision can be traced to evidence and versions.

## AC-19 — Idempotency

Repeated upstream events do not create duplicate active evaluations.

## AC-20 — Recovery

Worker failure can resume without corrupting state.

## AC-21 — Security

Secrets are not exposed in logs/artifacts and arbitrary file paths cannot escape the analysis sandbox.

## AC-22 — Observability

Operators can identify volume, pass rate, failure types, detector health and review backlog.

## AC-23 — Tests

Required unit, property, integration and load tests pass.

## AC-24 — P6 contract

P6 can consume a single release manifest and determine whether the candidate is cleared without reading P5 internal tables.

---

# 181. Recommended implementation sequence

Implement P5 in this order:

### P5.1 — Domain contracts

Define enums, entities, schemas, statuses and event types.

### P5.2 — Persistence

Create migrations for evaluation, findings, evidence, policy and manifest tables.

### P5.3 — P4 adapter

Implement candidate ingestion and snapshot creation.

### P5.4 — Technical QA

Implement deterministic file/container/audio checks first.

### P5.5 — Audio analysis

Implement loudness, peak, silence, channel and spectral checks.

### P5.6 — Duplicate/similarity

Implement exact fingerprinting, near-duplicate detection and internal catalog similarity.

### P5.7 — Provenance/license

Implement provider/license snapshot validation and commercial-use state handling.

### P5.8 — Lyrics/policy

Implement language, lyric integrity and policy checks.

### P5.9 — Advanced ML detectors

Add vocal/artifact/coherence detectors behind interfaces.

### P5.10 — Policy engine

Aggregate checks into versioned gate decisions.

### P5.11 — Quarantine/review

Build review queue and human decision recording.

### P5.12 — Release manifest

Implement the P6 handoff.

### P5.13 — Durable workflow

Wrap the evaluation stages in the durable orchestration layer.

### P5.14 — Observability/costs

Add metrics, tracing, cost accounting and alerts.

### P5.15 — Regression fixture suite

Build golden fixtures and policy regression tests.

### P5.16 — Shadow rollout

Compare P5 outputs against manual evaluation of a small batch.

### P5.17 — Capped autonomous rollout

Allow P5 to gate a limited production percentage.

### P5.18 — Autonomous mode

Promote only validated rules/models to hard gating.

---

# 182. What the implementation AI may decide

The implementation AI may choose:

- exact Python libraries;
- exact DSP stack;
- exact audio-quality model;
- exact embedding model;
- exact database ORM;
- exact workflow implementation if project infrastructure differs;
- internal class names;
- exact API framework;
- model-serving technology;
- exact feature-extraction method;
- whether a detector runs CPU/GPU;
- exact file-cache strategy;
- exact dashboard library.

Provided that contracts and semantics in this document remain intact.

---

# 183. What the implementation AI must not silently change

Do not silently change:

- P4/P5 boundary;
- distinction between commercial-use rights and copyright protection;
- unknown vs pass semantics;
- quarantine vs rejection semantics;
- immutable evaluation history;
- provenance requirements;
- AI disclosure field;
- hard vs soft gate semantics;
- policy versioning;
- evidence requirements;
- release-manifest contract;
- idempotency;
- detector error behavior;
- no-hidden-legal-certainty rule;
- experiment-integrity semantics.

If a material change is necessary, record it in the decision log and update this document.

---

# 184. Reasonable flexibility

This plan deliberately does not dictate a single quality model because the best detector mix may change as the catalog grows.

It is acceptable to start with:

```text
FFmpeg/librosa-style DSP
local embeddings
simple lyric classifiers
rule engine
```

and add more advanced models later.

Do not block the project waiting for a perfect all-in-one audio-quality model.

---

# 185. Avoid overengineering

Do not initially build:

- a full music-theory symbolic reconstruction engine;
- perfect song-form transcription;
- a universal copyright detector;
- expensive external similarity APIs for every candidate;
- real-time audio streaming analysis;
- a giant multimodal foundation model pipeline.

P5's first job is **reliable release gating**, not academic completeness.

---

# 186. Recommended initial detector stack

A pragmatic first implementation can use:

```text
FFmpeg / ffprobe
    → file/container/media facts

librosa / equivalent DSP
    → loudness/peak/silence/spectral/basic structure

local embedding model
    → duplicate/near-duplicate clustering

Whisper-class ASR or equivalent
    → lyric/vocal transcription where applicable

local text embedding
    → lyric similarity

rules + lightweight classifier
    → policy/metadata screening

strong LLM only for ambiguous contextual cases
```

Exact libraries remain flexible.

---

# 187. Quality-model training policy

Do not train a detector directly from raw production labels without versioning.

Every training/validation dataset should specify:

```text
dataset_version
label_definition
labeler_guidance
split_version
model_version
```

This prevents hidden training-data changes from altering gate behavior.

---

# 188. Human evaluation sampling

Even after autonomous operation, sample a percentage of:

```text
passed
warned
quarantined
rejected
```

for human review.

Purpose:

```text
false-positive detection
false-negative detection
model drift
policy drift
```

This should be random/stratified and configurable.

---

# 189. False-negative protocol

If a downstream claim or human review discovers a P5 miss:

```text
create incident
link original evaluation
link evidence
identify missed detector
assess affected catalog
create new regression fixture
update rule/model
re-evaluate affected candidates
```

Do not merely patch the individual track.

---

# 190. False-positive protocol

If human reviewers repeatedly overturn a rejection:

```text
measure override rate
identify detector/rule
add fixture
adjust model/threshold if justified
shadow test
promote new version
```

Never simply add a global exception for one song without documentation.

---

# 191. Incident severity

Use:

```text
P5-I1 — cosmetic metadata issue
P5-I2 — quality gate error
P5-I3 — release-blocking false positive/negative
P5-I4 — rights/compliance exposure
P5-I5 — systemic security/data-integrity issue
```

This helps prioritize remediation.

---

# 192. Data retention

Retain:

```text
final artifacts needed for catalog/audit
all evaluation records
policy snapshots
license evidence
decision logs
```

Temporary analysis files can have shorter retention.

Do not delete evidence needed to establish why a release decision was made.

---

# 193. Artifact storage layout

Suggested logical layout:

```text
artifacts/
  candidates/
    <candidate_id>/
      source/
      derived/
      analysis/
      manifests/

qa/
  evaluations/
    <evaluation_id>/
      report.json
      report.html
      evidence/
```

Do not expose this storage directly to the public web.

---

# 194. File naming

Use stable IDs, not titles:

```text
candidate_<candidate_id>_<artifact_id>.wav
```

Titles can change.

IDs should not.

---

# 195. Checksum strategy

For every persisted release-worthy artifact:

```text
SHA-256 required
```

Optionally store:

```text
BLAKE3
```

for fast internal verification, but SHA-256 remains the canonical interoperability checksum unless the rest of the repository already has a standard.

---

# 196. Reproducibility package

A complete evaluation should be reproducible from:

```text
source artifact
artifact checksum
configuration version
policy version
model versions
feature code version
provider/license snapshot
```

Stochastic models should retain seed/configuration metadata where meaningful.

---

# 197. Randomness policy

Deterministic checks must be deterministic.

Probabilistic models may have randomness, but evaluation metadata should record:

```text
seed if configurable
model version
sampling parameters
```

For critical gate decisions, prefer deterministic inference settings where practical.

---

# 198. Version matrix

P5 evaluation records should include:

```text
software_version
schema_version
policy_version
classifier_version
embedding_version
ASR_version
similarity_version
```

This is essential for future replays.

---

# 199. Example final evaluation

```text
Candidate: cand_01H...
Artifact: art_01H...
Evaluation: eval_01H...

Disposition: PASSED_WITH_WARNINGS

TECHNICAL
PASS
  WAV
  44.1 kHz
  Stereo
  03:18.4
  No material clipping
  True peak: -0.7 dBTP

MUSICAL
PASS
  Structure: coherent
  Repetition: expected
  Artifact score: 0.12

VOCAL
WARNING
  Minor transition artifact at 02:11–02:13

LYRICS
PASS
  Language: English
  Alignment: 93%
  Policy: pass

PROVENANCE
PASS
  P3 spec linked
  P4 generation linked
  Provider/model linked
  License snapshot present

RIGHTS
PASS / LOW RISK
  No high-risk distinctive match detected
  Commercial use: eligible under provider snapshot
  Copyright protection: not determined

YOUTUBE
AI disclosure: REQUIRED

FINAL
PASSED_WITH_WARNINGS
Release manifest: rm_01H...
```

---

# 200. Example quarantine evaluation

```text
Disposition: QUARANTINED

Blocking/uncertain findings:
- LICENSE_STATUS_UNKNOWN
- POSSIBLE_THIRD_PARTY_VOICE

Evidence:
- provider plan could not be verified at generation time
- voice similarity score 0.71, confidence 0.66

Recommended action:
REQUEST_MORE_EVIDENCE

No release manifest created.
```

---

# 201. Example rejection evaluation

```text
Disposition: REJECTED

Critical findings:
- ARTIFACT_TRUNCATED

Technical:
Expected duration: 211.3s
Decoded duration: 34.7s
Checksum valid, but file content is incomplete.

Action:
RETURN_TO_P4_REGENERATION
```

---

# 202. P5 operational command set

Provide operator/service commands such as:

```text
p5 evaluate <candidate_id>
p5 recheck <candidate_id>
p5 explain <evaluation_id>
p5 review queue
p5 policy show <version>
p5 policy validate <version>
p5 detector status
p5 detector shadow <version>
p5 catalog similarity <candidate_id>
p5 evidence export <evaluation_id>
p5 health
```

These should be wrappers around APIs/workflows, not ad-hoc scripts that bypass the database.

---

# 203. P5 definition of done

P5 is complete when a P4 candidate can be automatically evaluated end-to-end without human file handling; technical defects, incomplete provenance and explicit rights/policy failures are reliably blocked; ambiguous cases are quarantined; all decisions are reproducible and auditable; AI-disclosure requirements are carried downstream; approved candidates receive a release manifest; and P6 can rely on the P5 contract without understanding the internals of any detector or provider.

The most important end-state property is:

> **P6 must be able to trust that a candidate marked PASSED/PASSED_WITH_WARNINGS has passed the current internal release gates, while still understanding that P5 screening is not a guarantee of future platform claims, monetization outcomes, or jurisdiction-specific copyright protection.**

---

# 204. Final boundary rule

P5 owns:

```text
QUALITY
PROVENANCE
RIGHTS EVIDENCE
POLICY SCREENING
RELEASE GATING
```

P5 does not own:

```text
MARKET SUCCESS
GENRE SELECTION
A&R ALLOCATION
GENERATION
VIDEO CREATION
YOUTUBE PUBLICATION
DSP DISTRIBUTION
REVENUE OPTIMIZATION
```

The correct final P5 output is:

```text
Candidate evaluated.
Evidence captured.
Policy version fixed.
Disposition determined.
Release manifest created if eligible.
Ready for P6, or quarantined/rejected with explicit reasons.
```
