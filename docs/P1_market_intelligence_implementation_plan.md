# P1 — Market Intelligence Engine
## Detailed Implementation Plan

**Document status:** Implementation-ready specification
**Phase:** P1 of the Autonomous AI Music Media Engine
**Primary objective:** Build the market-research subsystem that continuously discovers, normalizes, scores and stores content opportunities for later autonomous A&R decisions.

---

## 1. Purpose

P1 is the intelligence foundation for the entire system.

It must answer, without a human choosing a genre:

> **What combinations of genre, mood, use case, language, vocal mode, duration, visual concept and packaging appear to have attractive demand, growth, competition and monetization characteristics?**

P1 does **not** generate music, publish content, select final production arms, or perform bandit allocation. Those belong to later phases.

P1's job is to create a trustworthy, reproducible **market opportunity dataset** that P2 can consume.

The system must never present an unvalidated LLM opinion such as "lo-fi is trending" as market research. Every market claim must be traceable to observations, source provenance, collection time and the deterministic transformation that produced the score.

---

# 2. Scope

## 2.1 In scope

P1 must implement:

1. Source connectors for approved market-data sources.
2. Query/seed management.
3. YouTube competitor discovery.
4. YouTube video/channel observation collection.
5. Normalization of raw YouTube data.
6. Trend/velocity calculations.
7. Competition/saturation measurements.
8. A music-market taxonomy model.
9. Concept/"content arm" generation from observed market language.
10. Opportunity scoring.
11. Confidence scoring.
12. Source provenance and freshness tracking.
13. Deduplication.
14. Retry/backoff and rate-limit handling.
15. Data-quality checks.
16. Scheduled refresh jobs.
17. A queryable API for P2.
18. A human-readable market report/export for debugging and audit.
19. Unit, integration and data-quality tests.
20. Observability and run-level logging.

## 2.2 Explicitly out of scope

Do not implement in P1:

- Music generation.
- Suno integration.
- Local music generation.
- Spotify distribution.
- YouTube publishing.
- Thumbnail generation.
- Video generation.
- Automated comments.
- Autonomous budget allocation.
- Contextual-bandit selection.
- Reinforcement learning.
- Revenue optimization based on the channel's own performance.
- Automated paid advertising.

P2+ consumes P1 outputs for those functions.

---

# 3. Design principles

## 3.1 Evidence first

Every derived metric must point to the raw observations from which it was calculated.

Never store only:

```text
market_score = 82.4
```

Store:

```text
market_score = 82.4
score_version = 1
component_scores = {...}
observation_ids = [...]
computed_at = ...
```

## 3.2 Raw data is immutable

Never overwrite raw observations.

If a source returns changed values later, store a new observation with a new collection timestamp.

Normalized tables may be rebuilt from immutable raw observations.

## 3.3 Source-specific schemas

Do not flatten every provider into one generic JSON structure prematurely.

Retain:

- provider name
- endpoint
- request parameters
- response timestamp
- provider object ID
- raw response payload where permitted
- parsed canonical fields

Then normalize only the fields needed for cross-source comparison.

## 3.4 LLMs classify; deterministic code calculates

LLMs may:

- classify genre/mood/use-case language;
- propose taxonomy labels;
- map synonymous terms;
- identify likely content concepts from observed descriptions.

LLMs must **not** silently invent raw metrics or calculate official source statistics.

Deterministic Python code must calculate:

- age;
- growth;
- velocity;
- medians;
- concentration;
- confidence;
- score components;
- final opportunity scores.

## 3.5 Current data vs historical data

Never mix historical and current observations without retaining their dates.

Every metric must have an explicit observation window.

Example:

```text
views_7d
views_30d
views_90d
```

are different measures and must never be represented by one ambiguous `views` field.

## 3.6 No false precision

If sample size is too small to support a comparison, return low confidence and preserve the metric rather than manufacturing certainty.

Example:

```text
sample_size = 3
confidence = low
```

not:

```text
opportunity_score = 91.2381
confidence = 0.94
```

## 3.7 Provider replacement

Each connector must be behind a provider interface so P1 can replace a source without changing taxonomy or scoring code.

---

# 4. External-source constraints verified for this implementation

## 4.1 YouTube Data API

The official `search.list` endpoint returns search results and has a documented quota cost. The current official documentation states a quota impact of 100 units per `search.list` call and supports filtering by resource type. Source:

https://developers.google.com/youtube/v3/docs/search/list

Use official YouTube APIs as the primary structured competitor-discovery source.

## 4.2 YouTube Analytics / Reporting

