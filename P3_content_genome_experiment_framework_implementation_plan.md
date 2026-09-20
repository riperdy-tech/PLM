# P3 — Content Genome & Experiment Framework
## Detailed Implementation Plan

**Document status:** Implementation-ready specification  
**Phase:** P3 of the Autonomous AI Music Media Engine  
**Primary objective:** Convert P2 allocation decisions into structured, reproducible content experiments and maintain a machine-readable representation of every content choice, while preserving enough creative flexibility for downstream AI production.

---

# 1. Purpose

P3 is the **experiment-definition and content-specification layer** between autonomous A&R (P2) and content production (P4+).

Its job is to answer:

> **Given an A&R-selected opportunity, what exactly are we testing, what variables define the test, what content should be produced, and how do we preserve enough structure to learn which attributes caused the result?**

P3 should transform a high-level opportunity such as:

```text
R&B × late-night driving × English vocal × rainy Asian city
```

into an explicit experiment package containing:

```text
market hypothesis
content genome
controlled variables
allowed creative variation
track/playlist brief
visual brief
packaging variables
experiment metadata
measurement plan
success criteria
```

P3 must **not** generate the final music, render the final video, publish anything, or decide the global A&R allocation. Those functions belong to later phases.

---

# 2. Strategic role in the system

The intended dependency chain is:

```text
P1
Market evidence
   ↓
P2
A&R allocation
   ↓
P3
Experiment + content specification
   ↓
P4
Music generation
   ↓
P5
QA / rights / provenance
   ↓
P6
Playlist + visual + video production
   ↓
P7
YouTube publishing
   ↓
P8
Distribution
   ↓
P9
Measurement / economics
   ↓
P2/P10
Learning and reallocation
```

P3 is therefore the **causal bookkeeping layer** that prevents the system from learning only from opaque finished videos.

Without P3, the system might know:

> "Video 183 got 84,000 views."

With P3, it can know:

> "Video 183 was an English-vocal, 45–75 minute, late-night R&B playlist using title archetype A, thumbnail family B, rainy-city visual family C, and 9 tracks generated from content genome version 4. It received 84,000 views under experiment policy version 2.1."

That difference is essential for future learning.

---

# 3. Scope

## 3.1 In scope

P3 must implement:

1. Intake of P2 production directives.
2. Canonical experiment creation.
3. Content genome definition and versioning.
4. Structured experiment variables.
5. Fixed vs. variable attributes.
6. Treatment/control definitions.
7. Creative constraint envelopes.
8. Track-level and playlist-level specifications.
9. Visual-world specifications.
10. Packaging specifications.
11. Experiment lineage and parent/child relationships.
12. Reusable templates and archetypes.
13. Candidate experiment materialization.
14. Assignment identifiers and deterministic experiment IDs.
15. Measurement contracts for downstream phases.
16. Content provenance linkage.
17. Experiment lifecycle state machine.
18. Validation and preflight checks.
19. API contracts for P4–P9.
20. Persistence and audit logs.
21. Experiment status tracking.
22. Experiment cancellation/expiry handling.
23. Unit/property/integration tests.
24. Local simulation fixtures.

## 3.2 Explicitly out of scope

Do not implement in P3:

- Music generation provider calls.
- Suno API calls.
- Local music model inference.
- Audio mastering.
- Visual/image generation.
- Video rendering.
- YouTube upload.
- Spotify distribution.
- YouTube Analytics ingestion.
- Spotify royalty ingestion.
- Global A&R selection.
- Bandit posterior updates.
- Financial optimization.
- Automated audience/community replies.

P3 defines contracts for these systems but does not own their implementations.

---

# 4. Design principles

## 4.1 Every production artifact must have an experimental identity

A generated song, playlist, thumbnail, video or release must be traceable to a P3 experiment.

Minimum chain:

```text
P2 directive
   ↓
experiment
   ↓
content genome version
   ↓
content specification
   ↓
asset
   ↓
published artifact
   ↓
measurement
```

## 4.2 The genome must describe, not over-prescribe

The genome is a structured specification, not a giant prompt.

Bad design:

```text
Produce exactly 3:47 of music with these 17 production instructions.
```

Better:

```text
Mood = melancholic/dreamy
Energy = low-medium
BPM = 82–96
Vocal style = intimate English vocal

Creative latitude:
±10 BPM unless explicitly constrained
melodic contour chosen by music model
instrument choices open within genre envelope
song structure open within selected archetype
```

The implementation should preserve enough freedom for P4 to produce high-quality content.

## 4.3 Separate business variables from creative variables

Examples:

Business/experiment variables:

```text
use case
language
video duration bucket
title archetype
thumbnail family
```

Creative variables:

```text
melodic contour
instrument arrangement
specific lyrics
synth texture
vocal delivery
```

This separation allows the learning engine to test meaningful factors without turning every creative choice into a rigid template.

## 4.4 Immutable experiment definitions after execution begins

Before production starts, an experiment may be edited through a draft state.

After any downstream asset generation begins, the effective experiment specification becomes immutable.

Corrections require a new version or child experiment.

## 4.5 Explicit uncertainty

P3 can carry P2 uncertainty into the experiment, but it must not turn a low-confidence opportunity into false precision.

## 4.6 Deterministic identifiers

The same canonical experiment specification should produce a reproducible fingerprint.

This prevents accidental duplicate experiments.

