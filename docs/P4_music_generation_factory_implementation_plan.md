# P4 — Music Generation Factory
## Detailed Implementation Plan

**Document status:** Implementation-ready specification  
**Phase:** P4 of the Autonomous AI Music Media Engine  
**Primary objective:** Convert P3 `ContentSpec` / `TrackSpec` production specifications into generated music candidates and versioned audio artifacts through a provider-neutral, autonomous, auditable generation pipeline.

---

# 1. Purpose

P4 is the **music production layer** of the autonomous music business.

Its responsibility is to take a production-ready specification from P3 and produce one or more candidate tracks without requiring a human to decide:

- what prompt to write;
- which generation variant to try;
- when to retry;
- whether to use an alternate generation mode;
- whether to extend a promising candidate;
- how many candidates should be produced within budget;
- how to package the resulting audio artifact for downstream QA.

P4 is deliberately **not** the final music-quality gate. P5 owns deeper quality, rights, provenance and publication eligibility decisions.

P4 should therefore optimize for:

> **high-throughput, reproducible, provenance-complete candidate generation with sensible autonomous iteration.**

It should not attempt to solve every artistic judgment problem inside one module.

---

# 2. Strategic position in the system

```text
P1 Market Intelligence
        ↓
P2 Autonomous A&R
        ↓
P3 Content Genome / Experiment Framework
        ↓
P4 Music Generation Factory
        ↓
P5 Quality / Rights / Provenance Gate
        ↓
P6 Playlist / Visual / Video Factory
        ↓
P7 Publishing
        ↓
P9 Analytics
        ↓
P10 Learning
```

P4 is downstream of the **experiment specification** and upstream of the **quality gate**.

P4 must not reinterpret P2's business decision or P3's experiment identity.

For example:

```text
P2 says:
  test R&B × late-night drive

P3 says:
  English vocal
  82–96 BPM
  dreamy/melancholic
  45–75 minute playlist context
  rainy Asian city visual world
  creative latitude allowed for arrangement

P4 decides:
  exact wording of generation prompts
  generation variants
  provider/model choice within configured policy
  iteration strategy
  candidate count
  extension/edit strategy
```

This separation must be preserved.

---

# 3. Scope

## 3.1 In scope

P4 must implement:

1. Provider-neutral music-generation interface.
2. Provider capability discovery.
3. Provider/model configuration.
4. P3 `TrackSpec` ingestion.
5. Generation-plan materialization.
6. Prompt construction.
7. Prompt/version provenance.
8. Candidate batch planning.
9. Initial generation.
10. Generation retries.
11. Candidate extension/edit workflows where the provider supports them.
12. Candidate lineage.
13. Audio artifact download/import.
14. Artifact checksums and immutable storage.
15. Audio metadata extraction.
16. Basic technical preflight.
17. Cost tracking.
18. Credit/quota tracking.
19. Concurrency control.
20. Provider fallback selection.
21. Candidate-bank management.
22. Autonomous refinement rules.
23. Generation stopping rules.
24. Idempotency.
25. Crash/restart recovery.
26. Structured logging.
27. Metrics and observability.
28. P4 → P5 handoff.
29. Tests using mocked providers and deterministic fixtures.

## 3.2 Explicitly out of scope

Do not make P4 responsible for:

- final artistic acceptance;
- copyright/legal clearance;
- final similarity clearance;
- YouTube publishing;
- Spotify release submission;
- thumbnail generation;
- playlist sequencing;
- final mastering decisions that depend on P5 policy;
- audience analytics;
- bandit allocation;
- deciding which market opportunity to pursue;
- changing the experimental hypothesis;
- inventing a new content arm without P3;
- training a foundation music model.

P4 may run technical preflight checks required to prevent obviously invalid artifacts from entering P5, but P5 remains the authoritative release/quality gate.

---

# 4. Design principles

## 4.1 Provider abstraction is mandatory

The business must not depend on one vendor's implementation details.

Implement a provider interface such as:

```python
class MusicGenerationProvider(Protocol):
    async def capabilities(self) -> ProviderCapabilities: ...

    async def generate(self, request: GenerationRequest) -> GenerationResponse: ...

    async def extend(self, request: ExtendRequest) -> GenerationResponse: ...

    async def edit(self, request: EditRequest) -> GenerationResponse: ...

    async def get_job(self, provider_job_id: str) -> ProviderJobStatus: ...

    async def cancel(self, provider_job_id: str) -> None: ...

    async def download(self, artifact_ref: str) -> AsyncIterator[bytes]: ...

    async def estimate_cost(self, request: GenerationRequest) -> CostEstimate: ...

    async def account_status(self) -> ProviderAccountStatus: ...

    async def license_info(self, generation_context: LicenseContext) -> LicenseInfo: ...
```

The exact method names may differ if an existing repository convention is stronger, but the logical contract must remain.

## 4.2 P4 must preserve creative freedom

Do not convert P3 into one giant rigid prompt.

P3 explicitly distinguishes:

```text
hard constraints
bounded preferences
creative freedom
```

P4 must preserve that structure when producing prompts.

## 4.3 Generate candidates before selecting winners

P4 is a production engine, not an A&R judge.

Where provider economics permit, generate multiple candidates and pass them forward.

P4 may perform technical screening and obvious artifact rejection.

P5 decides substantive quality/rights eligibility.

## 4.4 Keep provenance complete

Every generated audio artifact must be traceable to:

```text
production directive
experiment
track spec
content genome
prompt
provider
model/version
provider job
account/subscription context
creation timestamp
download timestamp
file checksum
source input artifacts
```

## 4.5 Deterministic orchestration, controlled creative randomness

The workflow itself must be deterministic in its decision logic.

Creative generation may be stochastic because the provider is stochastic.

Therefore record a `generation_attempt_seed` whenever the provider supports it. If it does not, create an orchestration-side deterministic attempt ID and record that provider-level randomness is uncontrolled.

## 4.6 Cost is a first-class constraint

The generator must never keep retrying indefinitely.

Every generation run must have:

```text
max candidates
max attempts
max spend
max provider credits
max wall time
```

## 4.7 Never silently downgrade rights

If a fallback provider does not provide the same commercial-use terms, the generated output must be marked accordingly and cannot silently enter a commercially eligible queue.

---

# 5. Core concepts

## 5.1 TrackSpec

P3's immutable production brief for one track.

## 5.2 GenerationPlan

P4's operational plan for turning one `TrackSpec` into one or more candidate generations.

## 5.3 GenerationAttempt

A single provider request/operation.

## 5.4 GeneratedTrack

Canonical logical identity representing the candidate independent of file version.

## 5.5 AudioArtifact

A specific immutable file produced/downloaded by a generation attempt.

## 5.6 CandidateRevision

A versioned candidate derived from another candidate through extension/edit/refinement.

## 5.7 Provider

An external or local generation implementation.

## 5.8 CandidateBank

A persistent pool of generated candidates awaiting P5 evaluation or later reuse.

---

# 6. End-to-end P4 flow

```text
P3 TrackSpec
    ↓
Validate specification
    ↓
Resolve provider policy
    ↓
Discover provider capabilities
    ↓
Materialize GenerationPlan
    ↓
Construct prompt / parameters
    ↓
Estimate cost
    ↓
Reserve budget / credits
    ↓
Submit generation
    ↓
Poll / receive completion
    ↓
Download artifact
    ↓
Verify integrity
    ↓
Extract metadata
    ↓
Technical preflight
    ↓
Store immutable artifact + provenance
    ↓
CandidateBank
    ↓
P5 Quality / Rights Gate
```