The YouTube Analytics API supports authenticated reports by dimensions and metrics; the official documentation includes metrics such as views, watch time, average view duration, average percentage viewed, subscribers gained/lost and other channel/video statistics.

Sources:

https://developers.google.com/youtube/analytics/reference/reports/query
https://developers.google.com/youtube/analytics/channel_reports
https://developers.google.com/youtube/reporting/v1/reports/channel_reports

In P1 these APIs are primarily reserved for the system's own channel only after credentials exist. Competitor research should not pretend that competitor private analytics are available.

## 4.3 Spotify

Spotify's Web API is usable for permitted catalog metadata retrieval, but Spotify's API surface changed materially in 2026. In February 2026, fields including track/artist/album popularity were removed from certain API responses, and playlist item access is restricted for playlists the user does not own/collaborate on.

Sources:

https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
https://developer.spotify.com/documentation/web-api/reference/get-track

Therefore P1 must treat Spotify as an optional secondary signal provider and must not make the opportunity score depend on a now-removed popularity field.

## 4.4 Browser automation

Use browser automation only for approved public web research where no usable API exists, and isolate it behind a connector. Browser automation is not the primary source of truth for YouTube or Spotify when official APIs provide the needed structured data.

---

# 5. High-level architecture

```text
                 ┌──────────────────────┐
                 │ Seed / Query Manager │
                 └──────────┬───────────┘
                            ↓
             ┌──────────────────────────────┐
             │ Connector Scheduler          │
             └──────────────┬───────────────┘
                            ↓
        ┌────────────────────────────────────────┐
        │ Source Connectors                      │
        │                                        │
        │ YouTube Data API                       │
        │ Spotify metadata (optional)            │
        │ Trends/public research connector       │
        │ Browser connector (optional fallback)  │
        └───────────────────┬────────────────────┘
                            ↓
                  ┌───────────────────┐
                  │ Raw Observation   │
                  │ Store             │
                  └─────────┬─────────┘
                            ↓
                  ┌───────────────────┐
                  │ Normalizer        │
                  └─────────┬─────────┘
                            ↓
              ┌───────────────────────────┐
              │ Entity Resolution / Dedupe│
              └────────────┬──────────────┘
                           ↓
                 ┌────────────────────┐
                 │ Taxonomy Classifier│
                 │ LLM + deterministic│
                 └──────────┬─────────┘
                            ↓
               ┌──────────────────────────┐
               │ Feature Engineering      │
               └─────────────┬────────────┘
                             ↓
                 ┌────────────────────────┐
                 │ Opportunity Scorer     │
                 └────────────┬───────────┘
                              ↓
                 ┌────────────────────────┐
                 │ Market Opportunity DB  │
                 └────────────┬───────────┘
                              ↓
                        P2 A&R Engine
```

---

# 6. Repository structure

The implementation should use a structure close to this unless the existing repository already has a superior established convention:

```text
src/
  market_intelligence/
    __init__.py
    config.py
    domain/
      entities.py
      enums.py
      value_objects.py
    connectors/
      base.py
      youtube.py
      spotify.py
      trends.py
      browser.py
    collection/
      scheduler.py
      query_planner.py
      collectors.py
      rate_limit.py
      retries.py
    normalization/
      youtube.py
      spotify.py
      trends.py
      canonical.py
    taxonomy/
      ontology.py
      classifier.py
      synonym_map.py
      validators.py
    features/
      growth.py
      velocity.py
      competition.py
      demand.py
      cross_platform.py
      production_fit.py
      confidence.py
    scoring/
      opportunity.py
      normalization.py
      calibration.py
    storage/
      models.py
      repositories.py
      raw_store.py
    reports/
      market_snapshot.py
      exports.py
    api/
      routes.py
      schemas.py
    jobs/
      daily.py
      weekly.py
      backfill.py
    observability/
      logging.py
      metrics.py

tests/
  unit/
  integration/
  fixtures/
  contract/

migrations/

docs/
  architecture.md
  P1_market_intelligence_implementation_plan.md
```

The exact directory names may adapt to an existing codebase, but the logical separation must remain.

---

# 7. Canonical data model

## 7.1 `market_sources`

Represents a configured external source.

Required fields:

```text
id UUID / PK
provider_name
connector_type
base_identifier
status
configuration_version
created_at
updated_at
```

Examples:

```text
youtube_data_api
spotify_web_api
public_trends
browser_public_search
```

Never store secrets in this table.

---

## 7.2 `market_queries`

Represents a research query/seed.

Fields:

