# P6 — Playlist, Visual & Video Factory
## Detailed Implementation Plan

**Document status:** Implementation-ready specification  
**Phase:** P6 of the Autonomous AI Music Media Engine  
**Primary objective:** Convert P5-cleared music assets into coherent, branded, platform-ready audiovisual packages without performing platform publishing.

---

# 1. Purpose

P6 is the media-assembly layer between the quality/release gate and platform publishing.

P5 answers:

> **Which music/artifacts are technically usable and cleared for downstream release?**

P6 answers:

> **How do we turn those cleared assets into a coherent playlist, visual identity, long-form video, thumbnail set, chapters, tracklist, and reusable short-form source assets?**

P6 must not silently change the musical experiment defined by P3/P4/P5. It may choose among permitted presentation options, but it must preserve experiment identity and content provenance.

P6 is a **production system**, not a publishing system and not the final market-decision engine.

---

# 2. Scope

## 2.1 In scope

P6 must implement:

1. P5 release-manifest ingestion.
2. Playlist/track-set validation.
3. Track sequencing and transition planning.
4. Playlist duration planning.
5. Intro/outro and optional interstitial policy.
6. Brand-profile loading and validation.
7. Cover artwork generation/orchestration.
8. Long-form visual concept selection.
9. Procedural visual composition.
10. Motion/animation asset assembly.
11. Thumbnail candidate generation.
12. Metadata-package generation.
13. Tracklist/chapter generation.
14. Video rendering.
15. Audio/video synchronization.
16. Render-time technical QA.
17. Asset caching and deduplication.
18. Artifact manifests and checksums.
19. Short-form source-clip extraction.
20. Media-versioning and reproducibility.
21. Cost accounting.
22. Durable workflows and retry/recovery.
23. APIs for P7.
24. Automated tests and rendering fixtures.
25. Human-review routing for exceptional visual or packaging cases.

## 2.2 Explicitly out of scope

Do not implement in P6:

- YouTube upload/publishing.
- YouTube OAuth.
- YouTube scheduling.
- Spotify distribution.
- DSP submission.
- Market-opportunity scoring.
- A&R selection of the underlying genre.
- Bandit allocation.
- Financial optimization.
- Final copyright adjudication.
- External paid promotion.
- Automated social posting.

P6 may generate outputs that P7/P8 later publish.

---

# 3. Design principles

## 3.1 Preserve experiment identity

Every media package must carry forward:

```text
experiment_id
experiment_family_id
content_arm_id
production_directive_id
song_ids
```

Do not allow the visual layer to silently introduce a variable that invalidates the intended experiment.

For example, if P3 is testing a music concept while holding visual packaging constant, P6 must not randomly change the visual treatment for only one treatment arm unless that variation is explicitly declared.

## 3.2 Separate content from presentation

The same cleared song may be used in multiple legitimate presentation contexts, but each presentation must have its own immutable artifact lineage.

## 3.3 Brand consistency without creative paralysis

Brand rules define the envelope:

```text
must follow
should follow
allowed to vary
```

The AI is encouraged to explore within the allowed region rather than producing mechanically identical videos.

## 3.4 Deterministic rendering

Given identical:

```text
source assets
brand version
layout version
render version
render parameters
font versions
```

P6 should reproduce the same output byte-for-byte where practical, or at minimum produce the same visual/audio result with a documented nondeterministic encoder exception.

## 3.5 Render late, validate early

Do as much validation as possible before expensive full-length rendering.

## 3.6 Don't upscale every job to the most expensive visual pipeline

Use tiered rendering:

```text
Tier 0 = static/procedural motion
Tier 1 = advanced compositing
Tier 2 = generated motion/video assets
```

Route based on content requirements and expected value.

---

# 4. P6 input contract

P6 consumes a **P5 Release Manifest**.

Minimum fields:

```json
{
  "release_manifest_id": "rm_123",
  "experiment_id": "exp_456",
  "content_arm_id": "arm_789",
  "brand_id": "neon_seoul",
  "tracks": [
    {
      "song_id": "song_001",
      "artifact_id": "artifact_001",
      "title": "After Midnight",
      "duration_seconds": 247,
      "audio_uri": "s3://...",
      "audio_checksum": "...",
      "provenance_status": "cleared",
      "release_constraints": {}
    }
  ],
  "required_duration_minutes": 60,
  "presentation_profile": "longform_mix_v1",
  "visual_profile": "neon_seoul_rain_v3",
  "thumbnail_profile": "neon_seoul_thumb_v2",
  "metadata_profile": "global_english_v1",
  "policy_flags": {
    "ai_disclosure_required": true
  }
}
```