For a refinement path:

```text
Candidate
   ↓
P5 / P4 signal
   ↓
refinement plan
   ↓
extend/edit
   ↓
new candidate revision
   ↓
technical preflight
   ↓
CandidateBank
```

---

# 7. Inputs from P3

P4 consumes the P3 `TrackSpec` and its parent `PlaylistSpec` / `Experiment` lineage.

Minimum required input:

```text
track_spec_id
playlist_spec_id
experiment_id
content_arm_id
brand_profile_id
hard_constraints
bounded_preferences
creative_latitude
lyric_spec
vocal_spec
instrumentation_spec
production_style
track_role
requested_duration
candidate_target
priority
budget_limit
provider_policy
```

P4 must reject a production request where:

```text
track_spec_id missing
experiment_id missing
hard constraints invalid
no permitted provider
budget is negative
candidate_target < 1
```

---

# 8. P4 request boundary

Expose a top-level application command similar to:

```python
await music_factory.create_candidates(
    ProductionRequest(
        track_spec_id="...",
        candidate_target=8,
        budget_limit=Decimal("...")
    )
)
```

The command must not require callers to manually provide a music prompt.

The point of P4 is that the system derives the operational prompt from P3.

---

# 9. Provider capability model

Each provider must declare capabilities.

Example:

```json
{
  "provider_id": "suno",
  "model_id": "vX",
  "supports_generation": true,
  "supports_extension": true,
  "supports_edit": true,
  "supports_stems": true,
  "supports_seed": false,
  "supports_custom_duration": true,
  "max_duration_seconds": 480,
  "min_duration_seconds": 30,
  "supports_instrumental": true,
  "supports_lyrics": true,
  "supports_negative_prompt": false,
  "supports_async_jobs": true,
  "commercial_use_policy_ref": "..."
}
```

Do not hard-code these capabilities elsewhere.

At runtime, the generation planner adapts to the provider capability set.

---

# 10. Provider adapter requirements

Every provider adapter must:

1. Authenticate through injected credentials.
2. Never log secrets.
3. Translate canonical requests to provider-specific requests.
4. Translate provider responses back to canonical models.
5. Preserve provider job/artifact identifiers.
6. Report provider-specific errors using canonical categories.
7. Report estimated or actual credit/cost consumption where available.
8. Expose licensing metadata or an explicit `UNKNOWN` state.
9. Support idempotent polling/retrieval as far as the provider allows.
10. Keep provider-specific code isolated from domain logic.

Do not leak provider SDK models through the rest of the application.

---

# 11. Provider selection policy

Provider selection must be configuration-driven.

A candidate provider must satisfy:

```text
enabled
healthy
capability-compatible
commercial-use-compatible
budget-compatible
region/account-compatible
```

Then rank eligible providers using configurable preferences such as:

```text
quality_prior
cost_prior
latency_prior
reliability_prior
commercial_rights_confidence
```

Do not embed `Suno is always first` in business logic.

A Suno adapter can be the initial default provider, but the factory must remain provider-neutral.

---

# 12. Commercial-use eligibility

For every generation attempt store:

```text
commercial_use_status
commercial_use_basis
license_reference
license_checked_at
subscription_state_if_relevant
provider_policy_version
```

Allowed statuses:

```text
eligible
ineligible
unknown
conditional
expired
revoked
```

If the provider cannot provide authoritative commercial-use information, mark it `unknown`.

Never convert `unknown` to `eligible` based on assumptions.

P5 must be able to reject any output with unresolved rights eligibility.

---

# 13. Prompt architecture

Prompt construction is a P4 responsibility, but it must use structured layers.

Recommended layers:

```text
1. musical identity
2. emotional/use-case intent
3. vocal direction
4. instrumentation
5. production characteristics
6. structure / arrangement intent
7. duration/context
8. exclusions
9. experiment-specific instruction
```

Example internal representation:

```json
{
  "identity": ["alternative R&B", "nocturnal"],
  "emotion": ["dreamy", "melancholic"],
  "use_case": "late_night_drive",
  "vocal": {
    "mode": "vocal",
    "language": "english",
    "profile": "intimate_female"
  },
  "instrumentation": ["warm_synth", "sub_bass", "restrained_drums"],
  "production": ["clean_modern_mix", "wide_atmosphere"],
  "structure": ["strong_intro", "clear_hook", "controlled_outro"],
  "creative_freedom": ["melody", "exact_chords", "specific_wording"]
}
```

The final provider prompt is generated from this structure.

---

# 14. Prompt prohibitions

The prompt builder must automatically block or rewrite dangerous patterns such as:

```text
"in the style of [specific living artist]"
"sound exactly like [specific song]"
"copy the melody of ..."
"use the voice of ..."
```

Instead, convert legitimate musical intent into descriptive attributes.

Example transformation:

```text
bad:
  "like Artist X"

acceptable semantic representation:
  "dark alternative R&B, restrained drums, intimate breathy vocal,
   sparse synth textures, late-night emotional atmosphere"
```

Do not silently preserve prohibited artist/song references in hidden prompt fields.

Store sanitized prompt and, separately, the original structured P3 input that caused the sanitization.

---

# 15. Prompt versioning

Every generation attempt must capture:

```text
prompt_template_id
prompt_template_version
prompt_builder_version
structured_prompt_json
provider_prompt_text
negative_prompt if supported
provider_parameters
```

Prompt templates must be versioned.

If the prompt builder changes, existing generated artifacts remain associated with the exact historical version.

---

# 16. Candidate count planning

P4 should not always generate the same number of candidates.

Candidate target should be derived from:

```text
P3 candidate_target
provider economics
track priority
uncertainty
historical quality yield
available budget
available credits
```

Example configuration:

```text
standard track: 6 candidates
high-priority exploratory track: 10
replication track: 4
budget-constrained track: 2
```

These are examples, not hard-coded values.

P2 controls strategic allocation. P3 defines the experiment. P4 determines operational generation quantity within the directive.

---

# 17. Generation attempt model

Each generation attempt must contain:

```text
attempt_id
track_spec_id
generation_plan_id
candidate_id if assigned
attempt_number
provider_id
model_id
provider_job_id
request_hash
request_payload_hash
started_at
submitted_at
completed_at
status
failure_code
retry_count
cost_estimate
actual_cost if available
credit_estimate
actual_credit_usage if available
```

Statuses:

```text
planned
reserved
submitted
processing
succeeded
downloaded
validated
failed
cancelled
expired
abandoned
```

---

# 18. Generation idempotency

A generation request must have a canonical request fingerprint.

Fingerprint inputs should include:

```text
track_spec_id
prompt_builder_version
structured_prompt
provider_id
model_id
provider_parameters
attempt purpose
creative variant ID
```

Do not include:

```text
runtime timestamps
random UUIDs
```

unless the random value is intentionally part of the creative seed.

If the exact same request has already succeeded and the workflow is retrying due to an orchestration failure, prefer reusing the existing result rather than generating a duplicate track.

However, if the provider is inherently stochastic and the business explicitly requests a new creative sample, create a new `creative_variant_id` so it is intentionally non-idempotent at the generation level.