```text
id
source_id
query_text
query_type
locale
country_code
language
seed_group
active
priority
first_seen_at
last_run_at
next_run_at
query_version
created_at
updated_at
```

`query_type` examples:

```text
music_genre
music_mood
use_case
playlist_pattern
artist_adjacent_term
long_tail
competitor_discovery
```

Do not encode taxonomy meaning only in free-form text.

---

## 7.3 `market_collection_runs`

Every collection attempt must have a run record.

Fields:

```text
id
source_id
query_id
started_at
finished_at
status
request_count
success_count
error_count
rate_limit_count
raw_observation_count
normalized_observation_count
error_summary
software_version
```

Possible statuses:

```text
queued
running
succeeded
partial
failed
cancelled
```

---

## 7.4 `market_raw_observations`

Immutable raw source observations.

Fields:

```text
id
collection_run_id
source_id
provider_object_type
provider_object_id
observed_at
request_context_hash
payload_json
payload_hash
schema_version
```

Deduplication key should include at least:

```text
source_id
provider_object_id
observed_at bucket or source event timestamp
payload_hash
```

Do not dedupe away legitimate time-series observations.

---

## 7.5 `market_entities`

Canonical entities across observations.

Fields:

```text
id
entity_type
canonical_name
normalized_name
canonical_url
language
country_code
first_seen_at
last_seen_at
status
```

Entity types:

```text
channel
video
playlist
track
artist
query
concept
```

---

## 7.6 `market_observations`

Normalized observations.

Fields should include a consistent subset such as:

```text
id
entity_id
source_id
observed_at
published_at
views
likes
comments
subscriber_count
video_duration_seconds
playlist_item_count
channel_video_count
text_title
text_description
country_signal
language_signal
```

Nullable fields are expected because sources do not expose the same data.

Do not substitute zero for unknown/null.

`NULL` means unknown/not provided.

`0` means explicitly observed zero where supported.

---

# 8. Seed generation

P1 must start without needing a human to manually enter hundreds of queries.

However, it also must not pretend that the LLM can discover the entire search space from nothing.

Implement a two-stage seed system.

## Stage A — bootstrap seed pack

Ship a small, curated configuration containing broad categories:

```text
rnb
pop
indie
city pop
synthwave
lofi
ambient
house
deep house
jazz
acoustic
sleep music
study music
focus music
night drive
cafe music
rainy day
luxury lounge
romantic
melancholic
```

These are **discovery seeds**, not assumptions that they are the best opportunities.

## Stage B — autonomous expansion

Use observed titles/descriptions/queries to discover new candidate terms.

Examples:

```text
late night drive
midnight city
rainy tokyo
neon r&b
hotel lobby music
after hours jazz
```

Every automatically generated seed must have:

```text
parent_seed
source_observation_id(s)
proposed_by_model
model_name
model_version
confidence
```

The LLM may propose a term; deterministic validation decides whether it enters the active query pool.

---

# 9. Query planner

Implement a planner that schedules queries based on expected information value.

Priority factors:

```text
newness
historical volatility
importance to active hypotheses
staleness
source cost
```

A query should be refreshed more often when:

- its observed market data changes rapidly;
- it feeds many active opportunity arms;
- it is near a decision boundary;
- its data is stale.

A stable low-value query can be refreshed less frequently.

Do not hard-code every query to refresh at the same interval.

Suggested initial schedule:

```text
high volatility / active topics: 24h
normal topics: 72h
long-tail discovery: 7d
historical backfill: on demand
```

These are defaults and must be configuration-driven.

---

# 10. YouTube data collection

## 10.1 Discovery

For each active query, use the official YouTube Data API search endpoint to identify relevant videos/channels/playlists where supported.

At minimum capture:

```text
video_id
channel_id
playlist_id if returned
published_at
title
description
channel_title
```

Then retrieve additional details through the appropriate resource endpoint(s) rather than assuming the search response contains all required metrics.

## 10.2 Channel observations

For discovered channels capture, when available:

```text
channel_id
channel_title
subscriber_count
video_count
view_count
country/category signals where available
```

Store the exact observation timestamp.

## 10.3 Video observations

Capture:

```text
video_id
channel_id
published_at
title
description
view_count
like_count
comment_count
duration
```

Do not infer watch time or CTR for competitors. Those are private analytics and should not be fabricated.

## 10.4 Refresh policy

Existing objects should be refreshed independently from discovery.

For example:

```text
new discovery → immediate metadata fetch
high-velocity video → 24h refresh
normal video → 72h refresh
old low-activity video → weekly/monthly refresh
```

The actual intervals should be configurable.

---

# 11. Derived YouTube metrics