P6 must reject manifests missing required identifiers, cleared artifacts, or required profile versions.

It must never silently substitute a different song because a requested asset is unavailable.

---

# 5. P6 output contract

P6 produces a **Media Package**.

Minimum output:

```json
{
  "media_package_id": "mp_123",
  "release_manifest_id": "rm_123",
  "experiment_id": "exp_456",
  "brand_id": "neon_seoul",
  "video": {
    "master_uri": "s3://...",
    "checksum": "...",
    "duration_seconds": 3612,
    "render_profile": "yt_longform_1080p_v1"
  },
  "audio": {
    "master_uri": "s3://...",
    "checksum": "..."
  },
  "thumbnails": [
    {"id": "thumb_a", "uri": "s3://..."},
    {"id": "thumb_b", "uri": "s3://..."},
    {"id": "thumb_c", "uri": "s3://..."}
  ],
  "tracklist": [],
  "chapters": [],
  "metadata_candidates": [],
  "short_source_assets": [],
  "render_report": {},
  "status": "ready_for_publish"
}
```

P7 must consume this contract rather than reading internal P6 database tables.

---

# 6. Core concepts

P6 introduces five primary concepts.

## 6.1 Playlist Session

A curated listening experience with a selected track list and sequence.

## 6.2 Visual Session

The visual narrative/configuration applied to the playlist.

## 6.3 Presentation Variant

One permitted packaging/render variant of the same underlying experiment.

Examples:

```text
thumbnail_A
thumbnail_B
thumbnail_C
visual_treatment_1
visual_treatment_2
```

## 6.4 Media Package

The complete set of platform-ready assets.

## 6.5 Render Job

A durable execution unit that transforms source assets + immutable configuration into output artifacts.

---

# 7. Repository structure

Use the existing project's conventions where present. Otherwise a logical structure similar to this is recommended:

```text
src/
  media_factory/
    domain/
      entities.py
      enums.py
      value_objects.py
    ingestion/
      release_manifest.py
      validators.py
    playlist/
      planner.py
      sequencing.py
      transitions.py
      duration.py
      chapters.py
    brand/
      profiles.py
      validator.py
      theme.py
    visuals/
      planner.py
      compositor.py
      motion.py
      assets.py
      templates.py
    artwork/
      generator.py
      selector.py
      validator.py
    thumbnails/
      generator.py
      scorer.py
      validator.py
    metadata/
      generator.py
      templates.py
      validator.py
    rendering/
      jobs.py
      ffmpeg.py
      profiles.py
      progress.py
      reconciliation.py
    shorts/
      candidate_extractor.py
      clip_planner.py
    quality/
      preflight.py
      postrender.py
    storage/
      models.py
      repositories.py
      manifests.py
    api/
      routes.py
      schemas.py
    jobs/
      orchestration.py
    observability/
      logging.py
      metrics.py

tests/
  unit/
  integration/
  render/
  fixtures/
  golden/
```

Exact filenames can differ if the repository already has an established architecture.

---

# 8. Data model

## 8.1 `playlist_sessions`

Fields:

```text
id
release_manifest_id
experiment_id
content_arm_id
brand_id
status
requested_duration_seconds
actual_duration_seconds
sequence_version
sequence_checksum
created_at
updated_at
```

Statuses:

```text
planned
validated
assembled
render_ready
rendering
rendered
failed
quarantined
```

---

## 8.2 `playlist_tracks`

Fields:

```text
id
playlist_session_id
song_id
source_artifact_id
position
start_offset_seconds
end_offset_seconds
transition_type
transition_duration_seconds
sequence_reason
created_at
```

`position` must be unique within a playlist session.

---

## 8.3 `visual_sessions`

Fields:

```text
id
playlist_session_id
brand_id
brand_version
visual_profile
visual_profile_version
visual_plan_json
status
created_at
updated_at
```

---

## 8.4 `visual_assets`

Fields:

```text
id
visual_session_id
asset_type
source_type
provider
provider_asset_id
uri
checksum
width
height
duration_seconds
fps
license_status
provenance_id
created_at
```