---

# 19. Creative variant strategy

Candidate diversity is important.

Do not submit identical prompts six times and call the outputs six meaningful experiments.

Create a `CreativeVariant` layer that changes controlled creative choices within P3's allowed latitude.

Examples:

```text
variant A:
  softer drums
  warmer synths
  restrained vocal

variant B:
  stronger chorus
  slightly brighter arrangement
  wider stereo field

variant C:
  more acoustic texture
  lower energy
  longer intro
```

The variant generator must only alter dimensions marked as creative/bounded by P3.

It must not mutate hard constraints.

---

# 20. Variant generation rules

Every variant must record:

```text
variant_id
parent_track_spec_id
base_variant_id if derived
changed_dimensions
change_reason
allowed_by_p3 = true/false
variant_prompt_delta
```

Never mutate the parent `TrackSpec`.

---

# 21. Batch generation planner

For each `TrackSpec`, produce a plan:

```json
{
  "generation_plan_id": "...",
  "track_spec_id": "...",
  "candidate_target": 8,
  "max_attempts": 12,
  "provider_preference": ["...", "..."],
  "creative_variants": ["A", "B", "C", "D"],
  "budget_limit": "...",
  "stopping_policy": {
    "min_candidates": 4,
    "max_candidates": 8,
    "max_spend": "...",
    "max_failures": 4
  }
}
```

The exact numbers are configurable.

---

# 22. Adaptive generation

P4 should support autonomous iteration within the track brief.

Example:

```text
Generate 4 initial variants
        ↓
technical screening
        ↓
if all clearly weak:
    change creative variant strategy
        ↓
Generate additional 3
```

P4 must not perform final quality ranking based on subjective artistic quality unless a separate configured model explicitly provides a non-authoritative pre-screen signal.

For the first implementation, keep adaptive iteration primarily based on:

```text
technical validity
provider success/failure
artifact completeness
obvious generation defects
```

P5 will supply deeper quality signals later.

---

# 23. Refinement loop

Once P5 exists, P4 may accept a refinement request.

Example:

```text
P5 says:
  instrumental quality good
  vocal coherence poor

P4 receives:
  refine vocal coherence
```

Then P4 should create a derived revision plan:

```text
preserve:
  genre
  mood
  tempo envelope
  use case

modify:
  vocal profile
  lyric density
  vocal generation settings
```

The parent candidate remains immutable.

---

# 24. Extension workflow

If the provider supports extension:

```text
candidate_A
    ↓
extension specification
    ↓
extension generation
    ↓
candidate_B
```

Candidate B must record:

```text
parent_candidate_id
parent_artifact_id
extension_start_context
extension_parameters
```

Do not overwrite the original file.

---

# 25. Edit workflow

If the provider supports targeted editing:

```text
candidate_A
    ↓
edit specification
    ↓
provider edit
    ↓
revision_B
```

The edit specification must declare the intended change.

Example:

```json
{
  "preserve": ["overall arrangement", "tempo", "vocal identity"],
  "modify": ["intro length", "drum density"],
  "reason": "intro should fit playlist opener role"
}
```

Do not use unrestricted full regeneration when a more precise provider operation is available unless policy explicitly permits it.

---

# 26. Lyrics boundary

P4 may generate or pass lyrics to a provider because P3 defines a lyric specification.

But the canonical lyric artifact must be stored separately from the provider prompt.

Objects:

```text
LyricSpec
LyricDraft
LyricRevision
```

At minimum preserve:

```text
lyric_source
lyric_model
lyric_prompt_version
language
explicitness
lyrics_text
lyrics_hash
```

If a provider generates lyrics internally and does not expose the prompt/text separately, store the provider's returned lyrics if available and mark source accordingly.

---

# 27. Lyric-generation strategy

Do not assume every track needs LLM-generated lyrics.

P3 may specify:

```text
instrumental
vocal with free lyrics
vocal with structured lyric concept
provider-generated lyrics
```

P4 chooses an implementation compatible with that mode.

If the lyric source is an external model, record its model/version and request identity.

---

# 28. Audio artifact intake

After generation completes, P4 must:

1. retrieve the artifact;
2. stream/download safely;
3. compute checksum;
4. verify MIME/content type;
5. verify file is non-empty;
6. inspect container metadata;
7. persist immutable artifact.

Do not trust provider-reported duration or file type without checking the downloaded file.

---

# 29. Storage architecture

Recommended object storage hierarchy:

```text
music/
  {brand_id}/
    {experiment_id}/
      {track_spec_id}/
        {candidate_id}/
          {artifact_id}/
            audio_master.ext
            metadata.json
            provenance.json
```

The database stores metadata and object references.

Do not store large audio blobs directly in PostgreSQL unless the repository already has a deliberately chosen blob-storage architecture.

---

# 30. Artifact immutability

Once an artifact is stored:

```text
checksum
object_path
byte_size
mime_type
sample_rate
channels
duration
created_at
```

must not be silently modified.

Any audio transformation creates a new artifact ID and parent relationship.

---

# 31. Audio metadata extraction

Use a reliable media probe such as FFprobe or equivalent.

Extract at minimum:

```text
codec
container
sample_rate
channels
bit_depth if available
bitrate if available
duration_seconds
file_size_bytes
```

Store probe tool/version.

Do not overwrite provider-reported metadata; store both if needed:

```text
provider_duration
measured_duration
```

---

# 32. Basic technical preflight

P4 performs only obvious technical checks before P5.

Required checks:

```text
file readable
supported audio format
non-zero duration
non-zero size
sample rate present
channel count valid
no decode errors
no grossly malformed container
```

Optional early checks:

```text
digital clipping heuristic
near-total silence
extreme duration mismatch
```

These are preflight signals, not final quality decisions.

---

# 33. Do not overreach into mastering

P4 should not automatically apply aggressive normalization/compression to every generated track.

The source artifact should remain available as produced by the provider.

Any later transformation such as:

```text
normalization
EQ
compression
limiting
noise treatment
fade
stereo processing
```

must create a new derived artifact with a transformation manifest.

P5/P6 can decide which transformations are appropriate.

---

# 34. Candidate identity

A candidate ID identifies the logical generated result, not the binary file.

Suggested identity:

```text
candidate_id
track_spec_id
experiment_id
generation_family_id
parent_candidate_id nullable
```

Artifact revisions attach to candidate identity.

---

# 35. Candidate lifecycle

Use explicit states:

```text
planned
queued
generating
artifact_received
preflight_passed
preflight_failed
awaiting_p5
refinement_requested
superseded
rejected
accepted_by_p5
archived
```

P4 must not set the final commercially publishable state.

`accepted_by_p5` is a downstream state.

---

# 36. Candidate bank

The candidate bank is a persistent pool of generated tracks.

Each candidate must be searchable by:

```text
brand
experiment
content arm
track role
genre
mood
language
provider
model
status
creation date
cost
quality state
```

P5 can pull candidates from the bank without rerunning generation.

---

# 37. Candidate retention policy

Do not delete rejected candidates immediately.

Recommended states:

```text
active
quarantined
rejected
superseded
archived
```

Store metadata even after the audio file is moved or deleted according to retention rules.

This is necessary for:

- future model evaluation;
- prompt analysis;
- cost analysis;
- failure analysis;
- provenance;
- avoiding repeated generation mistakes.

---

# 38. Cost accounting