## 11.1 Video age

```python
age_hours = max((observed_at - published_at).total_seconds() / 3600, minimum_epsilon)
```

Use UTC internally.

## 11.2 Raw view velocity

```text
views_per_day = current_views / max(age_days, epsilon)
```

Do not use this alone because old videos can dominate cumulative averages.

## 11.3 Channel-relative performance

```text
views_to_subscriber_ratio = views / max(channel_subscribers, 1)
```

Mark this metric as unstable when subscriber count is missing or zero.

## 11.4 Engagement proxies

```text
like_rate = likes / max(views, 1)
comment_rate = comments / max(views, 1)
```

These are descriptive proxies, not direct measures of recommendation quality.

## 11.5 Freshness-adjusted velocity

Prefer actual observation deltas where multiple observations exist:

```text
views_delta = views_t2 - views_t1
window_days = (t2 - t1).days
views_per_day = views_delta / window_days
```

This is preferred over cumulative lifetime velocity when sufficient historical observations exist.

---

# 12. Growth measurement

P1 must calculate growth at the **concept/market-arm level** as well as the individual-video level.

For a given market arm:

```text
current_window = recent 30 days
baseline_window = preceding comparable 30 days
```

Calculate:

```text
growth_rate = (current_activity - baseline_activity) / max(abs(baseline_activity), epsilon)
```

When the baseline is too small, mark growth as unstable rather than producing absurd percentages.

A second, more robust metric should compare recent vs historical median normalized performance.

Suggested features:

```text
median_velocity_7d
median_velocity_30d
median_velocity_90d
growth_30d_vs_prev30d
growth_30d_vs_prev90d
```

---

# 13. Competition / saturation model

Do not use a simplistic "number of search results" metric.

At minimum calculate:

```text
active_channel_count
active_video_count
median_video_velocity
p75_video_velocity
p90_video_velocity
median_channel_size
top_10_channel_share
new_upload_rate
```

Normalize these against the broader dataset.

A high-demand market with a handful of dominant channels has a different competitive profile from a fragmented market with hundreds of modest channels.

## Concentration

Calculate a concentration measure such as HHI over channel-normalized activity:

```text
share_i = channel_activity_i / total_activity
HHI = sum(share_i ** 2)
```

This is an analytic indicator, not a legal antitrust assessment.

Persist both:

```text
concentration_raw
concentration_normalized
```

and the sample size.

---

# 14. Market taxonomy

## 14.1 Goal

Create a stable controlled vocabulary so that:

```text
"late night driving"
"night drive"
"2am drive"
"midnight drive"
```

can map to the same canonical use-case while retaining the original phrase.

## 14.2 Taxonomy dimensions

Initial controlled dimensions:

```text
Genre
Subgenre
Mood
UseCase
Language
VocalMode
VocalGender
Energy
BPMRange
DurationBucket
VisualWorld
AudienceIntent
TitleArchetype
```

## 14.3 Controlled values

Use enums for stable high-level dimensions.

Allow an `other_candidate` bucket for observations the ontology does not yet cover.

Do not silently map unfamiliar terms to the nearest known label.

New labels should go through a taxonomy-expansion workflow.

---

# 15. LLM taxonomy classification

The LLM should receive structured text inputs only, for example:

```text
Title:
"2AM Seoul — Rainy Night R&B Mix"

Description:
"Late-night Korean city vibes..."

Existing taxonomy:
...
```

Required output schema:

```json
{
  "genre": ["rnb"],
  "subgenre": ["alt_rnb"],
  "mood": ["melancholic", "dreamy"],
  "use_case": ["late_night_drive"],
  "language": ["english"],
  "vocal_mode": "vocal",
  "energy": "low_medium",
  "visual_world": ["rainy_asian_city"],
  "title_archetypes": ["time_place_mood"],
  "confidence": 0.87,
  "unmapped_terms": []
}
```

The application must validate the JSON against a schema before writing it to the database.

The LLM must never be trusted to output a final numeric market score.

---

# 16. Concept / content-arm generation

After enough observations are classified, generate candidate arms from frequently co-occurring attributes.

An arm is a **production hypothesis**, not a claim that the market wants it.

Required arm fields:

```text
arm_id
canonical_genre
canonical_subgenre
mood
use_case
language
vocal_mode
vocal_gender
bpm_range
energy
video_duration_bucket
visual_world
title_archetype
source_observation_ids
created_at
status
```

Status values:

```text
candidate
active
paused
retired
```

P2 will decide experiment allocation. P1 only surfaces the candidates and calculates their market context.

---

# 17. Initial opportunity scoring