Asset types:

```text
background
foreground
character
environment
overlay
particle
transition
logo
text_texture
animation
```

---

## 8.5 `thumbnail_variants`

Fields:

```text
id
media_package_id
variant_key
source_assets
layout_version
uri
checksum
width
height
text_payload
predicted_ctr_score nullable
status
created_at
```

Do not treat a predicted CTR score as an observed result.

---

## 8.6 `media_packages`

Fields:

```text
id
release_manifest_id
experiment_id
playlist_session_id
visual_session_id
status
package_version
manifest_uri
created_at
updated_at
```

---

## 8.7 `render_jobs`

Fields:

```text
id
media_package_id
render_profile
render_version
job_status
priority
attempt_count
worker_id
started_at
finished_at
input_manifest_hash
output_checksum
error_code
error_detail
```

---

# 9. Playlist composition

P6 must not re-decide which songs are market opportunities. It receives the permitted song set from upstream.

It may, however, determine the **order and presentation** according to the P3/P5 release manifest.

## 9.1 Sequence objective

Optimize for a coherent listening experience while respecting declared experiment constraints.

Candidate factors:

```text
BPM continuity
energy continuity
key compatibility where meaningful
vocal density
instrument density
intro/outro compatibility
mood trajectory
track duration
listener fatigue
```

## 9.2 Sequence algorithm

Use a scored optimization approach rather than an opaque LLM-only sequence.

Suggested process:

```text
candidate permutations
      ↓
hard constraint filter
      ↓
transition scoring
      ↓
energy-curve scoring
      ↓
redundancy penalty
      ↓
duration adjustment
      ↓
select best valid sequence
```

For large track pools, use heuristic/local search instead of factorial enumeration.

## 9.3 Hard constraints

Examples:

```text
track cannot repeat within one playlist
required track must appear
forbidden track cannot appear
maximum same-vocal-style streak
maximum consecutive high-energy tracks
required duration window
```

The exact constraints come from the release manifest/profile.

## 9.4 Soft constraints

Examples:

```text
prefer smooth BPM changes
prefer mood arc
prefer stronger opening
prefer lower-energy closer
```

Do not fail the whole playlist because a soft preference cannot be satisfied.

---

# 10. Track-transition handling

P6 should support at least:

```text
hard_cut
short_crossfade
long_crossfade
silence_gap
ambient_bridge
custom_transition
```

Transition lengths must be configurable.

Do not automatically normalize every transition to one duration.

For long-form music playlists, default to smooth transitions, but allow the presentation profile to override them.

Any inserted non-musical audio must be explicitly represented in the playlist manifest.

Never insert hidden ads, calls-to-action, or unrelated speech.

---

# 11. Intro/outro policy

The media factory may optionally add:

```text
brand intro
very short ident
outro
```

These are presentation assets and must not alter track provenance.

Defaults should favor a minimal intro so the first musical hook is not unnecessarily delayed.

The profile must define:

```text
intro_enabled
max_intro_duration
outro_enabled
max_outro_duration
```

If intro duration exceeds configured limits, the preflight gate fails.

---

# 12. Brand system

Create a structured `BrandProfile`.

Example:

```json
{
  "brand_id": "neon_seoul",
  "version": "3",
  "identity": {
    "logo": "...",
    "tagline": "..."
  },
  "visual": {
    "palette": ["..."],
    "contrast_min": 4.5,
    "camera_language": ["cinematic", "night", "slow_motion"],
    "motifs": ["rain", "neon", "sedan", "apartment_windows"],
    "forbidden": ["generic_stock_style", "random_logo_variants"]
  },
  "typography": {
    "primary_font": "...",
    "secondary_font": "..."
  },
  "thumbnail": {
    "layout_family": "hero_subject_right",
    "max_words": 6
  }
}
```

The implementation should allow brand profiles to evolve by version.

Never mutate a brand profile in place after a media package has started production.

---

# 13. Visual concept planner

Input:

```text
brand profile
presentation profile
playlist concept
market/experiment visual constraints from P3
```

Output:

```text
visual plan
asset requirements
scene list
camera/motion rules
color rules
text placement rules
transition rules
```

The planner should be LLM-assisted where useful, but the resulting plan must validate against a schema.

Example:

```json
{
  "visual_theme": "rainy_seoul_night",
  "scenes": [
    {
      "duration_seconds": 180,
      "setting": "street",
      "motion": "slow_vehicle_pass",
      "camera": "locked_medium",
      "text": null
    }
  ],
  "loop_policy": {
    "allow_reuse": true,
    "max_consecutive_reuse": 1
  }
}
```

Do not allow unconstrained LLM-generated scene lists to directly drive an expensive render. Validate first.

---

# 14. Visual asset strategy

Use a tiered hierarchy:

### Tier A — procedural

Use:

```text
animated gradients
particles
rain
light flicker
parallax
noise
camera movement
shape animation
```

for low-cost continuity.

### Tier B — generated stills + motion

Use generated still images with:

```text
pan
zoom
parallax
mask animation
light animation
rain overlay
reflection animation
```

### Tier C — generated motion/video segments

Use when a specific visual requires genuine motion that procedural techniques cannot provide.

The AI should be free to select the tier based on the visual objective and cost budget.

---

# 15. Avoiding visual repetitiveness

The system must explicitly track:

```text
visual_asset_usage_count
scene_template_usage_count
thumbnail_template_usage_count
character_usage_count
background_usage_count
```

Create configurable repetition penalties.

Do not mechanically prohibit reuse; consistent branding sometimes requires recurring motifs.

The rule is:

> reuse the brand language, not the exact finished composition.

---

# 16. Visual asset provenance

Every generated/borrowed asset must carry:

```text
asset_id
provider
model/version
prompt or generation spec
creation timestamp
license status
source URI if external
checksum
brand profile version
```

Do not embed credentials or private prompts into public media metadata.

---

# 17. Artwork generation

For each playlist, generate at least one canonical cover-art candidate.

Optional multiple variants may be created for experimentation.

The artwork generator should receive:

```text
brand profile
playlist concept
visual profile
required dimensions/aspect ratio
safe-text region
```

Preflight must validate:

```text
correct dimensions
valid image format
no accidental borders
no transparent background if disallowed
logo usage rules
legibility
no unexpected text artifacts
```

---

# 18. Thumbnail factory

Generate multiple candidates from the same experiment-preserving brief.

Typical variants may alter:

```text
subject position
text length
crop
scene emphasis
logo treatment
contrast
```

Do not alter the underlying music concept in a thumbnail experiment.

For example:

```text
VALID:
Night Drive | 60 MIN
vs
2AM Seoul | R&B Mix
```

```text
INVALID without explicit experiment design:
Night R&B
vs
Workout EDM
```

The second changes the content promise rather than merely packaging it.

---

# 19. Thumbnail selection

P6 may generate:

```text
A/B/C candidates
```

and compute a model-based packaging score.

The score must remain labeled:

```text
predicted
```

until P9 provides real CTR data.

Store predicted and actual values separately.

P6 should not automatically discard all but one thumbnail unless downstream publishing requires a single candidate.

---

# 20. Metadata package

P6 should generate candidate metadata but not publish it.

Fields:

```text
title
subtitle if used
description
tracklist
chapters
keywords/tags where supported
hashtags where appropriate
pinned-comment candidate
```

Metadata should be generated from the actual playlist contents rather than from the original idea alone.

The system must not invent:

```text
artists who did not participate
real-world venues
fake credits
fake awards
fake playlist placements
```

---

# 21. Tracklist and chapters

Tracklist must be derived from the final sequence, not from the pre-render plan.

Example:

```text
00:00 After Midnight
04:07 Neon Rain
08:31 Last Train Home
...
```

The chapter timestamps must reflect actual final timeline offsets after transitions and intro/outro adjustments.

Do not manually hard-code timestamps in templates.

---

# 22. Render profiles

Define named render profiles, for example:

```text
yt_longform_1080p_v1
yt_longform_1440p_v1
archive_master_v1
short_vertical_1080x1920_v1
preview_720p_v1
```

Each profile defines:

```text
resolution
fps
video codec
video bitrate or quality mode
audio codec
sample rate
container
color-space expectations
max file size target if applicable
```

Profiles must be versioned.

Never let a worker silently use its own defaults if the job references a named render profile.

---

# 23. FFmpeg pipeline

The renderer should use FFmpeg or an equivalent deterministic media engine.

Conceptual pipeline:

```text
cleared audio assets
      ↓
playlist timeline
      ↓
transition/mix assembly
      ↓
visual scene timeline
      ↓
overlays / text / logo
      ↓
audio-video mux
      ↓
encode
      ↓
container validation
```

Do not use shell string concatenation for untrusted file names.

Pass arguments as structured argv arrays.

---

# 24. Audio assembly

P6 is not the artistic mastering stage; P5/P4 handle upstream quality.

P6 may perform presentation-level operations:

```text
crossfade
trim to exact declared offsets
intro/outro insertion
silence insertion if profile requires it
```

Do not apply destructive normalization unless explicitly specified by the media profile.

Always retain the source cleared audio artifact.

---

# 25. Video timeline model

Create an internal timeline model instead of rendering directly from arbitrary FFmpeg filters.

Example:

```json
{
  "canvas": {
    "width": 1920,
    "height": 1080,
    "fps": 30
  },
  "layers": [
    {
      "type": "background",
      "start": 0,
      "end": 3600,
      "asset": "asset_001"
    },
    {
      "type": "overlay",
      "start": 0,
      "end": 3600,
      "effect": "rain"
    }
  ]
}
```

The timeline must be serializable and checksum-able.

---

# 26. Motion system

Support:

```text
position keyframes
scale keyframes
opacity
rotation
blur
parallax
mask reveal
particle overlays
light flicker
color grading parameters
```

Prefer reusable primitives over hundreds of bespoke hard-coded effects.

---

# 27. Text rendering

Text must be placed using a safe-area system.

Store:

```text
text content
font
size
weight
line height
position
alignment
max width
safe-area padding
```

The system should render a text-preview image before the full video render for validation.

This catches:

```text
overflow
font substitution
cropping
unexpected line breaks
poor contrast
```

---

# 28. Rendering workflow

Use durable states:

```text
QUEUED
→ PREFLIGHT
→ ASSETS_READY
→ TIMELINE_BUILT
→ PREVIEW_RENDERED
→ PREVIEW_VALIDATED
→ FULL_RENDERING
→ FULL_RENDERED
→ POST_RENDER_QA
→ PACKAGED
```

Failure states:

```text
BLOCKED
FAILED_RETRYABLE
FAILED_TERMINAL
QUARANTINED
```

---

# 29. Preview render

Before full rendering, produce a short preview, ideally:

```text
5–30 seconds
```

containing representative:

```text
intro or opening
midpoint visual
text overlay
transition
outro/ending
```

Preview validation should catch most visual configuration bugs without waiting for a full-length render.

---

# 30. Post-render validation

At minimum validate:

```text
file exists
checksum exists
container opens
expected duration within tolerance
expected resolution
expected FPS
audio track exists
video track exists
audio sample rate
channel layout
no unexpected extra tracks
no zero-byte/corrupt segments
```

Also sample frames at configurable intervals and run lightweight visual checks for:

```text
black screen
frozen frame
severe brightness anomaly
text clipping
missing logo where required
```

Do not attempt to infer subjective artistic quality solely from these checks.

---

# 31. Render cost management

Track per render:

```text
GPU_seconds
CPU_seconds
wall_clock_seconds
storage_bytes
network_bytes
estimated_cost
```

Before starting a render, estimate cost and compare against configured package budget.

If projected cost exceeds a hard budget:

```text
do not automatically exceed it
```

Instead downgrade only when an explicit fallback policy permits it, otherwise quarantine for review.

---

# 32. Asset caching

Identical inputs must reuse cached derived assets where safe.

Cache key should include:

```text
source_checksum
renderer_version
render_parameters
brand_version
visual_profile_version
```

If any changes, invalidate the cache.

Never reuse a previous output merely because the filename is the same.

---

# 33. Media manifest

Every media package must ship with a machine-readable manifest:

```json
{
  "media_package_id": "mp_123",
  "package_version": "1",
  "experiment_id": "exp_456",
  "inputs": [
    {
      "asset_id": "song_001",
      "checksum": "..."
    }
  ],
  "presentation": {
    "brand_version": "3",
    "visual_profile": "v3",
    "render_profile": "yt_longform_1080p_v1",
    "layout_version": "12"
  },
  "outputs": [
    {
      "type": "master_video",
      "uri": "...",
      "checksum": "..."
    }
  ]
}
```

P7 must be able to reconstruct exactly what it is about to publish from this manifest.

---

# 34. Short-form source extraction