Every generation attempt must create a cost record.

Fields:

```text
cost_id
attempt_id
provider_id
cost_type
estimated_amount
actual_amount
currency
credits_estimated
credits_actual
calculation_method
pricing_version
recorded_at
```

Cost types:

```text
generation
extension
edit
download
storage_estimate
other_provider_fee
```

P4 itself should not calculate revenue.

P9 handles economics.

---

# 39. Budget reservation

Before submitting a paid request:

```text
check budget
→ reserve amount
→ submit generation
→ settle actual cost
→ release unused reservation
```

If reservation cannot be obtained, do not submit the request.

A reservation must expire if a job never starts.

---

# 40. Quota and credit accounting

Track both:

```text
financial budget
provider credits
```

These are not interchangeable.

A request can be financially affordable but unavailable because monthly credits are exhausted.

Implement provider-local account tracking.

---

# 41. Concurrency control

Prevent accidental generation storms.

At minimum support limits for:

```text
per provider
per account
per brand
per experiment
per track
per worker
```

Example configuration:

```text
max_concurrent_generations_provider = 4
max_concurrent_generations_brand = 8
max_concurrent_generations_track = 2
```

Do not hard-code these values.

---

# 42. Worker model

Use durable workers rather than a synchronous HTTP request for long-running generation.

Conceptual flow:

```text
API command
   ↓
Generation workflow
   ↓
Queue
   ↓
Provider worker
   ↓
Provider job
   ↓
polling/event handling
   ↓
download worker
   ↓
preflight worker
```

This allows process restarts without losing state.

---

# 43. Retry strategy

Retry only transient failures.

Retryable examples:

```text
network timeout
provider 5xx
temporary provider unavailability
rate limit with retry-after
```

Non-retryable examples:

```text
invalid prompt
invalid account
insufficient credit
unsupported feature
license policy rejection
malformed request
```

Use exponential backoff with jitter.

Every retry increments `attempt_number` or creates a distinct generation attempt according to the provider semantics.

Do not conflate a transport retry with an intentional creative regeneration.

---

# 44. Transport retry vs creative retry

This distinction is mandatory.

### Transport retry

The same request failed due to infrastructure/provider transport.

Use the same request fingerprint.

### Creative retry

The previous generation completed but a new creative candidate is intentionally requested.

Create:

```text
new creative_variant_id
new attempt_id
new request fingerprint
```

This prevents accidental duplicate charging or duplicate work.

---

# 45. Provider fallback

If provider A fails repeatedly, the factory may select provider B only if:

```text
provider B supports the required content mode
provider B satisfies rights policy
provider B satisfies brand/channel requirements
provider B is inside budget
provider B is enabled by configuration
```

The fallback must record:

```text
fallback_trigger
original_provider
fallback_provider
policy_version
```

Do not silently change provider/model and pretend it was the same generation setup.

---

# 46. Provider health score

Track provider health using:

```text
success_rate
mean_latency
p95_latency
error_rate
quota availability
artifact failure rate
rights-data completeness
```

This can inform provider routing later.

Do not let a provider health score override hard constraints.

---

# 47. Autonomous stopping rules

P4 must stop when any of these occurs:

```text
candidate_target reached
budget exhausted
credit exhausted
max attempts reached
max wall-clock duration reached
provider unavailable
hard rights condition violated
system shutdown/cancellation
```

Optional quality-aware stopping may be added after P5 integration, but it must not be enabled by default without sufficient validated quality data.

---

# 48. Underproduction handling

If the system requested:

```text
8 candidates
```

but only 5 are successfully produced:

store:

```text
requested = 8
produced = 5
failed = 3
```

Do not fabricate five as eight.

The workflow should determine whether to:

```text
retry
fallback provider
end as partial
```

according to policy.

---

# 49. Generation quality yield tracking

P4 should track technical yield:

```text
submitted
completed
artifact_download_success
preflight_passed
preflight_failed
```

Later P5 can supply substantive quality yield.

This allows the system to discover provider reliability differences without conflating them with artistic quality.

---

# 50. Provider/model registry

Create a registry containing:

```text
provider_id
model_id
model_version
status
capabilities
pricing_version
rights_policy_version
default_parameters
max_concurrency
health_state
```

Models must not be selected by scattered string literals throughout the application.

---

# 51. Provider configuration hierarchy

Resolve settings in this order:

```text
system defaults
→ provider defaults
→ brand profile
→ experiment policy
→ track-specific requirements
→ runtime safety constraints
```

A lower-level override must not violate a higher-priority hard constraint.

---

# 52. Parameter validation

Before submitting to a provider, validate:

```text
duration
BPM if provider supports it
language
vocal mode
instrumental flag
lyrics length
prompt length
model-specific limits
```

When P3 requests an unsupported parameter, adapt it semantically where possible.

Example:

```text
P3 requests 65 minutes
provider max per song = 5 minutes
```

Do not reject the whole playlist context.

Instead, P4 should interpret `65 minutes` as the playlist context and generate an individual track inside its track-level duration range.

If P3 truly requires a track longer than provider capability, mark the request `capability_blocked` rather than inventing a duration silently.

---

# 53. Semantic adaptation

Provider-specific constraints may require translation.

Example:

```text
P3:
energy = low_medium
```

Provider may have:

```text
mellow
balanced
energetic
```

Translate through a versioned mapping table:

```text
mapping_id
source_dimension
source_value
provider_id
model_id
target_parameter
target_value
mapping_version
```

Do not bury such mappings in prompt text only.

---

# 54. Generation prompt planner

The planner should produce:

```text
CanonicalMusicBrief
      ↓
ProviderSemanticMapping
      ↓
ProviderPrompt
      ↓
ProviderParameters
```

The canonical music brief is provider-neutral.

That means changing from Suno to a local provider should not require changing P3.

---

# 55. Model-specific adaptation

Use a model adapter when needed.

Example:

```python
class SunoVxAdapter(MusicModelAdapter):
    ...

class LocalAceStepAdapter(MusicModelAdapter):
    ...
```

The adapter handles:

```text
prompt syntax
length limits
lyrics format
parameter mapping
negative prompt handling
seed handling
extension semantics
```

Do not scatter model-specific `if provider == ...` branches through domain services.

---

# 56. Generation workflow pseudocode

Conceptually:

```python
async def run_generation_plan(plan_id):
    plan = load_plan(plan_id)
    validate_plan(plan)

    while not stopping_condition(plan):
        variant = choose_next_variant(plan)
        provider = select_provider(plan, variant)

        estimate = provider.estimate_cost(build_request(plan, variant))
        reserve_budget(estimate)

        attempt = create_attempt(plan, variant, provider, estimate)

        try:
            response = await provider.generate(build_request(plan, variant))
            await wait_for_completion(response)
            artifact = await download_artifact(response)
            record_artifact(artifact)
            run_preflight(artifact)
            mark_candidate_available()
            settle_cost()
        except TransientError:
            retry_transport_or_reschedule()
        except CreativeGenerationError:
            record_failure()
            release_budget()
        except Exception:
            mark_recovery_required()
            release_or_reconcile_budget()
            raise
```

The actual implementation can use Temporal or the repository's durable workflow engine rather than this literal structure.

---

# 57. Candidate selection inside P4

P4 may select the next variant to generate, but not the final winner.

Preferred policy:

1. satisfy required coverage of planned variants;
2. diversify creative dimensions;
3. avoid duplicate prompt fingerprints;
4. favor untested dimensions within P3 latitude;
5. avoid overspending early when uncertainty is high.

This is **production diversification**, not the A&R bandit.

---

# 58. Candidate diversity safeguards

At least one of the following must differ between intentionally diverse variants:

```text
arrangement profile
instrumentation emphasis
vocal delivery profile
texture
energy contour
intro/hook strategy
lyric density
```

provided P3 allows it.

Do not manipulate protected experiment variables in a way that invalidates P3's experiment.

---

# 59. Experiment integrity

P4 must validate every variant against the P3 experiment roles:

```text
fixed
controlled
experimental
incidental
```

If a variant changes a `fixed` variable:

```text
reject variant
raise invariant violation
```

If it changes an `experimental` variable unintentionally:

```text
reject or reclassify before generation
```

Do not silently contaminate experiments.

---

# 60. Track role adaptation

P3 may classify a track as:

```text
opener
build
anchor
peak
reset
closer
```

P4 can use that role to alter production instructions within the allowed creative envelope.

Example:

```text
opener:
  faster musical identity reveal
  avoid overly long ambient intro

closer:
  softer ending
  intentional outro
```

These are production adaptations, not experimental changes, unless P3 explicitly defines them as variables.

---

# 61. Duration handling

Maintain three distinct concepts:

```text
provider generation duration
track target duration
playlist target duration
```

Never confuse them.

If provider output duration differs from target, store:

```text
requested_duration
provider_duration
measured_duration
duration_deviation
```

Do not silently stretch audio in P4 merely to force a number unless an explicit duration transformation policy exists.

---

# 62. File naming

Generated filenames must be stable and non-semantic enough to avoid collisions.

Example:

```text
{artifact_id}.{ext}
```

Human-readable names belong in metadata, not as storage identity.

---

# 63. Metadata manifest

Every artifact must have a machine-readable manifest resembling:

```json
{
  "artifact_id": "...",
  "candidate_id": "...",
  "track_spec_id": "...",
  "experiment_id": "...",
  "provider": {
    "provider_id": "...",
    "model_id": "...",
    "model_version": "...",
    "job_id": "..."
  },
  "generation": {
    "attempt_id": "...",
    "variant_id": "...",
    "prompt_template_version": "...",
    "structured_prompt_hash": "..."
  },
  "audio": {
    "duration_seconds": 198.37,
    "sample_rate": 44100,
    "channels": 2,
    "sha256": "..."
  },
  "rights": {
    "commercial_use_status": "unknown"
  },
  "created_at": "..."
}
```

---

# 64. Provenance graph

The relationship should be queryable as:

```text
ProductionDirective
    ↓
Experiment
    ↓
ContentSpec
    ↓
TrackSpec
    ↓
GenerationPlan
    ↓
CreativeVariant
    ↓
GenerationAttempt
    ↓
GeneratedTrack / Candidate
    ↓
AudioArtifact
    ↓
P5 evaluation
```

For revisions:

```text
Artifact A
   ↓ parent_of
Artifact B
   ↓ parent_of
Artifact C
```

Never overwrite lineage.

---

# 65. Database schema

Implement at least the following tables/entities.

## `music_providers`

```text
id
provider_id
name
status
configuration_version
created_at
updated_at
```

## `music_models`

```text
id
provider_id
model_id
model_version
status
capabilities_json
pricing_version
rights_policy_version
created_at
updated_at
```

## `generation_plans`

```text
id
track_spec_id
experiment_id
candidate_target
max_attempts
budget_limit
credit_limit
policy_version
status
created_at
started_at
finished_at
```

## `creative_variants`

```text
id
generation_plan_id
variant_code
parent_variant_id
changed_dimensions_json
change_reason
prompt_delta_json
created_at
```

## `generation_attempts`

```text
id
generation_plan_id
creative_variant_id
provider_id
model_id
provider_job_id
attempt_type
transport_retry_of nullable
request_hash
request_payload_hash
status
failure_code
started_at
submitted_at
completed_at
cost_estimate
actual_cost
credit_estimate
actual_credits
```

## `generated_candidates`

```text
id
track_spec_id
experiment_id
generation_plan_id
parent_candidate_id nullable
status
provider_id
model_id
created_at
updated_at
```

## `audio_artifacts`

```text
id
candidate_id
artifact_type
object_uri
sha256
mime_type
byte_size
duration_seconds
sample_rate
channels
codec
probe_version
created_at
```

## `artifact_lineage`

```text
parent_artifact_id
child_artifact_id
transformation_type
transformation_manifest_json
created_at
```

## `rights_snapshots`

```text
id
attempt_id
provider_id
commercial_use_status
basis
license_reference
policy_version
checked_at
```

## `provider_cost_records`

```text
id
attempt_id
cost_type
estimated_amount
actual_amount
currency
credits_estimated
credits_actual
pricing_version
created_at
```

---

# 66. Database invariants

The following must be enforced at application and, where practical, DB constraint level:

```text
attempt.provider_id exists
attempt.model_id exists
candidate.track_spec_id exists
artifact.candidate_id exists
artifact.sha256 non-null
artifact.byte_size > 0
artifact.duration_seconds > 0
cost amounts >= 0
credit amounts >= 0
```

A child artifact must have an existing parent artifact if `transformation_type` requires one.

---

# 67. API endpoints

Expose P4 read/write operations through application services or APIs.

### `POST /music/generation-plans`

Creates a plan from a P3 `TrackSpec` reference.

### `POST /music/generation-plans/{id}/run`

Starts/resumes the generation workflow.

### `GET /music/generation-plans/{id}`

Returns status, candidate counts, costs, errors and lineage.

### `GET /music/candidates`

Filters candidates by experiment, arm, status, provider, track role, etc.

### `GET /music/candidates/{id}`

Returns candidate metadata and artifact list.

### `POST /music/candidates/{id}/refine`

Creates a P4 refinement request if permitted by policy.

### `GET /music/providers`

Returns provider/model capabilities and health information without exposing secrets.

---

# 68. P5 handoff contract

When P4 produces a candidate eligible for evaluation, emit an event/message such as:

```json
{
  "event_type": "music_candidate_ready_for_p5",
  "event_version": "1",
  "candidate_id": "...",
  "artifact_id": "...",
  "track_spec_id": "...",
  "experiment_id": "...",
  "content_arm_id": "...",
  "provider_id": "...",
  "model_id": "...",
  "measured_audio": {
    "duration_seconds": 198.37,
    "sample_rate": 44100,
    "channels": 2
  },
  "rights_status": "unknown",
  "provenance_complete": true
}
```

P5 should not need to reconstruct generation history from raw provider logs.

---

# 69. Event semantics

Events must be:

```text
versioned
idempotent
append-only
traceable
```

Suggested events:

```text
music_generation_plan_created
music_generation_started
music_generation_submitted
music_generation_completed
music_artifact_received
music_artifact_preflight_passed
music_artifact_preflight_failed
music_candidate_ready_for_p5
music_generation_failed
music_generation_cancelled
music_refinement_requested
```

---

# 70. LLM responsibilities

P4 may use an LLM for:

- prompt synthesis;
- lyric drafting;
- creative variant suggestions;
- semantic mapping between P3 attributes and provider terminology;
- generating human-readable generation notes.