## 17.1 Important boundary

P1 creates a **market opportunity score**, not a predicted YouTube success probability.

Do not name the field:

```text
probability_of_success
```

because no channel-specific experiment data exists yet.

Use:

```text
market_opportunity_score
```

## 17.2 Starting components

Initial default weights:

```text
Demand                25%
Recent Growth         20%
Competitor Velocity   15%
Competition/Saturation15%
Audience Quality      10%
Cross-platform        10%
Production Fit         5%
Confidence adjustment  applied separately
```

All weights must be configuration values.

## 17.3 Component definitions

### Demand score

Composite of normalized evidence such as:

```text
median normalized views
query prevalence
active-channel audience size
recent activity
```

### Growth score

Composite of:

```text
velocity acceleration
growth_30d_vs_prev30d
growth_30d_vs_historical_baseline
```

### Competitor velocity score

Measure how strongly active competitors are growing, not merely how large they are.

### Competition score

Invert normalized saturation/concentration penalties so that lower saturation generally contributes more opportunity.

### Audience quality score

Use signals such as:

```text
repeat playlist patterns
long-form prevalence
engagement consistency
international spread
```

Only use observable public signals.

### Cross-platform score

Use independent permitted evidence from non-YouTube market sources.

If a secondary source is unavailable, reduce confidence; do not automatically assign zero demand.

### Production fit score

This is not an economic prediction. It measures how feasible the candidate is for the planned automated production system.

Example factors:

```text
music-generation compatibility
visual generation compatibility
copyright-risk complexity
language support
metadata difficulty
```

---

# 18. Normalization method

Each component should be normalized onto 0–100.

Do not use min/max normalization on tiny samples because one outlier can dominate the entire scale.

Preferred default:

1. Winsorize extreme values using configurable percentiles.
2. Compute robust percentile/rank scores.
3. Convert to 0–100.

For example:

```text
p10 → 0
p50 → 50
p90 → 100
```

with monotonic interpolation between anchors.

Persist the normalization version.

---

# 19. Confidence model

Confidence must be independent of the opportunity score.

Candidate inputs:

```text
sample_size
source_count
source_agreement
freshness
observation_depth
metric_stability
classification_confidence
```

Example conceptual formula:

```text
confidence
= weighted combination of
  sample confidence
  + source diversity
  + time-series depth
  + taxonomy confidence
  + freshness
```

Clamp to 0–1.

The exact initial coefficients should be configurable and documented.

Do not allow high opportunity scores to imply high confidence.

Example:

```text
market_opportunity_score = 89
confidence = 0.31
```

This means:

> "The available data looks interesting, but there is not enough evidence yet."

---

# 20. Opportunity object

P2 should receive a response structurally similar to:

```json
{
  "arm_id": "rnb_late_night_drive_en_vocal_60",
  "market_opportunity_score": 82.4,
  "confidence": 0.71,
  "as_of": "2026-09-20T06:00:00Z",
  "taxonomy": {
    "genre": "rnb",
    "subgenre": "alt_rnb",
    "mood": ["melancholic", "dreamy"],
    "use_case": ["late_night_drive"],
    "language": "english",
    "vocal_mode": "vocal",
    "duration_bucket": "45_75m",
    "visual_world": "rainy_asian_city",
    "title_archetype": "time_place_mood"
  },
  "components": {
    "demand": 84.0,
    "growth": 91.2,
    "competitor_velocity": 78.2,
    "competition": 55.1,
    "audience_quality": 71.0,
    "cross_platform": 80.0,
    "production_fit": 90.0
  },
  "evidence": {
    "observation_count": 143,
    "source_count": 3,
    "top_observation_ids": ["...", "..."]
  },
  "score_version": "p1-v1"
}
```

---

# 21. Freshness rules

Every score must have an `as_of` timestamp.

Suggested initial freshness buckets:

```text
0–48h       fresh
3–7d        normal
8–30d       stale
>30d        expired for current-market decisions
```

These are defaults. P2 may request a stricter freshness requirement.

A score built from stale observations must have lower confidence.

---

# 22. Deduplication and entity resolution

YouTube IDs are canonical when available.

For non-ID concepts use normalized text fingerprints.

Normalize text by:

```text
Unicode normalization
case folding
whitespace normalization
punctuation normalization
```

Do not remove meaningful musical terms during normalization.

Example:

```text
"2AM Seoul 🌙"
"2am seoul"
```

may map to the same textual concept, but the original title must still be retained.

---

# 23. Error handling

All connectors must classify errors into:

```text
AUTH_FAILURE
RATE_LIMIT
TRANSIENT_NETWORK
PROVIDER_5XX
INVALID_REQUEST
NOT_FOUND
QUOTA_EXCEEDED
SCHEMA_CHANGE
PARSER_FAILURE
DATA_QUALITY
UNSUPPORTED_OPERATION
```

## Retry rules

Retry only retryable errors.

Use exponential backoff with jitter.

Never retry:

```text
invalid request
missing permission
schema validation failure caused by our code
known unsupported endpoint
```

For quota/rate-limit errors, honor provider guidance when available and reschedule instead of hammering the API.

---

# 24. Data-quality gates

A collection run may finish with `partial` status if some queries succeed and others fail.

Minimum gates:

```text
no malformed provider IDs
no negative counts
no timestamps in impossible order
no negative video duration
no duplicate canonical objects without explicit versioning
no NaN/Inf persisted
no score outside 0–100
no confidence outside 0–1
```

When data quality fails, quarantine the observation instead of silently dropping it.

---

# 25. API contract for P2

Implement a read-only API initially.

### `GET /market/opportunities`

Query parameters:

```text
limit
cursor
min_score
min_confidence
freshness
country
language
genre
use_case
```

Return:

```text
items
next_cursor
as_of
score_version
```

### `GET /market/opportunities/{arm_id}`

Return:

```text
arm
score
component_scores
confidence
evidence_summary
observation_counts
source_counts
last_updated
```

### `GET /market/observations/{id}`

Return the normalized observation and source provenance.

### `GET /market/runs/{id}`

Return collection-run status and metrics.

P2 should never need to access raw database tables directly.

---

# 26. Scheduled jobs

At minimum:

```text
market.seed_refresh
market.youtube_discovery
market.youtube_refresh
market.taxonomy_classification
market.feature_recompute
market.opportunity_scoring
market.stale_data_cleanup
```

Recommended initial cadence:

```text
Discovery: daily
High-value refresh: daily
Normal refresh: every 3 days
Taxonomy recompute: daily incremental + weekly full recompute
Opportunity scoring: daily
Historical backfill: manual/on demand
```

The scheduler must permit per-query overrides.

---

# 27. Caching and quota management

Because YouTube API calls consume quota, P1 should avoid re-fetching unchanged metadata unnecessarily.

Implement:

```text
request cache
ETag/conditional requests where supported
object freshness timestamps
per-provider quota counters
per-run request budgets
```

The system must expose:

```text
quota_used
quota_remaining if available
requests_per_query
cache_hit_rate
```

If quota is exhausted, the scheduler should stop low-priority work first and preserve quota for priority queries.

---

# 28. LLM cost control

LLM classification is likely to be one of the avoidable costs.

Use a tiered policy:

```text
local model → bulk simple classification
strong model → ambiguous/high-value classification
human → only high-risk taxonomy conflicts if eventually needed
```

Do not call the strong model for every observation.

Cache classification by content fingerprint + taxonomy version.

If the same title/description has already been classified against the same taxonomy version, reuse the result.

---

# 29. Taxonomy versioning

Every classification must specify:

```text
taxonomy_version
classifier_model
classifier_version
classification_prompt_version
classification_schema_version
```

When taxonomy changes materially, do not overwrite old classifications.

Store a new classification version so historical scores remain reproducible.

---

# 30. Score reproducibility

Given the same:

```text
observation set
feature version
normalization version
score weights
score version
```

P1 must produce the same score.

Randomness is prohibited in deterministic feature/scoring functions.

Any LLM-generated taxonomy decisions must be persisted so the numeric scoring layer can be reproduced without rerunning the model.

---

# 31. Market report

Generate a machine-readable and human-readable report daily.

### Human report sections

```text
1. Market snapshot
2. Fastest-growing concepts
3. Largest demand clusters
4. Lowest-saturation attractive clusters
5. New taxonomy discoveries
6. Data-quality warnings
7. Source freshness
8. Top opportunity arms
9. Low-confidence opportunities
10. Changes vs previous snapshot
```

The report must distinguish:

```text
OBSERVED
DERIVED
CLASSIFIED
ASSUMED
```

Do not write speculative conclusions as if directly observed.

---

# 32. Change detection

P1 should identify significant changes in market conditions.

Examples:

```text
opportunity score change > configurable threshold
growth component change > threshold
new query cluster discovered
competitor velocity spike
source availability degraded
```

Store a `market_change_event`.

P2 can later use these events to trigger exploration.

---

# 33. Acceptance criteria

P1 is complete only when all of the following are true.

## AC-01 — Source collection