P6 should prepare candidate source segments for P11 without publishing them.

Each candidate:

```text
clip_id
song_id
start
end
reason
visual_focus
hook_score_model
source_video_timeline
```

Candidate extraction may use:

```text
chorus boundaries
energy spikes
lyric hooks
instrumental hooks
visual events
```

The candidate does not become a Short automatically in P6; P11 controls that lifecycle.

---

# 35. Experiment integrity

Every presentation asset must declare which experimental dimensions it changes.

For example:

```json
{
  "varies": ["thumbnail_text"],
  "holds_constant": [
    "music",
    "playlist_sequence",
    "visual_world",
    "brand"
  ]
}
```

P6 must reject inconsistent packages where declared constants changed unexpectedly.

Example:

```text
P3 says visual_world = rainy_asian_city
P6 generated luxury tropical beach
```

This must fail validation.

---

# 36. Human review policy

P6 should be autonomous by default.

Escalate only when:

```text
brand rule conflict
visual safety policy conflict
missing licensed asset
renderer ambiguity
large unexpected cost
package integrity mismatch
required asset unavailable
experiment-integrity violation
```

Normal creative choice should not require a human.

---

# 37. LLM responsibilities

LLMs may:

- suggest visual concepts;
- create scene descriptions;
- generate metadata candidates;
- create title alternatives;
- identify likely clip moments;
- propose creative packaging variations.

LLMs must not:

- invent source assets;
- invent licenses;
- claim a rendered artifact exists when it does not;
- override P5 rights status;
- change experiment identity without recording it;
- silently change song order when sequence constraints forbid it;
- calculate technical media properties without inspecting actual artifacts.

Technical facts come from media parsers and render validators.

---

# 38. Metadata safety

Do not generate fabricated statements such as:

```text
"Official Spotify editorial playlist"
"Featured by YouTube Music"
"#1 in Korea"
"Award-winning"
```

unless independently supported and explicitly supplied as factual source data.

Do not present AI-generated fictional artist narratives as real human biographies unless the brand framework explicitly and transparently defines them as fictional.

---

# 39. Idempotency

Every P6 request must have an idempotency key.

Example:

```text
media_package:{release_manifest_id}:{presentation_profile}:{version}
```

If the same request is received again:

```text
return existing package/job
```

unless explicitly forced to rebuild.

Forced rebuilds create a new render version rather than overwriting an old artifact.

---

# 40. Concurrency

Prevent two workers from rendering the same package simultaneously.

Use:

```text
DB advisory lock
or distributed lock
```

with an expiry/lease and crash-recovery behavior.

Do not rely only on an in-memory process lock.

---

# 41. Recovery after worker failure

If a worker dies during rendering:

1. Mark worker lease expired.
2. Detect incomplete output.
3. Validate whether a completed artifact exists.
4. Resume or restart according to renderer capability.
5. Never mark the package complete based solely on worker heartbeat.

Output integrity must be verified after recovery.

---

# 42. File naming

Use immutable content-addressed or ID-based paths.

Example:

```text
media/{brand}/{experiment}/{package_id}/video/master_v1.mp4
media/{brand}/{experiment}/{package_id}/audio/master_v1.wav
media/{brand}/{experiment}/{package_id}/thumb/a.webp
```

Do not store important artifacts as:

```text
final_final_v2_really_final.mp4
```

The database manifest is authoritative.

---

# 43. Storage lifecycle

Separate:

```text
source
intermediate
preview
release
archive
```

Define retention policies independently.

Do not delete source/lineage artifacts needed to reproduce a release.

Derived intermediates may be deleted if their generation recipe is reproducible.

---

# 44. Security

Never pass arbitrary user/provider-controlled strings directly into shell commands.

Validate all file URIs and allowed storage roots.

Do not allow a metadata field to escape the intended output directory.

Sanitize external filenames.

Keep API keys out of render jobs unless the renderer explicitly requires authenticated access.

---

# 45. Observability

Metrics:

```text
p6_packages_created
p6_packages_failed
p6_render_success_rate
p6_render_retry_rate
p6_render_duration_seconds
p6_gpu_seconds
p6_cpu_seconds
p6_preview_failure_rate
p6_postrender_failure_rate
p6_thumbnail_candidates_per_package
p6_asset_cache_hit_rate
p6_storage_bytes
p6_estimated_cost
```

Structured logs must include:

```text
media_package_id
release_manifest_id
experiment_id
render_job_id
worker_id
render_profile
render_version
```

---

# 46. API contract for P7

Implement a clean API.

## `POST /media/packages`

Creates a media package request from a release manifest.

## `GET /media/packages/{id}`

Returns package state and manifest summary.

## `GET /media/packages/{id}/manifest`

Returns the publishable manifest.

## `GET /media/render-jobs/{id}`

Returns render status/progress.

## `POST /media/packages/{id}/rebuild`

Explicitly create a new render version.

## `GET /media/packages/{id}/artifacts`

Returns artifact URIs/checksums/types.

P7 must not need to know how FFmpeg, image generation or sequencing works.

---

# 47. Example end-to-end workflow

```text
P5 Release Manifest
        ↓
validate cleared songs
        ↓
create playlist session
        ↓
sequence tracks
        ↓
calculate final timeline
        ↓
load brand profile
        ↓
create visual plan
        ↓
materialize/generate visual assets
        ↓
create artwork
        ↓
create thumbnail candidates
        ↓
create metadata candidates
        ↓
build timeline
        ↓
preview render
        ↓
preview QA
        ↓
full render
        ↓
post-render QA
        ↓
build final manifest
        ↓
READY_FOR_PUBLISH
```

---

# 48. Example failure workflow

```text
Full render starts
      ↓
worker crashes
      ↓
lease expires
      ↓
job reconciler checks artifacts
      ↓
partial MP4 detected
      ↓
partial artifact quarantined
      ↓
job retried
      ↓
render succeeds
      ↓
post-render QA
      ↓
package complete
```

P7 should never see the quarantined partial file.

---

# 49. Tests

## 49.1 Unit tests

Minimum coverage:

```text
playlist duration
sequence scoring
hard constraint enforcement
transition calculations
chapter timestamp generation
safe-area calculations
brand validation
thumbnail text overflow
asset cache keys
render profile parsing
manifest checksum
idempotency keys
```

## 49.2 Property tests

Examples:

```text
adding a track cannot reduce actual playlist duration before transitions
chapter timestamps are monotonically increasing
no track position duplicates
render-profile dimensions remain positive
media package cannot contain an unvalidated asset
cache key changes when source checksum changes
```

## 49.3 Render golden tests

Maintain short deterministic fixtures:

```text
3-track audio fixture
2 visual assets
1 overlay
1 transition
1 logo
```

Render a known short package and compare:

```text
media metadata
frame hashes or perceptual hashes
audio checksum when deterministic
expected duration
```

Do not require exact full-video byte equality if the encoder introduces known metadata/timestamp nondeterminism; instead verify semantic equivalence with documented tolerances.

## 49.4 Integration tests

Run:

```text
P5 fixture
→ P6 package
→ preview render
→ full render
→ manifest
```

Verify no manual step is required.

## 49.5 Failure tests

Simulate:

```text
missing image
missing audio
corrupt source asset
FFmpeg crash
GPU unavailable
storage timeout
worker restart
duplicate request
brand profile version mismatch
experiment constraint conflict
```

Expected result must be deterministic.

---

# 50. Golden visual dataset

Create a repository-owned visual regression fixture set representing:

```text
brand profile
light scene
night scene
rain scene
text overlay
thumbnail
tracklist
logo
transition
```

Store expected perceptual signatures, not only screenshots.

A material layout regression should fail CI.

---

# 51. Cost-control acceptance tests

Simulate a job whose estimated cost is above the package budget.

Expected:

```text
job blocked
reason = BUDGET_EXCEEDED
no render started
```

Simulate an allowed fallback.

Expected:

```text
render profile downgraded according to explicit fallback policy
fallback recorded in manifest
```

No silent downgrade.

---

# 52. Experiment-integrity acceptance tests

## Scenario A

P3 says:

```text
visual_world = rainy_asian_city
```

Generated visual plan says:

```text
rainy_asian_city
```

Expected:

```text
PASS
```

## Scenario B

Generated plan says:

```text
sunny tropical beach
```

Expected:

```text
BLOCKED / REVIEW
```

## Scenario C

Thumbnail experiment declares:

```text
text_only variation
```

but generated asset swaps the entire visual world.

Expected:

```text
BLOCKED
```

---

# 53. Definition of done