The LLM must not be trusted for:

- license eligibility;
- cost accounting;
- exact artifact metadata;
- checksum generation;
- duration verification;
- provider job status;
- budget arithmetic;
- final rights approval;
- experiment identity.

Those are deterministic/application responsibilities.

---

# 71. Local-model vs strong-model routing

A sensible default:

```text
local model:
  bulk prompt expansion
  variant wording
  basic semantic mapping
  structured lyric drafting

strong model:
  difficult creative brief synthesis
  ambiguous semantic conflicts
  high-priority refinement planning
```

The model router must remain configurable.

Do not hard-code provider-specific model names into the content logic.

---

# 72. Prompt quality self-check

Before generation, run a deterministic prompt validator.

Check:

```text
hard constraints represented
no banned artist/song references
language specified where required
vocal mode consistent
duration context not confused
provider prompt within length limit
no unresolved placeholders
```

Example invalid placeholder:

```text
"[GENRE] dreamy [MOOD]"
```

The validator must reject it.

---

# 73. Semantic contradiction detection

Before sending a generation request, detect conflicts such as:

```text
instrumental = true
lyrics required = true
```

or:

```text
vocal_mode = instrumental
vocal_profile = female_lead
```

or:

```text
BPM range = 80–90
provider parameter = 120
```

Resolve only when the conflict is a clear provider mapping.

Otherwise mark `spec_conflict` and do not generate.

---

# 74. Provider prompt sanitization

After prompt construction and before submission:

```text
canonical prompt
  ↓
policy sanitizer
  ↓
provider-specific formatting
  ↓
final provider request
```

Store both:

```text
canonical_structured_prompt
provider_prompt
```

The provider prompt may differ because the provider syntax differs.

---

# 75. No hidden prompt mutation

If the provider adapter modifies the prompt for technical reasons, capture the exact final payload hash and, where permitted, the final text.

The database must never imply that the provider received an input it did not actually receive.

---

# 76. Output normalization

Do not normalize audio destructively as part of artifact intake.

P4 stores the provider output as the `source` artifact.

Any converted representation is a new artifact:

```text
source.wav
    ↓
normalized.wav
```

with:

```text
transformation_type = format_conversion
```

---

# 77. Multi-format support

P4 should normalize provider outputs into a canonical internal representation even if the provider returns:

```text
wav
mp3
m4a
flac
```

Keep original file where practical.

Recommended canonical internal evaluation format:

```text
WAV PCM
```

if storage/cost policy allows.

If not, use the repository's established lossless internal format.

Do not repeatedly lossy-transcode the same track.

---

# 78. Audio conversion policy

Conversions must be explicit:

```text
source artifact
→ conversion request
→ derived artifact
```

Record:

```text
input artifact
output artifact
converter
converter version
parameters
created_at
```

This is required for reproducibility.

---

# 79. Artifact security

Do not execute or trust arbitrary downloaded content.

Validate:

```text
expected MIME
known audio container
file signature
maximum file size
maximum duration
```

Quarantine suspicious files.

Provider-returned URLs must not be blindly followed without allowlist/validation where security policy requires it.

---

# 80. Network safety

Outbound requests must use:

```text
TLS
bounded timeouts
response size limits
retry limits
```

Do not expose provider URLs or signed URLs in application logs.

---

# 81. Secret management

Never store:

```text
API keys
OAuth client secrets
refresh tokens
provider credentials
```

in:

```text
prompt
raw request log
database payloads
Git
exception messages
```

Use the repository's secret manager/environment configuration.

---

# 82. Audit logs

Every major generation decision should be logged:

```text
who/what made decision
provider selected
why selected
policy version
budget state
creative variant
result
```

Automated actor:

```text
system:p4_generation_factory
```

Human intervention, if any, should be explicitly identified.

---

# 83. Observability metrics

Minimum P4 metrics:

```text
generation_plans_started
generation_plans_completed
generation_attempts
successful_generations
creative_failures
transport_failures
artifact_download_failures
preflight_failures
provider_fallbacks
credits_consumed
estimated_spend
actual_spend
mean_generation_latency
p95_generation_latency
artifact_size_bytes
candidate_yield
```

Tag metrics by:

```text
provider
model
brand
experiment
content_arm
track_role
```

Avoid high-cardinality tags such as raw prompt text.

---

# 84. Structured logging

Each log event should carry:

```text
trace_id
workflow_id
generation_plan_id
attempt_id
candidate_id
provider_id
model_id
event_type
status
```

Never put entire provider payloads into normal logs.

Persist sensitive/provider payloads only where necessary and permitted.

---

# 85. Failure states

Use explicit machine-readable failure codes:

```text
SPEC_INVALID
SPEC_CONFLICT
PROVIDER_UNAVAILABLE
PROVIDER_AUTH_FAILED
PROVIDER_RATE_LIMIT
PROVIDER_QUOTA_EXCEEDED
PROVIDER_REQUEST_INVALID
PROVIDER_POLICY_REJECTED
GENERATION_TIMEOUT
GENERATION_FAILED
ARTIFACT_NOT_FOUND
ARTIFACT_DOWNLOAD_FAILED
ARTIFACT_CORRUPT
ARTIFACT_UNSUPPORTED
PREFLIGHT_FAILED
BUDGET_EXCEEDED
CREDIT_EXCEEDED
LICENSE_UNKNOWN
LICENSE_INELIGIBLE
INTERNAL_ERROR
```

---

# 86. Recovery semantics

A workflow restart must determine the state from durable records.

Example:

```text
provider job submitted
process crashed
```

On restart:

```text
load attempt
↓
provider_job_id exists
↓
query provider status
↓
resume
```

Do not blindly submit a new generation.

---

# 87. Orphan job reconciliation

Run a periodic reconciliation job:

```text
P4 job says unknown/running
provider says completed
```

Then recover the artifact.

Likewise:

```text
P4 says queued
provider says cancelled
```

Mark the correct final state.

This is necessary when external APIs are asynchronous.

---

# 88. Budget reconciliation

A periodic job must reconcile:

```text
our estimated spend
our reservations
provider-reported usage where available
actual settled costs
```

Any unexplained discrepancy must generate an alert.

---

# 89. Duplicate detection

Before creating a new candidate, check:

```text
same generation request hash
same provider job
same artifact checksum
same creative variant
```

The same audio checksum may legitimately appear under multiple contextual candidates only if the system explicitly supports reuse; otherwise flag it.

---

# 90. Candidate reuse policy

P4 should support reusing a generated candidate for a later playlist only when allowed by P3/P6.

However, reuse must never create false experimental attribution.

If one track is reused:

```text
track_asset_id remains the same
playlist_usage_id differs
experiment attribution remains explicit
```

This distinction is critical for P9.

---

# 91. Experimental contamination protection

Before reusing a track:

```text
check whether the target experiment expects original generation
check whether reuse would invalidate the treatment
check whether track was already exposed to an audience
```

If uncertain, prohibit reuse automatically for strict causal experiments.

---

# 92. Candidate tagging

Each candidate should receive tags such as:

```text
provider:suno
model:vX
genre:rnb
mood:dreamy
use_case:late_night_drive
role:opener
variant:A
```

Tags are derived metadata and must not replace canonical typed fields.

---

# 93. Generation batch manifest

At the end of every plan, produce a summary manifest:

```json
{
  "generation_plan_id": "...",
  "requested": 8,
  "produced": 7,
  "technical_pass": 6,
  "technical_fail": 1,
  "provider_fail": 1,
  "total_estimated_cost": "...",
  "total_actual_cost": "...",
  "providers_used": ["..."],
  "models_used": ["..."],
  "completed_at": "..."
}
```

---

# 94. P4-generated report

P4 should produce an operator-readable summary:

```text
Generation Plan: GP-123
Track: TRACK-456

Requested candidates: 8
Generated: 7
Preflight passed: 6
Preflight failed: 1

Provider:
  primary: ...
  fallback: ...

Spend:
  estimated: ...
  actual: ...

Failures:
  1 provider timeout
  1 corrupt artifact

Ready for P5: 6
```

This is for operations/debugging, not for deciding what music is commercially best.

---

# 95. Testing strategy

Testing must cover:

```text
unit
property
contract
integration
workflow recovery
provider mock
artifact fixture
security
cost accounting
```

---

# 96. Provider contract tests

For every provider adapter test:

```text
request translation
response translation
capability reporting
error mapping
cost mapping
rights mapping
job status mapping
artifact retrieval
```

Use recorded fixtures or hand-built deterministic mock responses.

Never require live paid provider calls in unit tests.

---

# 97. Unit tests — prompt builder

Test:

```text
hard constraints included
creative freedom preserved
banned artist reference sanitized
banned song reference sanitized
instrumental mode coherent
vocal mode coherent
language preserved
duration interpretation correct
provider prompt length limits
version metadata recorded
```

---

# 98. Unit tests — generation planner

Test:

```text
candidate target honored
budget enforced
credit limit enforced
variant diversity
no mutation of fixed variables
provider capability compatibility
fallback eligibility
stopping rules
```

---

# 99. Unit tests — cost accounting

Test:

```text
reservation
settlement
refund/release
zero-cost provider
partial cost
provider error before billing
provider error after billing
```

Ensure monetary calculations use `Decimal`, not binary floating-point arithmetic.

---

# 100. Unit tests — artifact intake

Test:

```text
valid WAV
valid MP3
zero-byte file
corrupt file
wrong MIME
unexpected extension
missing duration
unsupported codec
checksum consistency
metadata extraction failure
```

---

# 101. Property tests

Important invariants:

```text
artifact checksum is deterministic
cost never becomes negative
candidate count never exceeds max candidate target unless explicitly configured
hard constraints never mutate
failed transport retry preserves request fingerprint
creative retry creates a new creative attempt identity
```

---

# 102. Workflow recovery tests

Simulate process crash after:

```text
budget reservation
provider submission
provider completion
artifact download
DB write before event
DB write after event
```

Restart the workflow and verify it resumes rather than double-generating or double-charging.

---

# 103. Concurrency tests

Simulate many workflows requesting generations concurrently.

Verify:

```text
provider concurrency cap obeyed
brand cap obeyed
budget not overspent
no duplicate reservation
no duplicate artifact records
```

---

# 104. Integration test scenario

Use a mock provider with behavior:

```text
attempt 1 → transient timeout
attempt 2 → successful job
artifact → valid audio
```

Expected:

```text
retry happens
same transport fingerprint
artifact stored once
candidate marked ready for P5
cost reconciled
```

---

# 105. Fallback test scenario

Mock:

```text
provider A → quota exceeded
provider B → available but different capability set
```

Verify provider B is selected only if compatible with P3's requirements and rights policy.

If not compatible:

```text
no generation
clear failure state
no silent downgrade
```

---

# 106. Experiment-integrity test

Construct a P3 specification where:

```text
language = fixed English
BPM = experimental 82–96
mood = bounded dreamy/melancholic
```

Attempt to create a variant that switches language to Korean.

Expected:

```text
variant rejected
experiment invariant violation recorded
no provider call
```

---

# 107. Candidate diversity test

Create 6 variants from one TrackSpec.

Verify that the resulting prompt set is not identical unless the provider/policy explicitly requests replication.

Also verify that differences exist only within allowed dimensions.

---

# 108. Rights test

Mock provider responses:

```text
license = eligible
license = unknown
license = ineligible
```

Verify candidate status contains the corresponding rights status and no status is silently promoted.

---

# 109. Performance tests

Benchmark:

```text
100 generation-plan creation requests
1,000 artifact metadata reads
high-concurrency plan scheduling
candidate search/filter queries
```

The generation workflow itself is external-provider-bound, so focus infrastructure benchmarks on orchestration/database/object storage behavior.

---

# 110. Security tests

Verify:

```text
API keys never appear in logs
signed provider URLs are not persisted unnecessarily
malformed audio cannot crash workers
path traversal is blocked
oversized download is rejected
MIME spoofing is detected
```

---

# 111. Acceptance criteria

P4 is complete only when all of the following are true.

## AC-01 — P3 ingestion

A valid P3 `TrackSpec` can produce a valid P4 `GenerationPlan` with no manual prompt writing.

## AC-02 — Provider abstraction

At least one real provider and one mock provider implement the canonical provider interface.

## AC-03 — Provider replacement

The orchestration layer can switch provider implementations without changes to P3 or P5 contracts.

## AC-04 — Candidate generation

A generation plan can produce multiple candidates and store each with complete lineage.

## AC-05 — Prompt provenance

Every candidate records the exact structured prompt, prompt version and provider-specific generation parameters used.

## AC-06 — Creative variants

Multiple candidates can vary within P3 creative latitude without mutating hard constraints.

## AC-07 — Cost controls

The factory enforces budget, credit and attempt limits.

## AC-08 — Idempotency

Transport retries do not accidentally create duplicate creative generations.

## AC-09 — Durable recovery

A process restart after provider submission does not cause duplicate generation when the external job can be reconciled.

## AC-10 — Artifact integrity

Downloaded audio receives a checksum, measured media metadata and immutable storage reference.

## AC-11 — Technical preflight

Obvious malformed/unsupported artifacts are rejected before P5.

## AC-12 — Rights metadata

Commercial-use status is explicitly recorded and cannot be inferred silently.

## AC-13 — Provider fallback

Fallback occurs only when policy/capability/rights conditions permit it.

## AC-14 — P5 handoff

P5 receives a self-contained candidate-ready event containing all required identifiers and measured artifact metadata.

## AC-15 — Observability

Operators can inspect provider, attempt, candidate, cost, error and artifact status without reading raw application logs.

## AC-16 — Tests

Unit, contract, integration, recovery, cost and security tests pass.

---

# 112. P4 completion smoke test

Use one actual permitted provider configuration and one P3 fixture.

Run:

```text
create generation plan
↓
create 2–3 candidates
↓
download artifacts
↓
run preflight
↓
store provenance
↓
emit P5-ready events
```

Then verify the database/object store relationship manually or through an automated consistency check.

The smoke test should be small enough to avoid unnecessary provider cost.

---

# 113. Production rollout strategy

Do not enable unrestricted autonomous generation immediately.

Use three operating modes.

## Mode 1 — dry run

No external generation call.

Produces:

```text
provider selection
prompt
variants
cost estimate
```

for inspection.

## Mode 2 — shadow / capped

Real generation with strict:

```text
small candidate cap
small budget
limited concurrency
```

## Mode 3 — autonomous

Normal P2/P3-driven production within configured budget and policy.

The mode should be a runtime configuration flag.