The system can execute a YouTube discovery query, persist raw results, normalize them, and expose canonical observations.

## AC-02 — Provenance

Every normalized observation can be traced to an immutable raw observation and source request context.

## AC-03 — Historical observations

Re-running the same query on different dates creates time-series observations rather than overwriting the original.

## AC-04 — Query expansion

The system can generate new candidate research terms from observed market language and store their lineage.

## AC-05 — Taxonomy

At least the initial dimensions are classified and schema-validated:

```text
Genre
Subgenre
Mood
UseCase
Language
VocalMode
Energy
DurationBucket
VisualWorld
TitleArchetype
```

## AC-06 — Feature calculations

The system calculates:

```text
views/day or windowed view velocity
like rate
comment rate
channel-relative performance
growth metrics
competition metrics
concentration metrics
freshness
```

with explicit handling for insufficient data.

## AC-07 — Opportunity score

The system outputs a 0–100 `market_opportunity_score` and independent 0–1 confidence value.

## AC-08 — Reproducibility

Replaying the same stored observations with the same scoring version produces identical scores.

## AC-09 — API

P2 can query ranked opportunity arms without direct database access.

## AC-10 — Scheduling

Daily discovery/refresh/scoring runs can execute without manual intervention.

## AC-11 — Quota safety

The collector tracks and enforces provider request budgets and does not continue blindly after quota exhaustion.

## AC-12 — Failure recovery

Transient failures retry; permanent failures quarantine; partial runs are visible and resumable.

## AC-13 — Tests

Unit tests cover all feature/scoring functions.

Integration tests cover at least one complete collection→normalize→classify→score path with mocked provider responses.

Contract tests validate provider response mapping.

## AC-14 — Observability

A run dashboard/log output can answer:

```text
what ran?
what source?
which queries?
how many objects?
what failed?
how much quota?
how much LLM cost?
what scores changed?
```

---

# 34. Required tests

## Unit tests

Minimum cases:

```text
age calculation
velocity calculation
windowed growth
zero baseline
tiny baseline
missing subscriber count
zero views
zero likes
zero comments
competition normalization
HHI calculation
winsorization
percentile normalization
score weighted sum
confidence bounds
freshness buckets
```

## Property tests

For scoring:

```text
increasing a positive demand input must not decrease final score
increasing saturation must not increase competition-opportunity score
score always in [0,100]
confidence always in [0,1]
```

## Integration test

Use deterministic fixtures representing:

```text
3 channels
20 videos
3 query seeds
2 taxonomy clusters
2 observation timestamps
1 transient API error
1 rate-limit response
```

Expected:

- retries occur where appropriate;
- no duplicate canonical videos;
- historical observations preserved;
- scores produced;
- failed collection reflected as partial;
- P2 API returns ranked opportunities.

---

# 35. Example fixture

Create fixture data deliberately designed to test the difference between raw popularity and opportunity.

Example:

```text
Channel A
10M subscribers
600k views/video
slow recent growth
high market concentration

Channel B
20k subscribers
45k views/video
fast recent growth
rapid upload cadence

Channel C
80k subscribers
35k views/video
stable growth
low competitive concentration
```

The scoring system must not blindly rank Channel A highest simply because its raw views are highest.

The purpose of the fixture is to validate normalization and market-opportunity logic.

---

# 36. What the P1 AI agent is allowed to decide

The implementation AI may choose:

- exact Python libraries;
- SQL indexes;
- repository abstractions;
- async/sync implementation;
- exact logging library;
- exact HTTP client;
- whether to use Pydantic/dataclasses internally;
- exact database migration framework if not already established;
- implementation details of caching;
- modular internal class structure.

Provided that behavior and contracts specified in this document remain intact.

---

# 37. What the P1 AI agent must NOT decide silently

Do not silently change:

- the definition of an opportunity arm;
- the required taxonomy dimensions;
- the distinction between raw/derived/classified/assumed data;
- source provenance requirements;
- deterministic scoring requirements;
- confidence semantics;
- null-vs-zero semantics;
- historical immutability;
- P1/P2 boundary;
- quota safety;
- data-quality quarantine behavior;
- API contracts;
- score/version reproducibility.

If an implementation choice materially affects any of those, record the decision in the repository's decision log and update this document rather than inventing an undocumented alternative.

---

# 38. Reasonable implementation flexibility

This specification intentionally does not dictate:

- one ORM;
- one LLM;
- one embedding model;
- one job scheduler implementation if Temporal is not yet installed;
- one trend provider;
- one storage serialization format;
- one exact classifier prompt;
- one exact coefficient for confidence.