## 4.7 Preserve original source decisions

Do not overwrite P2's directive when materializing an experiment.

Store both:

```text
P2 directive as received
P3 interpretation/materialization
```

If P3 modifies the directive because of production constraints, record the reason explicitly.

---

# 5. Core concepts

P3 introduces six core entities:

```text
Opportunity
Experiment
ContentGenome
ContentSpec
Treatment
Artifact
```

### Opportunity

Imported from P2/P1 and identifies what market hypothesis is being explored.

### Experiment

A test of one or more specific content variables.

### ContentGenome

The normalized representation of the creative/content attributes involved.

### ContentSpec

The concrete instructions for producing one content package from a genome.

### Treatment

One particular variation of the experiment.

### Artifact

The actual downstream object: song, playlist, image, thumbnail, video, release, etc.

---

# 6. Canonical content genome

The genome must use structured dimensions.

Initial dimensions:

```text
GENRE
SUBGENRE
MOOD
USE_CASE
LANGUAGE
VOCAL_MODE
VOCAL_GENDER
ENERGY
BPM_RANGE
KEY_PROFILE
LYRIC_DENSITY
SONG_STRUCTURE
HOOK_PROFILE
INSTRUMENTATION_PROFILE
PRODUCTION_STYLE
TRACK_DURATION_RANGE
PLAYLIST_DURATION_BUCKET
PLAYLIST_TRACK_COUNT
VISUAL_WORLD
VISUAL_MOTION_PROFILE
THUMBNAIL_FAMILY
TITLE_ARCHETYPE
DESCRIPTION_ARCHETYPE
AUDIENCE_INTENT
```

The schema should be extensible.

Do not require all dimensions to be populated for every experiment.

Use an explicit state:

```text
specified
unspecified
unknown
not_applicable
```

Do not overload `null` to mean all four states.

---

# 7. Dimension semantics

## 7.1 Genre

Broad musical classification.

Examples:

```text
rnb
pop
house
jazz
ambient
lofi
indie
synthwave
```

## 7.2 Subgenre

More specific musical family.

Examples:

```text
alt_rnb
city_pop
deep_house
jazz_house
chillwave
```

## 7.3 Mood

One or more affective descriptors.

Use controlled vocabulary with optional secondary labels.

Examples:

```text
dreamy
melancholic
romantic
uplifting
serene
nostalgic
```

## 7.4 Use case

Why the listener is using the music.

Examples:

```text
late_night_drive
study
focus
cafe
sleep
work
relaxation
```

Use case is distinct from mood.

"Melancholic" is not a use case.

## 7.5 Language

The dominant lyric language.

Possible values:

```text
english
korean
japanese
chinese_traditional
instrumental
multilingual
```

## 7.6 Vocal mode

```text
instrumental
vocal
spoken
mixed
```

## 7.7 Vocal gender

Only use when relevant to the experiment and when provider capabilities support it.

Do not make gender a required field.

## 7.8 Energy

Controlled qualitative scale:

```text
very_low
low
low_medium
medium
medium_high
high
very_high
```

## 7.9 BPM

Prefer ranges rather than single-point values.

Example:

```json
{"min":82,"max":96}
```

The music generator may select a value inside that interval.

## 7.10 Key profile

Prefer musical properties over rigid exact keys unless the experiment specifically studies key.

Examples:

```text
minor_favoring
major_favoring
mixed
open
```

## 7.11 Lyric density

Examples:

```text
instrumental
minimal
sparse
moderate
dense
```

## 7.12 Song structure

Use archetypes rather than exact timing templates.

Examples:

```text
intro_verse_chorus
ambient_evolving
hook_forward
verse_chorus_bridge
```

## 7.13 Hook profile

Examples:

```text
immediate
first_chorus
slow_burn
instrumental_hook
atmospheric
```

## 7.14 Instrumentation profile

Represent families rather than an inflexible instrument list.

Example:

```text
warm_synth
soft_drums
electric_bass
ambient_guitar
pad_layers
```

## 7.15 Production style

Broad production descriptors.

Examples:

```text
analog_warm
clean_modern
lofi_textured
cinematic
minimalist
```

## 7.16 Track duration

Prefer ranges.

Example:

```text
180–300 seconds
```

## 7.17 Playlist duration

Controlled buckets:

```text
15_30m
30_45m
45_75m
75_120m
120m_plus
```

## 7.18 Visual world

Examples:

```text
rainy_asian_city
sunrise_cafe
luxury_hotel
coastal_drive
night_rooftop
```

## 7.19 Visual motion profile

Examples:

```text
static_ambient
slow_parallax
cinematic_loop
city_motion
rain_motion
```

## 7.20 Thumbnail family

A controlled packaging concept, not an exact image.

Examples:

```text
character_closeup
cinematic_city_wide
vehicle_night_scene
minimal_typographic
interior_lifestyle
```

## 7.21 Title archetype

Examples:

```text
time_place_mood
activity_genre
scene_duration
mood_context
```

---

# 8. Genome representation

Implement a structured representation similar to:

```json
{
  "schema_version": "genome-v1",
  "attributes": {
    "genre": {"value": ["rnb"], "state": "specified"},
    "subgenre": {"value": ["alt_rnb"], "state": "specified"},
    "mood": {"value": ["dreamy", "melancholic"], "state": "specified"},
    "use_case": {"value": ["late_night_drive"], "state": "specified"},
    "language": {"value": ["english"], "state": "specified"},
    "vocal_mode": {"value": "vocal", "state": "specified"},
    "energy": {"value": "low_medium", "state": "specified"},
    "bpm_range": {"value": {"min": 82, "max": 96}, "state": "specified"},
    "playlist_duration": {"value": "45_75m", "state": "specified"},
    "visual_world": {"value": ["rainy_asian_city"], "state": "specified"}
  },
  "constraints": {
    "must": [],
    "must_not": [],
    "soft_preferences": []
  }
}
```

The exact implementation may use typed application models instead of raw JSON, but the semantics should remain equivalent.

---

# 9. Hard constraints vs soft constraints

This distinction is mandatory.

## Hard constraint

Must be satisfied.

Examples:

```text
language = English
playlist duration = 45–75 minutes
AI-generated music disclosure = required
no unlicensed source material
```

## Soft preference

Preferred but may be violated by downstream generation if necessary for quality.

Examples:

```text
BPM around 90
warm analog texture
minimal percussion
```

## Creative freedom

Anything not specified remains available to the production engine.

This prevents over-constraining the music model.

---

# 10. P2 input contract

P3 consumes a versioned `ProductionDirective` from P2.

Minimum fields:

```text
directive_id
policy_version
decision_id
arm_id
selection_reason
market_context
priority
requested_experiment_count
required_constraints
recommended_attributes
exploration_level
budget_limit
production_deadline
```

P3 must reject a directive with no `arm_id` or no valid policy/decision identity.

---

# 11. P2-to-P3 translation

P2 should not dictate every creative attribute.

Example P2 directive:

```text
arm:
R&B × late-night drive × English × vocal

market evidence:
high growth
medium competition
confidence 0.71

experiment objective:
validate arm
```

P3 materializes that into:

```text
fixed:
use_case = late_night_drive
language = english
vocal_mode = vocal

soft:
genre = rnb
BPM = 82–96
mood = dreamy/melancholic

experimental:
voice profile
visual world
thumbnail family
title archetype
playlist duration
```

The exact split should depend on the P2 exploration policy and may vary between experiments.

---

# 12. Experiment design model

Each experiment contains:

```text
experiment_id
experiment_version
experiment_family
parent_experiment_id
arm_id
hypothesis
objective
variables
control_definition
treatment_definitions
sample_plan
measurement_plan
stopping_rules
success_metrics
risk_constraints
created_at
status
```

---

# 13. Experiment hypothesis

Every experiment needs a machine-readable hypothesis.

Example:

```json
{
  "if": "late-night R&B is packaged for night-drive use",
  "when": "English vocal playlists of 45–75 minutes are published with rainy-city visuals",
  "then": "watch time and subscriber conversion will be stronger than comparable adjacent concepts",
  "because": "P1 observed strong growth and competitor velocity in this opportunity cluster"
}
```

The natural-language form is for interpretability.

Also store structured expected mechanisms where practical.

Do not store unsupported numeric predictions unless provided by a validated model.

---

# 14. Experiment variables

Represent each experimental variable as:

```text
variable_id
name
dimension
type
baseline
candidate_values
role
priority
confidence
source
```

`role` values:

```text
fixed
controlled
experimental
incidental
```

### Fixed

Cannot vary during this experiment family.

### Controlled

Should be held constant across treatment variants.

### Experimental

Intentionally varied to measure an effect.

### Incidental

Allowed to vary but not intended as a causal variable.

This distinction is important because AI-generated music inevitably has incidental creative variation.

---

# 15. Avoid invalid experiments

Do not create an experiment where everything changes simultaneously and call it an A/B test.

Bad:

```text
Genre changes
Mood changes
Language changes
Thumbnail changes
Title changes
Duration changes
Visual changes
```

Better:

```text
Control:
45–75m / rainy city / title archetype A

Treatment:
45–75m / rainy city / title archetype B

Only title archetype is intentionally changed.
```

However, multivariate experiments are allowed when explicitly represented as such and the downstream analysis method can handle them.

P3 must not prohibit multivariate designs entirely; it must make them explicit.

---

# 16. Experiment types

Implement at least:

```text
single_factor
multi_factor
portfolio_batch
exploratory_probe
replication
follow_up
```

### single_factor

One primary variable changes.

### multi_factor

Two or more intentionally varied factors.

### portfolio_batch

Several related content packages test an opportunity family without strict causal isolation.

Useful during early market exploration.

### exploratory_probe

Deliberately broad experiment to investigate an unknown concept.

### replication

Repeat a known successful or uncertain experiment to test persistence.

### follow_up

Tests a more specific question discovered from prior results.

---

# 17. Experimental replication

Replication must be a first-class concept.

A strong result should not automatically cause immediate exploitation.

Create:

```text
original experiment
      ↓
replication experiment
```

The replication should preserve the primary hypothesis while permitting a fresh creative sample.

This protects the system from one-off lucky content.

---

# 18. Content family hierarchy

P3 should support:

```text
Market opportunity
   ↓
Experiment family
   ↓
Experiment
   ↓
Treatment
   ↓
Content package
   ↓
Artifact
```

Example:

```text
Opportunity:
Rainy city R&B

Experiment family:
Night-drive R&B packaging

Experiment:
Title archetype A vs B

Treatment A:
2AM Seoul...

Treatment B:
Night Drive...

Content package:
10-track 60-minute playlist

Artifacts:
10 songs
1 video
3 thumbnails
2 Shorts
1 Spotify release bundle
```

---

# 19. Playlist-level content specification

Each playlist package should specify:

```text
playlist_id
experiment_id
track_count_target
duration_target
energy_arc
opening_strategy
middle_strategy
closing_strategy
track_role_distribution
crossfade_policy
transition_policy
```

Example track roles:

```text
opener
build
anchor
peak
reset
closer
```

The exact number of each role should remain flexible.

---

# 20. Track-level specification

Each track brief should include:

```text
track_spec_id
playlist_spec_id
role
genome_reference
lyrical_theme
hook_profile
energy_target
duration_range
vocal_profile
instrument_profile
negative_constraints
creative_latitude
```

Do not require exact lyrics in P3.

P4 will generate them.

---

# 21. Lyrics specification

Separate:

```text
lyric_theme
narrative_perspective
language
emotional_register
explicitness_policy
lexical_density
hook_requirement
```

from:

```text
actual_lyrics
```

Actual lyrics belong to generated content and must be versioned separately.

---

# 22. Visual specification

Visual brief should include:

```text
visual_world
location_archetype
time_of_day
weather
color_family
camera_distance
camera_motion
character_presence
vehicle_presence
environment_motion
loop_strategy
brand_elements
```

Use semantic rules rather than pixel-level instructions.

---

# 23. Thumbnail specification

Each thumbnail variant should identify:

```text
thumbnail_family
subject_type
composition
text_presence
text_length_limit
brand_elements
visual_emphasis
variant_id
```

Do not bake final wording into the experiment genome unless the experiment specifically tests wording.

---

# 24. Title specification

Title spec should contain:

```text
title_archetype
required_terms
optional_terms
language
character_limit
emoji_policy
keyword_policy
brand_prefix_policy
```

A final title is an artifact and can be tested independently of the genome.

---

# 25. Creative latitude envelope

P3 should specify what downstream generation may vary.

Example:

```json
{
  "fixed": [
    "use_case",
    "language",
    "vocal_mode"
  ],
  "bounded": {
    "bpm": "82-96",
    "duration_seconds": "180-300",
    "energy": "low_medium"
  },
  "free": [
    "instrument_arrangement",
    "melodic_contour",
    "specific_lyrics",
    "sound_design_details"
  ]
}
```

Downstream agents must honor hard constraints but may exercise judgment within bounded/free areas.

---

# 26. Brand integration

Each content specification should reference a `brand_profile_id`.

Brand profile determines:

```text
visual language
typography family
logo usage
thumbnail style
color family
character/world consistency
copy tone
```

P3 should reference the brand rather than duplicating all brand rules into every experiment.

---

# 27. Provenance

Every experiment must capture:

```text
created_by = p2_to_p3_materializer
source_directive_id
source_decision_id
source_arm_id
source_market_snapshot_id if available
p1_score_version
p2_policy_version
p3_schema_version
```

Do not create an experiment without lineage.

---

# 28. Experiment fingerprint

Create a canonical representation of the experiment excluding non-semantic fields such as timestamps and runtime IDs.

Hash it to create:

```text
experiment_fingerprint
```

The fingerprint should include:

```text
arm_id
experiment_type
fixed variables
experimental variables
candidate values
brand profile
platform target
content format
```

Do not include transient queue IDs.

---

# 29. Duplicate prevention

Before creating a new experiment:

1. compute fingerprint;
2. query recent/active experiments;
3. determine exact duplicate vs intentional replication;
4. reject accidental duplicates;
5. allow explicit replication with a distinct `replication_group_id`.

Do not globally ban similar experiments; some repetition is essential for statistical learning.

---

# 30. Lifecycle state machine

Required states:

```text
draft
validated
queued
materializing
materialized
in_production
partially_completed
completed
published
measuring
closed
cancelled
expired
blocked
```

Valid transitions must be explicit.

Example:

```text
draft → validated
validated → queued
queued → materializing
materializing → materialized
materialized → in_production
in_production → partially_completed
in_production → completed
completed → published
published → measuring
measuring → closed
```

An invalid transition must produce a controlled error rather than silently updating status.

---

# 31. Experiment expiry

An experiment may expire before production if:

```text
market arm is retired
market conditions are stale
required provider capability disappears
budget becomes unavailable
policy changes
```

Do not automatically delete the experiment.

Set:

```text
status = expired
expiry_reason
expired_at
```

This preserves the learning history.

---

# 32. Production preflight

Before moving an experiment from `validated` to `queued`, verify:

```text
arm exists
P2 decision exists
content genome is schema-valid
required fields present
brand exists
platform target exists
budget envelope valid
experiment variables valid
no contradictory constraints
no unsupported values
no known policy conflict
```

P3 does not perform full rights analysis but should reject obviously incomplete specifications.

---

# 33. Contradiction detection

Examples of invalid combinations:

```text
vocal_mode = instrumental
AND
vocal_mandatory = true
```

```text
playlist_duration = 15_30m
AND
track_count_minimum = 20 × 4 minutes
```

```text
language = instrumental
AND
lyric_language_required = english
```

The validator should explain the conflicting fields.

---

# 34. Experiment budgeting

Each experiment should carry an upper bound:

```text
max_generation_cost
max_external_api_cost
max_compute_cost
max_asset_count
```

These are ceilings, not instructions to spend the full amount.

P4/P6 should be allowed to return a lower actual cost.

---

# 35. Batch design

P2 may request:

```text
requested_experiment_count = 5
```

P3 materializes five distinct experiments only if their fingerprints differ meaningfully.

For a batch, include:

```text
batch_id
batch_strategy
batch_slot
randomization_seed if used
```

Randomization must be deterministic for reproducibility.

---

# 36. Treatment assignment

When multiple treatment variants exist, create an explicit treatment ID:

```text
treatment_A
treatment_B
treatment_C
```

Each treatment contains:

```text
treatment_id
experiment_id
variable_overrides
content_spec_override
assignment_seed
```

Do not encode treatment only in free-form text.

---

# 37. Randomization policy

P3 may randomize non-semantic choices such as:

```text
treatment presentation order
track candidate ordering
visual variant assignment
thumbnail variant selection
```

Where randomization is used:

- store the seed;
- store the randomization policy version;
- ensure reproducibility.

Do not randomize hard business constraints.

---

# 38. Treatment contamination

The system should detect cases where one treatment unintentionally changes multiple factors.

Example:

```text
treatment A title family A
thumbnail family A

Treatment B title family B
thumbnail family B
```

If the experiment claims to test only title family, the validator must flag contamination.

The system may allow the design, but it must be explicitly classified as multi-factor.

---

# 39. Experiment measurement contract

P3 defines the expected downstream dimensions but does not collect them.

Minimum measurement keys:

```text
impressions
views
click_through_rate
watch_time
average_view_duration
average_percentage_viewed
likes
comments
shares
subscribers_gained
revenue
spotify_streams
spotify_revenue
```

These may be unavailable for some stages.

The experiment should distinguish:

```text
required_metric
optional_metric
future_metric
```

---

# 40. Success metric definition

Each experiment should declare a primary metric and secondary metrics.

Example:

```json
{
  "primary": "watch_time_per_impression",
  "secondary": [
    "subscriber_conversion_rate",
    "views_per_24h",
    "estimated_revenue_per_1000_views"
  ]
}
```

Do not hard-code YouTube CTR as the universal primary metric. The experiment objective determines the primary metric.

For a discovery experiment, the primary metric might be normalized view velocity.

For packaging, it might be CTR.

For catalog economics, it might be revenue per content package.

---

# 41. Metric definitions

Each metric referenced by an experiment must point to a metric registry entry.

Metric registry fields:

```text
metric_id
name
definition
formula
source
unit
minimum_sample_size
attribution_window
version
```

This avoids later ambiguity around terms such as "retention" or "conversion."

---

# 42. Attribution window

Store explicitly:

```text
24h
72h
7d
28d
lifetime
```

Do not mix a 24-hour metric with a lifetime metric under one field name.

---

# 43. Cohort definition

Where downstream data permits, attach:

```text
country
language
device
traffic_source
new_vs_returning
subscription_status where available
```

P3 only defines the measurement metadata. P9 will collect and analyze it.

---

# 44. Content package model

A content package is the production unit handed to P4/P6.

Fields:

```text
package_id
experiment_id
treatment_id
brand_profile_id
music_specs
playlist_spec
visual_spec
thumbnail_spec
title_spec
description_spec
platform_targets
release_targets
budget_envelope
```

This should be the canonical handoff contract.

---

# 45. Downstream interface: P4

Expose:

```text
GET /content-packages/{package_id}
```

P4 should receive only the fields required to generate music and associated textual creative inputs.

P4 must not need to understand P2's bandit logic.

---

# 46. Downstream interface: P5

P5 should receive package identity and provenance requirements.

It must be able to associate QA records with:

```text
package_id
track_spec_id
experiment_id
```

---

# 47. Downstream interface: P6

P6 consumes:

```text
playlist_spec
visual_spec
thumbnail_spec
brand_profile
track references
```

P6 must be able to create multiple visual/thumbnail candidates when an experiment calls for variants.

---

# 48. Downstream interface: P7/P8

Publishing/distribution phases consume platform targets and packaging metadata.

P3 should specify:

```text
youtube_target = true/false
spotify_target = true/false
shorts_target = true/false
```

but should not perform the platform actions.

---

# 49. API surface

Implement a provider-neutral internal API.

### Create experiment

```text
POST /experiments
```

### Validate experiment

```text
POST /experiments/{id}/validate
```

### Materialize content package

```text
POST /experiments/{id}/materialize
```

### List experiments

```text
GET /experiments
```

### Get experiment

```text
GET /experiments/{id}
```

### Get treatments

```text
GET /experiments/{id}/treatments
```

### Get package

```text
GET /content-packages/{package_id}
```

### Close/cancel

```text
POST /experiments/{id}/cancel
POST /experiments/{id}/close
```

Use idempotency keys on create/materialize operations.

---

# 50. Database tables

Minimum tables:

```text
experiments
experiment_versions
experiment_variables
experiment_treatments
experiment_metrics
experiment_lineage
content_genomes
content_genome_versions
content_packages
track_specs
playlist_specs
visual_specs
thumbnail_specs
title_specs
metric_registry
brand_profiles
experiment_events
```

Additional tables may be introduced where useful.

---

# 51. `experiments`

Suggested fields:

```text
id UUID PK
experiment_id human-readable unique ID
arm_id
batch_id
experiment_family
experiment_type
status
hypothesis
objective
primary_metric_id
replication_group_id
fingerprint
schema_version
created_at
updated_at
validated_at
queued_at
closed_at
```

---

# 52. `experiment_versions`

Fields:

```text
id
experiment_id
version_number
spec_json
created_at
created_by
change_reason
is_effective
```

The effective version becomes immutable when production starts.

---

# 53. `experiment_variables`

Fields:

```text
id
experiment_id
variable_name
dimension
type
role
baseline_value
candidate_values
constraints
source
version
```

---

# 54. `experiment_treatments`

Fields:

```text
id
experiment_id
treatment_id
label
variable_overrides
assignment_seed
status
```

---

# 55. `content_genomes`

Fields:

```text
id
genome_id
name
status
current_version
created_at
updated_at
```

---

# 56. `content_genome_versions`

Fields:

```text
id
genome_id
version
schema_version
attributes_json
constraints_json
created_at
created_by
change_reason
```

---

# 57. `content_packages`

Fields:

```text
id
package_id
experiment_id
treatment_id
brand_profile_id
package_type
spec_json
status
created_at
updated_at
```

---

# 58. `track_specs`

Fields:

```text
id
track_spec_id
package_id
track_role
sequence_position
genome_version
thematic_brief
creative_constraints
creative_latitude
status
```

---

# 59. `playlist_specs`

Fields:

```text
id
playlist_spec_id
package_id
duration_bucket
target_duration_seconds
track_count_min
track_count_max
energy_arc
transition_policy
```

---

# 60. `visual_specs`

Fields:

```text
id
visual_spec_id
package_id
brand_profile_id
visual_world
motion_profile
scene_rules
character_rules
environment_rules
loop_policy
```

---

# 61. `thumbnail_specs`

Fields:

```text
id
thumbnail_spec_id
package_id
variant_id
family
composition
text_policy
brand_rules
```

---

# 62. `title_specs`

Fields:

```text
id
title_spec_id
package_id
variant_id
archetype
required_terms
optional_terms
language
character_limit
emoji_policy
```

---

# 63. `experiment_events`

Use an append-only event stream for important state changes.

Example:

```text
EXPERIMENT_CREATED
EXPERIMENT_VALIDATED
EXPERIMENT_MATERIALIZED
PRODUCTION_STARTED
PRODUCTION_COMPLETED
PUBLISHED
MEASUREMENT_STARTED
EXPERIMENT_CLOSED
EXPERIMENT_CANCELLED
```

Each event contains:

```text
event_id
experiment_id
event_type
occurred_at
actor
payload
software_version
```

---

# 64. LLM responsibilities

LLMs may help with:

```text
hypothesis wording
creative brief drafting
thematic concepts
lyric themes
visual semantic concepts
title archetype suggestions
classification of ambiguous creative attributes
```

LLMs must not be the sole source of:

```text
experiment identity
numeric metrics
randomization
state transitions
budget enforcement
hard constraint validation
fingerprint computation
```

Use deterministic application logic for these.

---

# 65. LLM prompt contract

Any LLM call in P3 should specify:

```text
purpose
input schema
allowed taxonomy
output schema
forbidden claims
model
model_version
prompt_version
```

Persist the model/prompt version with the resulting structured decision.

---

# 66. Taxonomy drift

The content genome depends on the taxonomy from P1.

When P1 introduces a new dimension/value:

1. mark schema compatibility;
2. determine whether old genomes remain valid;
3. create a new genome schema version only when necessary;
4. never silently reinterpret historical experiments.

Example:

```text
genome-v1:
visual_world = rainy_asian_city

genome-v2:
visual_world = rainy_city
region = east_asia
```

Historical experiments remain v1.

---

# 67. Creative template system

P3 should support reusable templates such as:

```text
Night Drive Playlist
Cafe Morning
Luxury Lounge
Rainy City
Sunday Reset
```

Templates define defaults only.

They must be overridable by the experiment.

Avoid hard-coding templates directly into application logic.

Store them as versioned configuration.

---

# 68. Template inheritance

Support:

```text
base template
   ↓
brand override
   ↓
experiment override
   ↓
treatment override
```

Later overrides should win only where a value is explicitly provided.

Do not replace the entire parent genome when one field changes.

---

# 69. Example template

```json
{
  "template_id": "night_drive_v1",
  "defaults": {
    "use_case": "late_night_drive",
    "playlist_duration": "45_75m",
    "visual_motion_profile": "city_motion",
    "hook_profile": "slow_burn",
    "thumbnail_family": "cinematic_city_wide"
  },
  "soft_preferences": {
    "energy": "low_medium"
  }
}
```

This is a starting point, not an immutable production recipe.

---

# 70. Content diversity rules

P3 should support configurable diversity constraints to avoid accidental duplicate content.

Examples:

```text
within_batch:
no identical genome fingerprint
no identical title+thumbnail combination
max N packages sharing exact visual world
```

These constraints should be configurable by P2/policy.

Do not globally ban similar content.

---

# 71. Similarity thresholds

Use a structured distance function over content genomes.

Potential weighting:

```text
genre          20%
subgenre       10%
mood           10%
use_case       15%
language        5%
vocal_mode      5%
energy          5%
duration         5%
visual_world   10%
title_style     5%
other           10%
```