P6 is complete when a valid P5 Release Manifest can be submitted unattended and result in a verified Media Package containing:

```text
final audio timeline
final video
thumbnail candidates
cover artwork
tracklist
chapters
metadata candidates
short-form source candidates
publishable manifest
checksums
provenance
render report
```

without any manual rendering or file manipulation.

The package must be:

```text
reproducible
traceable
experiment-aware
brand-consistent
cost-controlled
crash-recoverable
ready for P7
```

---

# 54. What the P6 AI implementation agent may decide

The implementation agent may select:

- exact image/video generation provider;
- exact FFmpeg filter graph structure;
- exact animation library;
- exact ORM;
- exact queue/worker implementation;
- exact image-processing library;
- exact perceptual-hash implementation;
- exact thumbnail-scoring model;
- exact internal class boundaries.

It may improve implementation efficiency as long as the external contracts and invariants remain intact.

---

# 55. What the P6 AI implementation agent must not decide silently

Do not silently change:

- P5/P6 release-manifest boundary;
- experiment identifiers;
- cleared/rejected asset status;
- provenance lineage;
- brand-version semantics;
- deterministic artifact versioning;
- P6/P7 publishing boundary;
- hard-vs-soft experiment constraints;
- checksum requirements;
- idempotency semantics;
- failure/quarantine semantics;
- output manifest requirements.

Any material architectural deviation must be recorded in the project decision log and reflected in the implementation documentation.

---

# 56. Recommended implementation sequence

Implement in this order:

### P6.1 — Domain contracts

Define:

```text
ReleaseManifest
PlaylistSession
VisualSession
MediaPackage
RenderJob
BrandProfile
RenderProfile
```

### P6.2 — Storage

Create database migrations and object-storage conventions.

### P6.3 — P5 ingestion

Validate and snapshot upstream release manifests.

### P6.4 — Playlist timeline

Implement ordering, transitions, timing and chapter generation.

### P6.5 — Brand system

Implement versioned profiles and validation.

### P6.6 — Visual planner

Generate schema-valid scene plans.

### P6.7 — Visual asset manager

Implement asset creation/reuse/provenance.

### P6.8 — Artwork/thumbnail factory

Generate and validate multiple candidates.

### P6.9 — Timeline/compositor

Convert playlist + visual plan into a renderable timeline.

### P6.10 — Preview rendering

Build low-cost previews and validation.

### P6.11 — Full renderer

Implement durable rendering and recovery.

### P6.12 — Post-render QA

Validate actual output artifacts.

### P6.13 — Manifest builder

Create final P7-consumable manifest.

### P6.14 — Short-source extraction

Prepare clip candidates for future P11.

### P6.15 — Observability/cost controls

Wire all metrics and run accounting.

### P6.16 — Testing

Complete unit/property/integration/golden/recovery tests.

### P6.17 — End-to-end smoke test

Run a full P5 fixture through P6 with zero manual intervention.

---

# 57. Operational acceptance run

A successful smoke test should produce:

```text
1 release manifest
1 playlist session
8–12 songs
1 visual session
multiple visual assets
3 thumbnail candidates
1 preview video
1 final long-form video
tracklist
chapters
metadata package
short source candidates
complete manifest
checksums
render report
cost report
```

No manual shell commands should be necessary after job submission.

---

# 58. Architectural handoff to P7

P7 should see P6 as a black box:

```text
P5
  ↓
Release Manifest
  ↓
P6
  ↓
Media Package
  ↓
P7
```

P7 should not need to know:

```text
how tracks were sequenced
how images were generated
how scenes were composed
how FFmpeg was configured
how thumbnails were rendered
```

P7 only needs:

```text
what to publish
where the assets are
which metadata candidate is selected
which disclosure/policy flags apply
what provenance/package IDs to retain
```

This boundary allows the visual/media engine to evolve independently from platform automation.

---

# 59. Final principle

P6 should behave like a **professional automated post-production studio**.

It should not behave like:

```text
AI image
+
music file
+
FFmpeg
=
YouTube video
```

The intended output is a coherent package in which:

```text
music
   +
playlist sequence
   +
visual world
   +
brand identity
   +
thumbnail
   +
metadata
   +
chapters
   +
provenance
   +
experiment metadata
```

all describe the same content concept and remain traceable to the upstream production decision.

The downstream publishing layer should therefore receive a **finished media product**, not a pile of files.