The system should remain replaceable and testable.

The important thing is that these choices are isolated behind explicit interfaces and versioned configuration.

---

# 39. Recommended implementation sequence

Implement in this order:

### P1.1 — Domain contracts

Define enums, entities, pydantic schemas, IDs and status values.

### P1.2 — Database migrations

Create raw, normalized, query, run, taxonomy and opportunity tables.

### P1.3 — YouTube connector

Implement authentication injection, discovery, resource hydration, pagination, quota tracking, retry/backoff and fixtures.

### P1.4 — Raw observation storage

Persist immutable payloads and request provenance.

### P1.5 — Normalization / entity resolution

Create canonical channel/video/playlist entities and time-series observations.

### P1.6 — Seed manager

Implement bootstrap seeds, expansion lineage and scheduling priority.

### P1.7 — Taxonomy

Implement the controlled vocabulary, classifier interface, output validation and cached classification.

### P1.8 — Feature engineering

Implement demand, growth, velocity, competition, concentration, freshness and confidence features.

### P1.9 — Opportunity scorer

Implement versioned deterministic scoring and evidence storage.

### P1.10 — API

Expose ranked opportunity arms and evidence.

### P1.11 — Scheduler

Automate daily collection and scoring.

### P1.12 — Reports/observability

Expose run summaries, market snapshots, errors, quota and score changes.

### P1.13 — Tests

Complete unit/property/integration/contract tests.

### P1.14 — End-to-end acceptance run

Run a full mock-backed cycle and a controlled real API smoke test after credentials are configured.

---

# 40. Real API smoke test

The real API smoke test must use a small quota-safe query set.

It must verify:

```text
OAuth credential works
search request works
pagination works
resource hydration works
raw payload stored
normalized observation stored
taxonomy classification works
features calculated
opportunity score calculated
API returns result
```

Do not perform a large crawl during initial credential validation.

---

# 41. Security requirements

Never put credentials into:

```text
source code
Git
raw observation payloads
LLM prompts
logs
market reports
```

Use environment variables or the repository's existing secrets manager.

Redact authorization headers and access tokens from logs.

Store only the minimum provider data necessary for research and provenance.

---

# 42. Cost-control requirements

P1 must expose:

```text
api_calls
api_quota_units
llm_calls
llm_tokens if available
llm_cost_estimate
browser_actions
storage_bytes
```

Attach all costs to `collection_run_id` where possible.

This lets P9 eventually evaluate the economics of market research itself.

---

# 43. Operational safeguards

P1 must stop or degrade gracefully when:

```text
provider quota > threshold
unexpected API schema
massive query-result explosion
LLM classifier failure rate spikes
source freshness collapses
normalization error rate exceeds threshold
```

Example:

```text
If >5% of YouTube records fail schema validation in one run:
    mark run PARTIAL
    quarantine invalid records
    raise alert
    do not silently continue as if healthy
```

Thresholds must be configurable.

---

# 44. Expected outputs after successful P1 completion

A healthy P1 installation should be capable of producing an output resembling:

```text
Market Snapshot — 2026-09-20

Queries collected: 184
Channels observed: 3,412
Videos observed: 28,104
New concepts discovered: 217
Active opportunity arms: 96

Top opportunity clusters:

1. <cluster A>
   Market score: 84.2
   Confidence: 0.73
   Demand: 88
   Growth: 91
   Competition: 51

2. <cluster B>
   Market score: 82.5
   Confidence: 0.69
   ...

Data-quality warnings: 2
Stale sources: 1
Quota used: ...
LLM classification calls: ...
Estimated research cost: ...
```

The exact winning cluster is intentionally not hard-coded. The system must discover it from data.

---

# 45. Definition of done

P1 is done when a fresh environment can be configured with credentials, run the market-intelligence workflow unattended, survive normal transient failures and quota constraints, produce auditable opportunity arms, and expose them to P2 through a stable API.

The most important end-state property is:

> **A downstream AI must be able to ask "What are the current market opportunities?" and receive ranked, evidence-backed, confidence-aware hypotheses without needing to understand any external API, scrape any website, inspect raw tables, or trust an LLM's unsupported opinion.**

---

# 46. Non-goals that protect the architecture

P1 is not a fortune teller.

It does not claim:

```text
"this genre will go viral"
"this video will get X views"
"this is the best genre"
```

It produces:

```text
"this market arm currently shows stronger/weaker observed evidence under score version X, with confidence Y"
```

P2 is responsible for experimental allocation, and P9/P10 are responsible for learning from the project's own results.

That separation is intentional and must be preserved.