These are initial implementation defaults only.

The exact weights should be configuration-driven and later tuned by experiment results.

---

# 72. No false causal claims

P3 should store:

```text
intentional_variable = thumbnail_family
```

It should not store:

```text
thumbnail_family causes higher revenue
```

unless a later analytics/causal-analysis subsystem establishes such evidence.

P3 records the experimental design, not the conclusion.

---

# 73. Experiment randomization seed

Every experiment requiring randomness must store:

```text
seed
randomization_algorithm
randomization_version
```

Do not use system time as an implicit seed.

---

# 74. Idempotency

Creating the same experiment request twice with the same idempotency key must return the same experiment.

Materializing the same experiment twice must not create duplicate content packages unless explicitly requested as separate replications.

---

# 75. Concurrency

P3 must prevent two workers from materializing the same draft simultaneously.

Use database locks or an equivalent concurrency mechanism.

State transitions should be atomic.

---

# 76. Failure recovery

If materialization fails halfway:

```text
experiment status = partially_completed
```

Persist all successfully created child records.

Retry should resume from the last durable boundary rather than recreate everything.

Every child artifact should have its own idempotency key.

---

# 77. Auditability

For each experiment, an auditor should be able to answer:

```text
Which P2 decision created this?
Which market arm?
Which genome version?
Which template?
Which variables were intentional?
What were the treatment values?
What budget ceiling applied?
Which model generated the creative brief?
Which downstream assets belong to it?
```

No answer should depend solely on reconstructing application logs.

---

# 78. Security

P3 should not store platform secrets.

References to future providers should use abstract provider IDs.

LLM prompts must not contain OAuth tokens.

Logs must redact secrets.

Access control should distinguish:

```text
read experiment
create experiment
validate experiment
materialize experiment
cancel experiment
modify templates
```

---

# 79. Observability

Metrics:

```text
experiments_created
experiments_validated
experiments_rejected
experiments_materialized
experiments_cancelled
materialization_failures
validation_failures
duplicate_attempts
average_materialization_latency
llm_calls
llm_cost_estimate
```

Useful labels:

```text
experiment_type
brand
arm
provider-independent status
```

Do not include sensitive prompt contents in metrics labels.

---

# 80. Logging

Structured logs must include:

```text
timestamp
experiment_id
package_id if known
event
state_before
state_after
worker_id
software_version
error_code
```

Use correlation IDs for cross-service tracing.

---

# 81. API validation errors

Return machine-readable errors such as:

```json
{
  "code": "CONTRADICTORY_CONSTRAINTS",
  "field_paths": [
    "genome.vocal_mode",
    "track_specs[0].vocals_required"
  ],
  "message": "Instrumental genome conflicts with mandatory vocal track specification."
}
```

Do not return only free-form error strings.

---

# 82. Test strategy

## Unit tests

Cover:

```text
genome normalization
state transitions
fingerprint generation
constraint validation
inheritance
variable classification
similarity
randomization
metric validation
budget validation
idempotency
```

## Property tests

Examples:

```text
adding an irrelevant timestamp must not change fingerprint
reordering unordered attribute lists must not change fingerprint
changing an intentional experiment variable must change fingerprint
invalid hard constraints must never validate
state machine never reaches an invalid state
```

## Integration tests

Test:

```text
P2 directive
→ P3 experiment
→ validation
→ treatment creation
→ content package
→ downstream API retrieval
```

## Regression fixtures

Maintain representative fixtures for:

```text
night_drive_rnb
rainy_city_pop
luxury_deep_house
instrumental_jazz
ambient_sleep
```

---

# 83. Required acceptance scenarios

## AC-01 — Basic materialization

Given a valid P2 directive, P3 creates one validated experiment and one content package.

## AC-02 — Multiple treatments

A two-treatment experiment creates distinct, correctly isolated treatment specifications.

## AC-03 — Controlled variable

A controlled variable remains identical across all treatments.

## AC-04 — Experimental variable

An experimental variable differs only where the experiment specifies it.

## AC-05 — Creative freedom

Unspecified creative fields remain open rather than receiving arbitrary hard-coded values.

## AC-06 — Duplicate prevention

Repeated identical requests are detected as duplicates unless marked as replication.

## AC-07 — Replication

A replication creates a new experiment with lineage to the original.

## AC-08 — Schema versioning

Historical experiments remain readable after a new genome version is introduced.

## AC-09 — Invalid constraint

Contradictory constraints fail validation with machine-readable errors.

## AC-10 — Idempotency

Retrying creation/materialization does not duplicate child objects.

## AC-11 — Failure recovery

A failed materialization can resume without recreating already committed objects.

## AC-12 — P4 handoff

P4 can obtain a complete music-production-ready package through the API without querying the P3 database directly.

## AC-13 — Audit trail

Every experiment can be traced back to P2 and the source market arm.

## AC-14 — Budget ceiling

P3 rejects a package that exceeds configured hard constraints before it enters production.

## AC-15 — Experiment contamination

A design claiming to test one factor is flagged if its treatment overrides intentionally change another factor.

---

# 84. Example end-to-end P3 artifact

A P2 directive might arrive as:

```json
{
  "directive_id": "DIR-20260920-001",
  "decision_id": "DEC-20260920-008",
  "arm_id": "rnb_late_night_drive_en_vocal_60",
  "priority": 0.88,
  "requested_experiment_count": 2,
  "objective": "validate opportunity arm",
  "recommended_attributes": {
    "genre": "rnb",
    "use_case": "late_night_drive",
    "language": "english",
    "vocal_mode": "vocal"
  }
}
```

P3 could materialize:

```text
Experiment E1
  Type: portfolio_batch
  Hypothesis: market arm validation
  Fixed:
    genre = R&B
    use_case = late-night drive
    language = English
    vocal = yes

  Soft:
    mood = dreamy/melancholic
    BPM = 82–96

  Test variables:
    visual_world = rainy_seoul vs rainy_tokyo
    title_archetype = time_place_mood vs activity_genre

Experiment E2
  Type: replication
  Parent = E1
  Keep core arm fixed
  Fresh creative sample
  New visual scene
```

This is preferable to producing two near-identical videos with no experiment metadata.

---

# 85. Recommended implementation sequence

## P3.1 — Domain contracts

Define:

```text
Genome
Experiment
Variable
Treatment
ContentPackage
TrackSpec
PlaylistSpec
VisualSpec
ThumbnailSpec
TitleSpec
```

## P3.2 — Schema/version layer

Implement schema validation and versioned serialization.

## P3.3 — Database

Create migrations and repositories for experiments and genome versions.

## P3.4 — P2 adapter

Implement `ProductionDirective` ingestion and validation.

## P3.5 — Genome engine

Implement normalization, inheritance, hard/soft/free constraint semantics and fingerprints.

## P3.6 — Experiment engine

Implement experiment types, variables, treatments, lineage and lifecycle.

## P3.7 — Materializer

Convert P2 directives into packages according to configurable templates.

## P3.8 — Validation engine

Implement contradiction detection, contamination detection and budget validation.

## P3.9 — Measurement contract

Implement metric registry and experiment measurement definitions.

## P3.10 — API

Expose experiments, packages, treatments and validation results.

## P3.11 — Observability

Add structured events, metrics and tracing.

## P3.12 — Tests

Complete unit, property, integration and acceptance suites.

## P3.13 — End-to-end smoke test

Run:

```text
synthetic P2 directive
→ P3 experiment
→ validate
→ materialize
→ retrieve package
```

No external music/video provider is required for the smoke test.

---

# 86. Configuration

Keep the following configurable:

```text
genome_schema_version
experiment_schema_version
metric_registry_version
diversity_thresholds
similarity_weights
default_duration_buckets
batch_size_limits
budget_limits
template_versions
randomization_version
```

Do not put these values into scattered code constants.

---

# 87. What the implementation AI may decide

The coding agent may decide:

- ORM/library choices;
- exact class/module layout;
- sync vs async internals;
- migration tooling;
- API framework conventions;
- exact JSON serialization implementation;
- database indexes;
- cache implementation;
- exact LLM provider for creative brief drafting;
- implementation of vector similarity;
- exact internal event-bus mechanism.

It may also improve implementation quality beyond this document when behavior remains compatible.

---

# 88. What the implementation AI must not silently change

Do not silently change:

- the P2→P3 contract;
- the experiment/content-package distinction;
- genome versioning semantics;
- hard vs soft vs free constraints;
- explicit treatment definitions;
- immutable post-production specs;
- provenance requirements;
- deterministic fingerprints;
- idempotency requirements;
- replication semantics;
- metric-registry principle;
- experiment lifecycle states;
- the P3/P4 boundary.

If a better design requires changing one of these, update this plan and record a decision before implementation.

---

# 89. Avoid overengineering

P3 should not become a generalized scientific experiment-management platform before the music workflow needs it.

Start with the minimum capable abstraction that supports:

```text
opportunity
→ experiment
→ treatment
→ content package
→ artifact lineage
→ measurement definition
```

Do not build a general causal-inference engine here.

Do not build a custom ML platform here.

Do not build a full digital asset management suite here.

Those are later concerns.

---

# 90. Definition of done

P3 is complete when the system can accept a valid P2 production directive and autonomously create an auditable, versioned, experiment-aware content package that downstream production agents can consume without making assumptions about:

```text
what is being tested
why it is being tested
which attributes are fixed
which attributes may vary
which variables are intentional
what creative latitude remains
which brand applies
how the resulting assets will be measured
```

The critical end-state property is:

> **Every piece of produced content must be explainable as the output of a specific experiment specification, while the specification must remain flexible enough for the downstream AI to make quality-oriented creative decisions rather than mechanically following an over-constrained prompt.**

---

# 91. P3 handoff to later phases

P4 should be able to say:

> "Give me the music specs for package PKG-123."

P6 should be able to say:

> "Give me the playlist and visual specs for PKG-123."

P7 should be able to say:

> "Give me all YouTube packaging variants for PKG-123."

P8 should be able to say:

> "Give me all distribution-ready release metadata for PKG-123."

P9 should later be able to say:

> "Give me the exact experiment definition and variables associated with this published asset."

No downstream subsystem should need to reverse-engineer experiment intent from a final prompt or filename.

---

# 92. Final architectural principle

P3 should make the autonomous system **measurable without making it mechanical**.

The genome gives the system a vocabulary.

The experiment model gives it scientific discipline.

The creative-latitude envelope gives downstream AI freedom.

The lineage model makes every result learnable.

That combination is the purpose of P3.