---

# 114. Rollback strategy

Any provider adapter or prompt-builder release must be versioned.

If a deployment causes:

```text
dramatic failure rate
cost spike
artifact corruption
unexpected prompt mutation
rights-status regression
```

the provider/model/prompt version can be rolled back without deleting historical artifacts.

---

# 115. Configuration versioning

Version at least:

```text
provider_policy_version
prompt_template_version
prompt_builder_version
generation_policy_version
cost_policy_version
rights_policy_version
```

Store these on generation plans and attempts.

---

# 116. Decision log requirements

When implementation choices materially change behavior, record a decision entry.

Examples:

```text
Why provider fallback order changed
Why canonical audio format chosen
Why candidate target changed
Why a provider capability mapping changed
Why a prompt sanitizer rule was introduced
```

Do not silently alter economic or rights behavior.

---

# 117. What the vibe-coding AI may choose

The implementation AI may choose:

- exact Python package for media probing;
- exact object-storage SDK;
- async HTTP client;
- repository pattern;
- Pydantic/dataclass usage;
- internal class layout;
- queue implementation if the project does not yet have a durable workflow engine;
- database index design;
- exact prompt wording inside the structured framework;
- exact provider adapter internal organization.

Provided the external behavior and invariants remain intact.

---

# 118. What the vibe-coding AI must not decide silently

Do not silently change:

- P3 hard constraints;
- experiment identity;
- content-arm identity;
- provider licensing semantics;
- commercial-use status;
- provenance requirements;
- cost reservation semantics;
- idempotency behavior;
- artifact immutability;
- P4/P5 responsibilities;
- candidate lineage;
- transport-vs-creative retry semantics;
- provider fallback eligibility;
- publication eligibility.

If an implementation decision affects any of these, document it in the repository decision log.

---

# 119. Reasonable flexibility

This specification does not require:

- one particular music provider;
- one particular LLM;
- one specific workflow engine implementation;
- one exact audio codec;
- one exact number of candidate generations;
- one exact prompt style;
- one exact cost model.

The design requires that those choices remain configurable, traceable and replaceable.

---

# 120. Recommended implementation order

## P4.1 — Domain contracts

Implement:

```text
ProviderCapabilities
GenerationRequest
GenerationResponse
GenerationPlan
GenerationAttempt
GeneratedCandidate
AudioArtifact
RightsSnapshot
CostRecord
```

## P4.2 — Persistence

Create migrations/repositories for all P4 entities.

## P4.3 — Provider registry

Implement provider/model registration and capabilities.

## P4.4 — Mock provider

Build the deterministic provider used for all tests.

## P4.5 — P3 integration

Consume actual `TrackSpec` records and validate constraints.

## P4.6 — Prompt builder

Implement structured canonical brief → provider prompt.

## P4.7 — Generation planner

Implement candidate count, variant planning, cost checks and stopping rules.

## P4.8 — Real provider adapter

Implement one production provider behind the interface.

## P4.9 — Durable generation workflow

Implement submission, polling, retries, reconciliation and cancellation.

## P4.10 — Artifact intake

Implement download, checksum, media probe and object storage.

## P4.11 — Candidate bank

Implement candidate state and search.

## P4.12 — P5 event contract

Emit candidate-ready events.

## P4.13 — Observability

Metrics, logs, run summaries and cost dashboards.

## P4.14 — Recovery/security tests

Complete crash recovery, quota, secrets, malformed artifact and concurrency tests.

## P4.15 — Capped production smoke test

Run a small real generation batch.

## P4.16 — Autonomous-mode gate

Enable autonomous execution only after all acceptance criteria pass.

---

# 121. Example end-to-end request

P3 supplies:

```json
{
  "track_spec_id": "TRACK-001",
  "experiment_id": "EXP-021",
  "content_arm_id": "rnb_late_night_drive_en_vocal",
  "role": "opener",
  "hard_constraints": {
    "language": "english",
    "vocal_mode": "vocal"
  },
  "bounded_preferences": {
    "bpm": [82, 96],
    "mood": ["dreamy", "melancholic"],
    "energy": "low_medium"
  },
  "creative_latitude": [
    "instrumentation",
    "melody",
    "arrangement",
    "exact_lyrics"
  ],
  "candidate_target": 6
}
```

P4 creates:

```text
GenerationPlan GP-001

Variant A → intimate / sparse
Variant B → warmer / fuller
Variant C → stronger hook
Variant D → atmospheric
Variant E → acoustic accent
Variant F → rhythmic accent
```

Then:

```text
provider selection
↓
6 generation attempts
↓
5 successful artifacts
↓
1 malformed artifact
↓
preflight
↓
5 candidates ready for P5
```

No human prompt-writing is required.

---

# 122. Example refinement request

P5 later reports:

```text
Candidate C

Strengths:
  arrangement = strong
  sound quality = strong

Weakness:
  vocal articulation inconsistent
```

P4 receives:

```json
{
  "candidate_id": "C",
  "refinement_type": "vocal_coherence",
  "preserve": ["genre", "mood", "bpm", "overall_arrangement"],
  "modify": ["vocal delivery", "lyric density"]
}
```

P4 produces revision C2 while retaining C.

---

# 123. Example provider failure

Provider A:

```text
quota exceeded
```

P4 evaluates Provider B:

```text
supports required language = yes
supports vocals = yes
commercial use = eligible
cost = within budget
```

Then:

```text
select B
record fallback event
continue generation
```

If Provider B has:

```text
commercial use = unknown
```

and the policy is:

```text
commercial_use_required = true
```

then:

```text
do not generate
mark provider incompatible
```

---

# 124. Example crash recovery

State before crash:

```text
attempt_id = A123
provider_job_id = JOB999
status = processing
```

After restart:

```text
load A123
↓
query provider JOB999
↓
provider = completed
↓
download artifact
↓
checksum
↓
mark downloaded
↓
preflight
```

The system must not submit JOB1000 unless explicitly creating a new creative attempt.

---

# 125. Data-quality and reconciliation dashboard

At minimum expose:

```text
plans requested
plans completed
candidates requested
candidates generated
candidate technical yield
provider failures
artifact failures
average cost/candidate
fallback rate
unknown-license count
orphan-job count
unreconciled-cost count
```

These are operating-health signals, not performance signals.

---

# 126. P4-to-P5 boundary — final rule

P4's final responsibility is:

> **Produce a technically valid, provenance-complete, policy-annotated candidate artifact and hand it to P5.**

P4 must not declare:

```text
this song is excellent
this song will perform well
this song is safe to publish
this song is copyright-safe
```

Those are downstream decisions or evidence-backed assessments.

The correct P4 outcome is:

```text
Candidate generated.
Technical preflight passed.
Commercial-use status = eligible/unknown/ineligible.
Provenance complete.
Ready for P5 evaluation.
```

---

# 127. Definition of done

P4 is complete when the system can receive a valid P3 `TrackSpec`, autonomously construct a generation plan, select a compatible provider, generate multiple creative candidates under explicit budget/credit limits, survive ordinary provider/network failures, persist immutable audio artifacts and complete provenance, reconcile asynchronous provider jobs, and hand P5 a clean candidate queue — without requiring a human to write prompts, choose individual generations, or manually move files between systems.

The architecture should make it possible to replace the music-generation provider later without changing P2, P3, P5, or the catalog/data model.
