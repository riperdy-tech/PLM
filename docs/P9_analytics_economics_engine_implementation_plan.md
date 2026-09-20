# P9 — Analytics & Economics Engine
## Detailed Implementation Plan

**Document status:** Implementation-ready specification  
**Phase:** P9 of the Autonomous AI Music Media Engine  
**Primary objective:** Build the canonical measurement, attribution, revenue, cost, unit-economics, cohort, experimentation-evaluation and reporting layer that converts P7/P8 outcomes into trustworthy business intelligence for P10 recursive optimization.

---

# 1. Purpose

P9 is the system's **measurement and economics control plane**.

P1 discovers external market opportunity.  
P2 allocates experiments/opportunities.  
P3 defines what experiment/content should be built.  
P4 generates candidate music.  
P5 validates quality, provenance and release eligibility.  
P6 builds media packages.  
P7 publishes to YouTube.  
P8 distributes recordings to DSPs/stores.

P9 answers:

> **What happened, who saw/heard it, where did the audience come from, what did each asset cost, what revenue was generated, which experiment variables appear associated with the outcome, what is the current economic value of the catalog, and how certain are we?**

P9 must be the canonical source for **project-owned analytics and economics**.

P9 is intentionally not the autonomous decision maker. P10 consumes P9 outputs to decide how to change future allocation and behavior.

The system must never confuse:

```text
OBSERVED METRIC
vs
DERIVED METRIC
vs
ATTRIBUTED RESULT
vs
FORECAST
vs
CAUSAL CLAIM
```

Those categories must remain explicit in the data model and API.

---

# 2. Scope

## 2.1 In scope

P9 must implement:

1. P7 YouTube publication-state ingestion.
2. YouTube Analytics API ingestion for owned channel data.
3. YouTube Reporting API ingestion for bulk historical/channel reports where available and authorized.
4. P8 distribution-state ingestion.
5. Distributor/DSP report ingestion where authorized.
6. Canonical metric normalization.
7. Daily and intraday time-series storage.
8. Per-video performance metrics.
9. Per-track performance metrics.
10. Per-playlist performance metrics.
11. Per-release performance metrics.
12. Per-channel performance metrics.
13. Audience geography and traffic-source analytics where available.
14. Subscriber acquisition/attrition metrics.
15. Watch-time and retention measurements where available.
16. Revenue ingestion and normalization.
17. Revenue-source classification.
18. Cost ingestion and allocation.
19. Track-level, video-level, playlist-level and release-level P&L.
20. Unit-economics calculations.
21. ROI calculations.
22. Catalog economics.
23. Revenue-per-view and revenue-per-stream derived metrics.
24. Cohort analysis.
25. Experiment performance evaluation.
26. Delayed attribution handling.
27. Data freshness and completeness monitoring.
28. Late-arriving/backfilled data handling.
29. Metric reconciliation across source systems.
30. Outlier and anomaly detection.
31. Forecasting primitives.
32. Confidence/uncertainty reporting.
33. P10-ready learning features.
34. Executive/operational dashboards.
35. Daily/weekly/monthly reports.
36. Audit/event lineage.
37. Reprocessing and backfill capabilities.
38. Cost controls.
39. Data retention and privacy controls.
40. Comprehensive tests.

## 2.2 Explicitly out of scope

Do not implement in P9:

- Autonomous production allocation.
- Bandit policy updates.
- Generative music selection.
- Music generation.
- Video generation.
- YouTube publishing.
- DSP release submission.
- Artificial streaming.
- Purchased/fake engagement.
- Paid guaranteed playlist placement.
- Scraping private analytics without authorization.
- Pretending third-party competitor analytics are available when they are not.
- Fully autonomous financial spending decisions.
- Tax/legal accounting as a substitute for a qualified accountant.

P10 will use P9 outputs to make autonomous future-allocation decisions.

---

# 3. Core architectural principle

P9 must be a **measurement ledger first and a dashboard second**.

```text
External platforms / distributors
            ↓
      Raw metric ingestion
            ↓
       Normalization
            ↓
     Identity resolution
            ↓
      Metric fact store
            ↓
     Attribution engine
            ↓
   Revenue + cost ledger
            ↓
       Unit economics
            ↓
   Cohort / experiment views
            ↓
     Forecast / uncertainty
            ↓
        P10 features
```

Dashboards are projections of the underlying measurement system.

Never make the dashboard database the canonical source.

---

# 4. Measurement philosophy

## 4.1 Preserve source truth

Every ingested metric must retain:

```text
source
provider
report_type
source_row_hash
observed_at
reported_for_date
retrieved_at
currency where applicable
source_record_identifier
```

## 4.2 Never silently overwrite history

Platform reports can be revised or backfilled.

Therefore a source record should be modeled as a **versioned observation**.

For a logical date/entity/metric tuple, later observations may supersede earlier ones without deleting the historical ingestion evidence.

Example:

```text
2026-09-20 views = 10,000 (retrieved Sep 21)
2026-09-20 views = 10,250 (retrieved Sep 23)
```

The normalized current truth may become 10,250, but the original ingestion remains auditable.

---

# 5. Current platform constraints relevant to P9

YouTube Analytics API reports require authorized access and can return metrics such as views, likes, watch time, average view duration, subscriber changes and monetization/revenue metrics depending on the report and OAuth scopes. The current official query API documentation explicitly distinguishes metric and dimension handling and documents monetary scopes for estimated revenue. Reference:

https://developers.google.com/youtube/analytics/reference/reports/query

The YouTube Reporting API provides bulk reports, including daily channel/video activity, traffic sources, geography, playlist interactions and other metrics. Reports are delivered as files and can be revised/backfilled; consumers must therefore support re-importing corrected data. Reference:

https://developers.google.com/youtube/reporting/v1/reports

YouTube's current reports expose dimensions such as country and traffic-source/playback-location information where supported. Reference:

https://developers.google.com/youtube/analytics/dimensions

P9 must not infer that every report is available for every account. Capability discovery and actual API availability must be checked at runtime during initialization.

For DSP revenue, P9 must consume authorized distributor/store statements rather than assuming a fixed per-stream royalty. Spotify and other DSPs do not promise a universal fixed payout per stream; effective payout depends on market, rights, revenue pool, contracts, territories and reporting periods.

---

# 6. Canonical entity hierarchy

P9 must preserve the following hierarchy:

```text
Brand
  ↓
Channel
  ↓
Video / YouTube playlist
  ↓
Playlist membership

Brand
  ↓
Artist
  ↓
Release
  ↓
Track
  ↓
DSP/store delivery
```

Cross-platform mapping:

```text
Track
 ├── P4 song identity
 ├── P5 cleared asset
 ├── P6 playlist placement
 ├── P7 YouTube video
 ├── P8 release
 ├── Spotify/store track ID
 └── revenue records
```

This mapping must be explicit and versioned.

---

# 7. Canonical metric vocabulary

Every metric must have a defined semantic type.

Recommended top-level categories:

```text
ACTIVITY
ENGAGEMENT
AUDIENCE
ACQUISITION
RETENTION
REVENUE
COST
PROFITABILITY
DISTRIBUTION
CATALOG
EXPERIMENT
FORECAST
QUALITY
DATA_HEALTH
```

Metric definitions must include:

```text
metric_name
unit
granularity
source
calculation_definition
allowed_dimensions
lag_behavior
whether_cumulative
whether_revisable
```

---

# 8. Metric fact model

Create a generic metric fact table rather than hundreds of ad hoc columns.

Recommended shape:

```text
metric_fact
-----------
id
entity_type
entity_id
metric_name
metric_value_numeric
metric_value_text if applicable
unit
currency
reported_for_start
reported_for_end
observed_at
retrieved_at
source_id
source_report_id
source_record_id
observation_version
is_current
quality_status
confidence
created_at
```

However, do not force all high-volume analytics into EAV storage if performance becomes unacceptable.

For hot paths, materialized wide facts may coexist with the canonical metric ledger.

The implementation AI may choose columnar or fact-table optimizations, provided the semantic contract remains stable.

---

# 9. Source ingestion architecture

Define provider interfaces:

```text
AnalyticsProvider
RevenueProvider
DistributionMetricsProvider
SocialMetricsProvider
```

Concrete implementations should include at minimum:

```text
YouTubeAnalyticsProvider
YouTubeReportingProvider
DistributorReportProvider
```

Possible future:

```text
SpotifyArtistProvider if authorized and useful
AppleMusicProvider if authorized and useful
TikTokProvider if later added
```

P9 must not require a provider to expose every metric.

---

# 10. YouTube Analytics ingestion

## 10.1 Required report classes

At minimum attempt to support:

```text
video daily activity
channel daily activity
traffic source
geography
playlist performance
subscriber activity
revenue/ad performance where authorized
```

The exact report type IDs and metric names must be discovered from the current API rather than hard-coded from an outdated document.

## 10.2 Scope handling

Use the least-privileged OAuth scopes needed.

For ordinary analytics:

```text
https://www.googleapis.com/auth/yt-analytics.readonly
```

For monetary metrics where required:

```text
https://www.googleapis.com/auth/yt-analytics-monetary.readonly
```

P9 must gracefully operate in reduced capability mode if monetary permissions are not granted.

## 10.3 Query strategy

Use separate jobs for:

```text
activity
monetization
geography
traffic sources
playlist
```

Do not create giant high-dimensional reports unless they are verified to be valid and operationally affordable.

High-dimensional reports can cause sparse output and more complex reprocessing.

---

# 11. YouTube Reporting API ingestion

The Reporting API can provide bulk CSV reports and corrected/backfilled data.

Implement:

```text
reportTypes discovery
job creation
report availability polling
file download
CSV parsing
schema validation
row hashing
upsert/versioning
```

Store:

```text
report_type
report_id
download_url metadata
period_start
period_end
retrieved_at
file_hash
schema_version
```

Never rely only on filenames for identity.

---

# 12. Daily metric ingestion

Recommended initial schedule:

```text
01:00 UTC
previous-day analytics query

02:00 UTC
available Reporting API files

03:00 UTC
revenue/distributor reports

04:00 UTC
normalization

05:00 UTC
reconciliation

06:00 UTC
derived metric refresh

07:00 UTC
anomaly detection

08:00 UTC
daily report
```

Actual schedule should be configurable.

Do not assume reporting is available immediately after midnight. Respect source latency.

---

# 13. Intraday YouTube measurements

If the source and account permit sufficiently fresh data, collect selected intraday metrics for recently published videos.

Recommended window:

```text
0–6h after publication: frequent
6–24h: moderate
24–72h: less frequent
>72h: daily
```

Do not poll at high frequency indefinitely.

The system should allocate observation frequency based on expected information value and cost.

---

# 14. Publication-aligned windows

For every YouTube video, calculate canonical windows:

```text
first_1h
first_6h
first_24h
first_48h
first_72h
first_7d
first_14d
first_28d
first_90d
lifetime
```

Not all windows can be known immediately.

Represent future/unavailable windows as `NOT_READY`, not zero.

---

# 15. Track-aligned windows

For every distributed track:

```text
first_1d
first_7d
first_14d
first_28d
first_90d
monthly
lifetime
```

Store both:

```text
calendar metrics
age-since-release metrics
```

This allows comparison between releases launched at different times.

---

# 16. Core YouTube metrics

At minimum support:

```text
views
watch_time_minutes
average_view_duration_seconds
average_view_duration_percentage
likes
comments
shares
subscribers_gained
subscribers_lost
engaged_views
```

Where account access exposes them, support:

```text
estimated_revenue
estimated_ad_revenue
estimated_red_partner_revenue
cpm
playback_based_cpm
```

Do not assume estimated revenue is final accounting revenue.

Label it:

```text
revenue_status = ESTIMATED
```

Official YouTube documentation distinguishes estimated revenue metrics and provides monetary-reporting scopes. Reference:

https://developers.google.com/youtube/analytics/reference/reports/query

---

# 17. Traffic-source metrics

Where available, store traffic source categories and details.

Examples include:

```text
browse
search
suggested
external
playlist
channel
notifications
other
```

Do not convert provider-specific categories into a generic taxonomy without retaining the original provider value.

Keep:

```text
source_raw
source_canonical
```

---

# 18. Geography

Store country using ISO-3166 where the source supplies country codes.

Do not infer exact user location from ambiguous data.

Example canonical fields:

```text
country_code
region_code where supported
city where supported
```

YouTube documents country, province, city and playback dimensions for supported reports. Reference:

https://developers.google.com/youtube/analytics/dimensions

Apply privacy/sparsity rules from the source rather than attempting to reverse-engineer anonymized rows.

---

# 19. Playlist analytics

Track:

```text
playlist_starts
playlist_saves_added
playlist_saves_removed
playlist_views
playlist_watch_time
```

where the current report exposes them.

Separate:

```text
video performance
```

from:

```text
video performance in playlist context
```

Do not combine them without labeling context.

---

# 20. Subscriber metrics

Store:

```text
subscribers_gained
subscribers_lost
net_subscribers
subscriber_conversion_rate
```

Derived:

```text
subscriber_conversion_rate = subscribers_gained / max(views, epsilon)
```

Treat this as a descriptive ratio, not a causal measure.

---

# 21. Retention metrics

Store source-provided retention metrics where available.

At minimum:

```text
average_view_duration
average_view_duration_percentage
```

If detailed retention curves are available through another authorized source, store them as time-indexed observations.

Do not manufacture retention curves from average duration.

---

# 22. Revenue model

P9 must distinguish at least:

```text
YOUTUBE_ESTIMATED_REVENUE
YOUTUBE_FINALIZED_REVENUE if available
DSP_REPORTED_REVENUE
DISTRIBUTOR_REPORTED_REVENUE
OTHER_REVENUE
```

Each revenue record must include:

```text
revenue_source
provider
currency
amount_gross
amount_net_if_known
period_start
period_end
statement_date
payment_date if known
rights_party
release_id
track_id where available
store
territory where available
```

Do not assume distributor-reported net revenue and DSP gross royalty reports are directly comparable.

---

# 23. Currency normalization

Persist original amounts exactly as received.

Additionally store a normalized reporting currency, initially KRW unless configuration says otherwise.

Fields:

```text
amount_original
currency_original
amount_normalized
currency_normalized
fx_rate
fx_source
fx_timestamp
fx_method
```

Never overwrite original currency values.

For estimated YouTube revenue, note that YouTube may calculate returned monetary metrics using daily-changing exchange rates. Its Analytics API documentation explicitly notes exchange-rate handling for estimated revenue metrics.

---

# 24. Revenue recognition semantics

P9 must distinguish:

```text
EARNED
REPORTED
RECEIVED
ESTIMATED
ACCRUED
```

Example:

```text
Spotify statement says
$100 royalty for July
```

This is `REPORTED/EARNED` when supported by the statement.

When $100 hits the bank account in September, that is `RECEIVED` in September.

Do not mix cash accounting and performance accounting.

---

# 25. Cost ledger

Create a canonical cost ledger.

Costs include:

```text
music_generation
llm
image_generation
video_generation
compute
GPU electricity estimate
cloud compute
storage
bandwidth
distribution fees
software subscriptions
API fees
transaction fees
other operating costs
```

Each cost entry must include:

```text
cost_id
cost_type
provider
amount
currency
incurred_at
service_period
allocation_scope
allocation_basis
source_record
```

---

# 26. Allocation scopes

Costs may apply to:

```text
company
brand
channel
playlist
video
release
track
experiment
job
```

Use explicit allocation rules.

Examples:

```text
Suno subscription → monthly software overhead
individual generation credit → generation job
FFmpeg local GPU → compute cost estimate per job
DistroKid annual fee → distributor overhead / monthly amortization
```

Do not allocate a company-wide subscription arbitrarily to individual songs without retaining the allocation method.

---

# 27. Cost allocation ledger

Store both:

```text
source_cost
```

and:

```text
allocated_cost
```

Example:

```text
monthly API cost = ₩50,000

allocated across:
video A = ₩10,000
video B = ₩15,000
track C = ₩25,000
```

The source ledger remains unchanged.

---

# 28. Compute economics

Because the system uses local GPU resources, compute cost must be measurable.

For each job capture:

```text
machine_id
GPU model
job_start
job_end
duration_seconds
power_estimate_watts if measured/estimated
energy_kwh if available
CPU time
GPU time
artifact_count
```

Then calculate an operational estimate:

```text
compute_cost = energy_cost + allocated infrastructure cost
```

Clearly label estimated values.

Do not pretend GPU electricity is an exact accounting figure unless actual power measurements exist.

---

# 29. Track-level P&L

For each track:

```text
Generation cost
QA cost
Visual allocation
Distribution allocation
YouTube attributed revenue
DSP revenue
Other revenue
Gross contribution
Net contribution
ROI
Payback period
```

Core formula:

```text
net_contribution
= attributable_revenue - attributable_cost
```

```text
ROI
= attributable_revenue / max(attributable_cost, epsilon)
```

Also provide:

```text
ROI_excluding_overhead
ROI_including_overhead
```

Never hide the allocation methodology.

---

# 30. Video-level economics

For every video calculate:

```text
views
watch_hours
subscribers_gained
estimated_revenue
allocated_production_cost
allocated_distribution_cost if any
net_contribution
revenue_per_1k_views
revenue_per_watch_hour
cost_per_1k_views
```

Store the denominator explicitly.

Do not call a metric "RPM" unless it follows the platform's specific RPM definition.

Prefer internally named metrics such as:

```text
revenue_per_1000_views_internal
```

---

# 31. Playlist economics

A playlist/mix can contain tracks that also appear elsewhere.

Therefore calculate two levels:

```text
video economics
```

and:

```text
catalog/track economics
```

Do not double-count a track's generation cost every time it is reused in a playlist.

Instead maintain:

```text
asset creation cost
```

separately from:

```text
placement/distribution cost
```

---

# 32. Catalog economics

At catalog level calculate:

```text
catalog_size
active_tracks
released_tracks
monthly_streams
monthly_youtube_views
monthly_revenue
gross_margin
contribution_margin
revenue_per_track
revenue_concentration
```

Also calculate:

```text
percent_of_revenue_from_top_1_track
top_5_share
top_10_share
```

This shows whether the business is diversified or dependent on a few songs.

---

# 33. Revenue concentration

Calculate concentration by:

```text
track
release
video
channel
market
country
platform
```

For each dimension report:

```text
HHI or equivalent concentration indicator
Top-N revenue share
median revenue
mean revenue
```

Treat concentration as descriptive risk information, not a forecast.

---

# 34. Attribution model

P9 needs attribution because:

```text
one track → many videos
one playlist → many tracks
one release → many stores
one video → many tracks
```

Therefore attribution must support multiple relationship types.

Required relation types:

```text
VIDEO_CONTAINS_TRACK
VIDEO_BUNDLES_PLAYLIST
RELEASE_CONTAINS_TRACK
TRACK_DISTRIBUTED_TO_STORE
TRACK_FEATURED_IN_VIDEO
TRACK_FEATURED_IN_SHORT
```

---

# 35. Attribution policy hierarchy

Use the strongest available evidence first.

Priority:

```text
1. Explicit identity mapping
2. ISRC / provider ID mapping
3. P6 manifest mapping
4. P7 manifest mapping
5. P8 distribution mapping
6. Filename/hash mapping
7. Conservative manual mapping
```

Do not use fuzzy title matching as unquestioned truth.

If only fuzzy evidence exists:

```text
attribution_status = UNCERTAIN
```

---

# 36. Playlist attribution

For a 60-minute playlist containing 10 tracks, YouTube revenue is not automatically separable into 10 equal portions.

Therefore P9 must support:

```text
video-level revenue = observed
track-level attributed revenue = MODELLED
```

Possible attribution modes:

```text
EQUAL_SPLIT
WATCH_TIME_WEIGHTED
TRACK_POSITION_WEIGHTED
EXPLICIT_EXTERNAL_SIGNAL
NONE
```

The default must be `NONE` unless a defensible allocation basis is available.

If P10 needs track-level estimates, it can consume modelled values with explicit uncertainty.

---

# 37. Attribution uncertainty

Every non-explicit attribution must include:

```text
attribution_method
attribution_confidence
attribution_model_version
```

Never mix modelled revenue with observed revenue in one unlabeled number.

Provide both:

```text
observed_revenue
attributed_revenue_estimate
```

---

# 38. Experiment linkage

Every P7 video and P8 release must be linked back to:

```text
experiment_id
experiment_family_id
content_arm_id
production_directive_id
content_genome_id
```

If the linkage is missing, mark the result:

```text
experiment_context = UNKNOWN
```

Do not retroactively guess the experiment assignment.

---

# 39. Experiment outcome record

For every experiment unit create:

```text
experiment_result
-----------------
experiment_id
unit_id
arm_id
start_time
observation_window
eligible_status
metric_snapshot
outcome_value
confidence
quality_status
```

This is the main P10 interface.

---

# 40. Primary experiment metrics

P9 should support a hierarchy of metrics rather than one universal KPI.

### Primary business metrics

```text
net_contribution
revenue_per_video
revenue_per_track
revenue_per_1000_views
revenue_per_1000_streams_estimate
```

### Growth metrics

```text
views
watch_time
subscribers_gained
streams
```

### Quality metrics

```text
average_view_duration
average_view_duration_percentage
engagement_rate
```

### Catalog metrics

```text
repeat_consumption
playlist_reuse
track_longevity
```

---

# 41. Observation maturity

Not every experiment is mature enough to judge.

Define:

```text
IMMATURE
EARLY_SIGNAL
MATURE
STABILIZED
ARCHIVED
```

Example defaults:

```text
<24h: IMMATURE
24–72h: EARLY_SIGNAL
7–28d: MATURE
>28d: STABILIZED
```

These are initial heuristics, not universal laws.

P10 must be able to request results only from specified maturity states.

---

# 42. Comparison windows

P9 must calculate both absolute and normalized comparisons.

Examples:

```text
video age matched
channel age matched
publication month matched
traffic-source matched
country matched
```

This prevents comparing:

```text
1-day-old video
```

against:

```text
180-day-old video
```

using raw lifetime views.

---

# 43. Peer baselines

For each content arm, calculate peer baselines:

```text
median
p25
p50
p75
p90
```

for:

```text
views
watch time
subscriber conversion
revenue
retention
```

A new result should be evaluated relative to the correct cohort.

---

# 44. Robust statistics

Use robust statistics where distributions are heavy-tailed.

Prefer:

```text
median
trimmed mean
percentiles
winsorized means
log-transformed metrics
```

rather than raw means for highly skewed view/revenue distributions.

Do not discard outliers merely because they are inconvenient. Flag them and retain them.

---

# 45. Confidence intervals

Where sample size permits, calculate uncertainty intervals for aggregate metrics.

Examples:

```text
bootstrap CI
Wilson interval for proportions
credible interval for Bayesian summaries
```

The implementation may choose the appropriate method per metric.

Do not produce confidence intervals for deterministic ratios where assumptions are invalid.

---

# 46. Statistical significance vs. business significance

P9 must report separately:

```text
statistical evidence
```

and:

```text
economic magnitude
```

Example:

```text
+3% views
high confidence

but

+0.1% revenue
low economic materiality
```

Do not label a result "winner" merely because a p-value crosses a threshold.

P10 decides policy implications.

---

# 47. Multiple testing

The system will run many experiments.

P9 must track:

```text
number_of_comparisons
experiment_family
comparison_set
```

When performing large-scale significance testing, apply a documented multiple-testing strategy where appropriate.

Possible approaches:

```text
FDR control
family-wise correction
Bayesian posterior probabilities
```

The method must be explicit.

---

# 48. Causal inference boundary

P9 may report:

```text
Arm A had higher median 7-day views than Arm B.
```

P9 must not automatically report:

```text
Arm A caused 30% more views.
```

unless an explicitly designed causal experiment supports such a claim.

Observational data must be labeled observational.

---

# 49. Experiment contamination detection

P9 should flag experiments where conditions differ unexpectedly.

Examples:

```text
multiple uploads from same arm overlap heavily
cross-promotion occurred
playlist placement changed
thumbnail changed mid-test
video length changed unexpectedly
channel-wide event occurred
external traffic spike
```

Store:

```text
contamination_flag
contamination_reason
severity
```

P10 can decide whether to exclude, downweight or model contaminated results.

---

# 50. Event annotations

Build an event ledger:

```text
video_published
thumbnail_changed
title_changed
description_changed
playlist_changed
copyright_claim
strike
channel_policy_event
release_delivered
distributor_rejection
promo_event
external_media_event
```

This allows future models to distinguish performance changes caused by content from operational interventions.

---

# 51. Revenue lag model

DSP and distributor revenue can arrive months after consumption.

Therefore P9 must support:

```text
consumption_period
reporting_period
statement_period
payment_period
```

Do not attribute a September payout to September streams simply because payment arrived in September.

The source reporting period controls economic attribution.

---

# 52. Stream/revenue reconciliation

When a distributor reports:

```text
100,000 streams
$400 royalty
```

store:

```text
reported_streams = 100000
reported_revenue = 400
```

Derive:

```text
revenue_per_stream_estimate = 0.004
```

but explicitly name it:

```text
reported_revenue_per_stream
```

because it is a derived observation for that statement, not a universal Spotify rate.

---

# 53. Statement ingestion

Distributor reports may arrive as:

```text
CSV
XLSX
JSON
API
SFTP
webhook
manual upload
```

The ingestion layer must use a provider-specific parser interface.

Required parser contract:

```text
parse_statement()
validate_schema()
normalize_records()
extract_period()
extract_currency()
extract_ids()
```

---

# 54. Revenue statement deduplication

Every imported statement should have:

```text
provider_statement_id if available
file_hash
statement_period
provider
```

Do not double-count the same statement when it is uploaded twice.

If a provider republishes a corrected statement:

```text
statement_version = 2
```

and the correction must supersede the earlier normalized version while preserving audit history.

---

# 55. Data reconciliation

P9 should reconcile:

```text
P7 published videos
vs
YouTube analytics videos

P8 delivered tracks
vs
distributor statements

P8 store mappings
vs
P9 revenue records
```

Flag:

```text
MISSING_ANALYTICS
MISSING_DISTRIBUTION
MISSING_REVENUE
UNMAPPED_TRACK
UNMAPPED_VIDEO
CONFLICTING_IDENTITY
```

---

# 56. Identity conflicts

Possible conflict:

```text
same ISRC
→ two different internal track IDs
```

or:

```text
same YouTube video ID
→ two internal videos
```

P9 must fail closed on ambiguous canonical identity.

Do not silently merge.

Create an identity-conflict event for reconciliation.

---

# 57. Data quality states

Every metric dataset should have:

```text
VALID
PARTIAL
LATE
ESTIMATED
REVISED
ANOMALOUS
QUARANTINED
INVALID
```

A dashboard must be able to filter out invalid/quarantined metrics.

---

# 58. Missing-data semantics

Use distinct states:

```text
NULL = unknown
NOT_READY = expected later
NOT_APPLICABLE = metric does not apply
ZERO = verified zero
MISSING = expected but not received
```

Never convert all of these to zero.

---

# 59. Data completeness score

For each daily ingestion cycle calculate:

```text
expected_records
received_records
valid_records
invalid_records
late_records
```

Then:

```text
completeness_ratio = valid_expected_records / expected_records
```

The denominator must be defined by the source schedule.

---

# 60. Freshness score

For each data source:

```text
freshness_age = now - latest_expected_observation
```

Map it to:

```text
FRESH
ACCEPTABLE
STALE
CRITICAL
```

Thresholds must be provider/configuration specific.

---

# 61. Backfill engine

P9 must support targeted backfills:

```text
channel
video
track
release
metric family
period
```

Backfill must be:

```text
idempotent
rate-limited
reproducible
auditable
```

Never overwrite source payloads without recording the new version.

---

# 62. Recalculation engine

When source data changes, derived metrics must be recalculated.

Use dependency metadata such as:

```text
metric A depends on raw metrics X,Y
metric B depends on A,Z
```

A corrected revenue statement should trigger:

```text
revenue metrics
→ P&L
→ ROI
→ catalog economics
→ experiment outcomes
```

without rerunning unrelated computations.

---

# 63. Metric dependency graph

Represent derived metrics declaratively where feasible.

Example:

```text
net_revenue
 ├── youtube_revenue
 ├── dsp_revenue
 └── other_revenue

net_contribution
 ├── net_revenue
 └── allocated_cost

roi
 ├── net_revenue
 └── allocated_cost
```

Store metric definition/version.

---

# 64. Anomaly detection

Detect anomalies at multiple layers.

### Source anomalies

```text
missing report
schema change
unexpected row count

```

### Metric anomalies

```text
10x view spike
negative revenue
impossible subscriber count
sudden zero for all videos
```

### Business anomalies

```text
revenue collapse
cost spike
catalog concentration spike
```

Anomalies are flags, not automatic deletion triggers.

---

# 65. Anomaly methods

Support simple deterministic methods first:

```text
absolute threshold
z-score
MAD
IQR
rolling median deviation
```

More advanced models may be added later.

Do not introduce a heavy ML anomaly model unless it improves actual decision quality.

---

# 66. Forecasting primitives

P9 may produce forecasts, but they must be labeled:

```text
FORECAST
```

Initial forecasts may include:

```text
7-day views
28-day views
monthly revenue
catalog revenue
cash receipt forecast
```

Store:

```text
forecast_model
forecast_version
forecast_created_at
prediction_interval
```

---

# 67. Forecast methods

Support simple baseline methods first:

```text
naive last-period
moving average
exponential smoothing
cohort curve
```

Do not deploy complex forecasting models before enough data exists.

Model selection should be based on out-of-sample error.

---

# 68. Forecast evaluation

Track:

```text
MAE
MAPE where valid
sMAPE
WAPE
coverage of prediction intervals
```

Do not use MAPE when denominators can be near zero without handling that explicitly.

---

# 69. Cohort model

Create cohorts by:

```text
publication week
publication month
content arm
brand
channel
release month
country
```

Cohort tables should support:

```text
age_since_publish
cumulative_views
cumulative_watch_time
cumulative_revenue
cumulative_subscribers
```

---

# 70. Cohort curve model

For videos:

```text
age_day 0 → views
age_day 1 → views
...
age_day 90 → views
```

For tracks:

```text
release_day 0 → streams
...
release_day 90 → streams
```

This supports apples-to-apples comparison across launch dates.

---

# 71. Catalog decay / longevity

Track whether tracks:

```text
accelerate
plateau
decay
revive
```

Useful derived features:

```text
peak_day
half_life_estimate
revival_count
30d_to_90d retention
```

Do not infer causal reasons for revival without additional evidence.

---

# 72. Reuse value

Track when a song appears in multiple assets.

Compute:

```text
placements_count
platform_count
video_count
short_count
playlist_count
revenue_per_placement
```

This helps P10 assess catalog reuse efficiency.

---

# 73. Content lifecycle

Every video/track should move through:

```text
PUBLISHED
EARLY_SIGNAL
MATURE
LONG_TAIL
REVIVED
ARCHIVED
```

Transitions should be metric-driven/configurable.

---

# 74. Audience segmentation

Where data is legitimately available, support segments such as:

```text
country
traffic_source
device
subscribed_status
playback_context
```

Do not infer sensitive personal attributes.

Do not create user-level surveillance datasets.

Use aggregate analytics.

---

# 75. Geography economics

For each country where sufficient data exists:

```text
views
watch_time
revenue
revenue_per_1000_views
subscriber_conversion
```

Use minimum sample thresholds before ranking tiny geographies.

---

# 76. Audience quality proxies

P9 may expose descriptive audience-quality proxies:

```text
watch_time_per_view
subscriber_conversion
repeat_view patterns where available
playlist_save rate
long-session behavior
```

These are proxies, not psychological or demographic truth.

---

# 77. Revenue quality

Separate:

```text
advertising revenue
streaming royalty revenue
subscription/platform revenue where identifiable
other monetization
```

Do not combine fundamentally different revenue sources into one metric without retaining the component breakdown.

---

# 78. Margin calculations

At minimum:

```text
gross_revenue
variable_cost
contribution_margin
allocated_overhead
operating_contribution
```

The default business-health metric should be contribution economics, not gross views.

---

# 79. Cost per successful asset

Define a configurable success criterion.

Examples:

```text
video reached 10k views
track reached 10k streams
asset generated >₩X contribution
```

Then calculate:

```text
cost_per_successful_asset
```

Do not hard-code one success threshold into core accounting.

---

# 80. Return-on-compute

Useful metric for local automation:

```text
revenue / GPU-hour
contribution / GPU-hour
revenue / generation-credit
```

Also calculate:

```text
expected_value / generation_cost
```

but label forecasts separately from realized values.

---

# 81. Content opportunity realization

P1/P2 predicted an opportunity.

P9 should later compare:

```text
predicted_market_score
vs
actual_performance
```

This allows model calibration.

Fields:

```text
predicted_at
prediction_version
predicted_score
actual_outcome
outcome_window
prediction_error
```

P9 stores the evaluation; P10 may use it to improve decisions.

---

# 82. Prediction error metrics

For opportunity-model calibration calculate:

```text
rank correlation
MAE
Brier-style calibration if a probabilistic output exists
top-decile lift
regret against observed outcomes
```

Do not treat the P1 market score as a probability unless the underlying model explicitly supports that interpretation.

---

# 83. Experiment metadata integrity

P9 must verify that observed published content actually matches its experiment definition.

Examples:

```text
planned duration = 60m
actual duration = 43m
```

Flag:

```text
SPEC_DEVIATION
```

or:

```text
TREATMENT_MUTATION
```

Do not silently classify the result as the planned treatment.

---

# 84. Treatment fingerprint comparison

Compare:

```text
planned_content_genome
actual_content_genome
planned_thumbnail_variant
actual_thumbnail_variant
planned_title_archetype
actual_title
```

Produce:

```text
fidelity_score
changed_dimensions
severity
```

This helps distinguish a failed experiment from a failed implementation.

---

# 85. Experiment eligibility

Before including a result in P10 learning, evaluate:

```text
publication_success
measurement_completeness
experiment_fidelity
data_freshness
contamination
policy events
rights events
```

Return:

```text
ELIGIBLE
PARTIALLY_ELIGIBLE
INELIGIBLE
```

with reasons.

---

# 86. P10 feature view

Expose a P10-optimized feature record such as:

```json
{
  "experiment_id": "exp_123",
  "arm_id": "arm_rnb_001",
  "unit_id": "video_123",
  "window": "7d",
  "maturity": "MATURE",
  "metrics": {
    "views": 85000,
    "watch_time_hours": 612.4,
    "subscriber_conversion": 0.0021,
    "revenue": 182.4,
    "net_contribution": 141.2
  },
  "quality": {
    "data_completeness": 0.98,
    "experiment_fidelity": 0.96,
    "contamination": false
  },
  "uncertainty": {
    "revenue_status": "ESTIMATED",
    "confidence": 0.74
  }
}
```

Exact field shape may evolve, but semantics must remain explicit.

---

# 87. P10 should not query raw tables

P10 should consume a stable analytics feature API or materialized views.

It should not directly query:

```text
raw YouTube CSV tables
raw distributor statements
raw API payloads
```

This keeps downstream policy logic decoupled from source schemas.

---

# 88. Analytics API

Implement a read-oriented API.

### `GET /analytics/video/{video_id}`

Returns:

```text
current metrics
7d/28d/lifetime windows
revenue
cost
profitability
experiment linkage
quality state
```

### `GET /analytics/track/{track_id}`

Returns:

```text
streams by store
revenue
placements
cost
catalog metrics
```

### `GET /analytics/experiment/{experiment_id}`

Returns:

```text
arms
units
windowed outcomes
quality
contamination
```

### `GET /analytics/catalog`

Returns aggregate catalog economics.

### `GET /analytics/p10/features`

Returns eligible learning features.

---

# 89. Metric API semantics

Every response must include:

```text
as_of
source_freshness
quality_status
metric_version
currency
```

For aggregated metrics include:

```text
observation_count
```

Do not expose raw estimated values as exact-looking financial truth.

---

# 90. Daily executive report

Generate:

```text
Revenue today / recent period
YouTube views
watch hours
Spotify/DSP streams
subscriber change
catalog growth
top contributing assets
costs
net contribution
anomalies
source freshness
```

Include data-quality warnings at the top.

---

# 91. Weekly experiment report

Include:

```text
experiments matured this week
new experiments with early signals
arm performance distribution
replication results
contaminated tests
ineligible tests
predicted vs actual performance
```

Do not declare final winners. P10 owns policy decisions.

---

# 92. Monthly P&L report

Include:

```text
YouTube revenue
DSP revenue
other revenue
variable costs
software
compute
distribution
contribution margin
catalog growth
cash receipts
outstanding estimated revenue
```

The report should have:

```text
accrual/performance view
cash view
```

separately.

---

# 93. Revenue reconciliation report

Show:

```text
source statement amount
normalized amount
allocated amount
received cash
unallocated revenue
reconciliation variance
```

Target zero unexplained variance.

If variance remains:

```text
RECONCILIATION_OPEN
```

---

# 94. Cost reconciliation report

Likewise show:

```text
provider bill
internal job costs
allocated costs
unallocated costs
```

A subscription with no allocation must remain visible as overhead rather than disappearing.

---

# 95. Budget tracking

P9 consumes budget configuration and reports:

```text
budget_monthly
budget_committed
budget_consumed
budget_remaining
forecasted_commitment
```

P9 should not authorize spending.

P10 or an operations layer handles any future automated spending policy.

---

# 96. ROI horizons

Provide:

```text
1d ROI
7d ROI
28d ROI
90d ROI
lifetime ROI
```

A short-horizon zero-revenue result does not imply lifetime failure.

Use `NOT_READY` when a horizon has not matured.

---

# 97. Payback period

For assets with measurable costs and ongoing revenue, estimate:

```text
payback_days
```

If cumulative revenue has not recovered allocated cost:

```text
payback_status = NOT_REPAID
```

Do not report infinite payback as a numeric duration.

---

# 98. Lifetime value

Track:

```text
realized_lifetime_value
forecast_lifetime_value
```

Keep them separate.

Forecast LTV should include:

```text
model
version
confidence / interval
```

---

# 99. Catalog half-life

For tracks/videos calculate a configurable half-life estimate based on cumulative activity curves.

This is an analytical summary, not a physical-law parameter.

If insufficient data exists:

```text
HALF_LIFE_UNAVAILABLE
```

---

# 100. Data lineage

Every high-level number must be traceable.

Example:

```text
Monthly contribution
 ↓
Revenue records
 ↓
Distributor statement
 ↓
Source file hash
```

and:

```text
ROI
 ↓
Revenue
 ↓
Cost allocations
 ↓
Generation job
 ↓
P4 artifact
```

---

# 101. Audit ledger

Create a P9 analytics event ledger:

```text
INGESTED
NORMALIZED
REVISED
RECONCILED
ALLOCATED
RECALCULATED
FORECASTED
PUBLISHED_TO_REPORT
```

All events need:

```text
timestamp
actor
job_id
source
version
```

---

# 102. Idempotency

All ingestion jobs must be safe to rerun.

Idempotency key examples:

```text
youtube + report_type + report_date + channel_id
```

or:

```text
distributor + statement_id + statement_version
```

A rerun must not double-count revenue or views.

---

# 103. Concurrency

Prevent concurrent jobs from producing competing final states for the same logical partition.

Possible partition key:

```text
source
entity
period
metric_family
```

Use database constraints or distributed locks as appropriate.

---

# 104. Transaction strategy

Ingestion of one source report should be transactional at the logical batch level:

```text
validate
→ stage
→ normalize
→ commit
```

Do not partially expose half-parsed revenue statements as if complete.

---

# 105. Staging tables

For file-based reports use:

```text
raw_file
staging_rows
normalized_facts
```

Only normalized facts become visible as current data after validation succeeds.

---

# 106. Schema-change detection

If a source report changes headers/types:

```text
SCHEMA_CHANGE_DETECTED
```

Do not silently drop unknown columns.

Persist the raw file and halt normalization for the affected report type until the parser can safely adapt.

---

# 107. Parser versioning

Every parser run stores:

```text
provider
report_type
parser_version
schema_version
```

Historical data remains reconstructable.

---

# 108. Metric versioning

Derived metric definitions can change.

Therefore store:

```text
metric_definition_version
```

Example:

```text
net_contribution_v1
net_contribution_v2
```

Do not redefine a metric silently while retaining the same version string.

---

# 109. Reproducibility

Given:

```text
same source facts
same allocation rules
same metric version
same FX source/method
```

P9 must produce the same derived economics.

If current FX is intentionally used instead of historical FX, that methodology must be explicit.

---

# 110. FX policy

Configuration must specify whether financial reports use:

```text
source-reported exchange rate
transaction-date rate
statement-period average
receipt-date rate
fixed management rate
```

Do not mix methodologies silently.

---

# 111. Estimated YouTube revenue policy

YouTube estimated revenue should be treated as:

```text
platform_estimate
```

not final accounting revenue.

If a finalized payout statement becomes available, maintain both figures and a reconciliation relationship.

---

# 112. Revenue confidence

Suggested states:

```text
FINALIZED
REPORTED
ESTIMATED
MODELLED
PROVISIONAL
```

A dashboard can display a combined figure, but API responses must retain the component statuses.

---

# 113. Data aggregation rules

When aggregating:

```text
sum additive metrics
```

Examples:

```text
views
watch_time_minutes
revenue
```

For ratios:

```text
do not average ratios blindly
```

Correct example:

```text
aggregate_like_rate
= total_likes / total_views
```

not:

```text
mean(video_like_rate)
```

unless explicitly named as an unweighted mean.

---

# 114. Revenue-per-view calculation

Correct:

```text
revenue_per_view
= total_revenue / total_views
```

Not:

```text
mean(video_revenue/video_views)
```

unless the metric is explicitly called `mean_video_revenue_per_view`.

---

# 115. Time-zone policy

Store all timestamps in UTC.

Reports may retain source timezone metadata.

Business reporting should support:

```text
UTC
Asia/Seoul
brand-configured timezone
```

Daily bucketing must be deterministic and documented.

---

# 116. Calendar vs. rolling windows

Support both:

```text
calendar month
rolling 30d
rolling 7d
age-since-publish 7d
```

Never compare them without labeling the window type.

---

# 117. Revenue period vs. content period

For each revenue record support:

```text
content_period
reporting_period
recognition_period
cash_period
```

Where the source cannot distinguish them, preserve the available period and mark the others unknown.

---

# 118. Cost amortization

Annual/monthly subscriptions may be amortized for internal P&L.

Example:

```text
annual distribution subscription
→ monthly allocation
```

Store:

```text
original_cost
amortization_rule
amortization_period
```

Do not alter the actual payment ledger.

---

# 119. Shared overhead

Support overhead categories:

```text
software
infrastructure
monitoring
storage
administration
```

Default allocation:

```text
unallocated_overhead
```

until a declared allocation policy exists.

Avoid arbitrary per-track assignment.

---

# 120. Contribution margin views

Provide multiple margin views:

```text
asset_direct_margin
asset_allocated_margin
brand_margin
channel_margin
company_margin
```

This prevents a profitable video from appearing loss-making solely due to a questionable overhead allocation.

---

# 121. Dashboard requirements

## Command Center

Show:

```text
monthly revenue
monthly contribution
YT views
watch time
DSP streams
subscribers
catalog tracks
active releases
```

## Performance

Show:

```text
top videos
slowest videos
top tracks
long-tail tracks
traffic source mix
country mix
```

## Economics

Show:

```text
revenue
cost
margin
ROI
payback
revenue concentration
```

## Experiments

Show:

```text
active experiments
mature experiments
data-quality states
predicted vs actual
```

## Data Health

Show:

```text
source freshness
completeness
schema alerts
reconciliation issues
```

---

# 122. P10 dashboard

Create a decision-facing view that contains only P10-ready data:

```text
arm
experiment
age
maturity
primary metric
secondary metrics
uncertainty
fidelity
contamination
predicted expectation
actual outcome
```

Do not embed direct policy recommendations in P9 unless explicitly requested by the architecture later.

---

# 123. Alerts

Alert on:

```text
analytics API failure
report missing
revenue statement missing
data completeness below threshold
schema change
identity conflict
reconciliation variance
revenue anomaly
cost anomaly
```

Severity levels:

```text
INFO
WARNING
ERROR
CRITICAL
```

---

# 124. Failure handling

Classify failures:

```text
AUTH_FAILURE
RATE_LIMIT
QUOTA_EXCEEDED
NETWORK_FAILURE
PROVIDER_5XX
SCHEMA_CHANGE
PARSE_FAILURE
DATA_CONFLICT
MISSING_DATA
CORRUPT_FILE
```

Retry only transient failures.

Quarantine source files that cannot be safely parsed.

---

# 125. Retry policy

Use exponential backoff with jitter for:

```text
network
provider 5xx
retryable 429
```

Do not retry indefinitely.

Use a maximum retry count and then move to a durable retry queue.

---

# 126. Source quota protection

Maintain per-provider counters:

```text
requests
quota units where available
estimated cost
failures
```

Use scheduling priority:

```text
recent experiments
high-value data
reconciliation
historical backfill
```

Low-priority backfills should yield to fresh operational data.

---

# 127. Security

Protect:

```text
OAuth tokens
revenue files
distributor credentials
financial statements
internal performance data
```

Never put secrets into:

```text
logs
LLM prompts
dashboard exports
Git
```

---

# 128. Privacy

Use aggregate analytics where possible.

Do not store unnecessary user-level identifiers.

Apply provider privacy requirements.

Do not attempt to reconstruct anonymized audience records.

---

# 129. LLM boundary

LLMs may help with:

```text
report categorization
unstructured statement parsing
anomaly explanation draft
natural-language report summarization
metric-definition documentation
```

LLMs must not be the source of truth for:

```text
revenue arithmetic
cost arithmetic
metric definitions
FX conversion
identity mapping when deterministic IDs exist
experiment assignment
final accounting reconciliation
```

Critical financial calculations must be deterministic.

---

# 130. Local-model usage

Use local models for cheap batch tasks where practical:

```text
statement classification
metadata cleanup
report narrative drafts
anomaly description
```

Use stronger models only for genuinely ambiguous cases.

This reduces external AI cost without putting core accounting logic inside a black-box model.

---

# 131. Natural-language report generation

Narrative reports should be generated from a structured metrics object.

Pipeline:

```text
analytics facts
→ validated summary JSON
→ LLM narrative
→ factual consistency checker
→ report
```

Never let the LLM calculate the numbers independently.

---

# 132. Narrative fact checker

Before publishing an AI-generated report:

1. Extract every numeric claim.
2. Match it to source metrics.
3. Verify arithmetic/rounding.
4. Reject unsupported claims.

Example:

```text
Narrative says +31%
source says +24%
→ FAIL
```

---

# 133. Business glossary

Create a versioned glossary for terms:

```text
view
watch hour
stream
estimated revenue
net revenue
contribution
ROI
margin
lifetime value
forecast
maturity
confidence
```

Every dashboard and report must use these definitions consistently.

---

# 134. Report rounding

Store full-precision calculations internally.

Round only for presentation.

Example:

```text
internal: 1234.567891
report: ₩1,234.57
```

Do not perform subsequent calculations using rounded display values.

---

# 135. Metric anti-gaming safeguards

The system should track whether a metric can be inflated without economic value.

Examples:

```text
views without watch time
clicks without retention
streams without royalty
```

No single vanity metric should become the sole economic objective.

---

# 136. Metric bundles

Define standard bundles.

### Discovery bundle

```text
views
CTR if available
audience source
watch time
```

### Quality bundle

```text
average view duration
percentage viewed
engagement
subscriber conversion
```

### Economics bundle

```text
revenue
cost
contribution
ROI
```

### Catalog bundle

```text
streams
revenue
longevity
reuse
```

---

# 137. Early-signal score

P9 may calculate a non-final early-signal composite for operational triage.

Example components:

```text
normalized early views
watch time
subscriber conversion
engagement
```

This must be named explicitly:

```text
early_signal_score
```

It must not replace P2/P10's final economic objective.

---

# 138. Mature economic score

For mature content, expose:

```text
mature_contribution_score
```

based on:

```text
contribution
revenue efficiency
catalog longevity
```

Do not convert it into an opaque 0–100 score unless P10 explicitly requests one.

---

# 139. Data-quality weighted learning

P9 should expose:

```text
learning_weight
```

based on:

```text
completeness
freshness
experiment fidelity
attribution confidence
revenue certainty
```

This lets P10 choose how strongly to learn from noisy observations.

The precise weight formula belongs to P10; P9 should expose the components and an optional documented baseline weight.

---

# 140. Recommendation feedback

When P10 later makes a policy decision, P9 should be able to store:

```text
prediction
expected outcome
observed outcome
policy decision
result
```

This closes the loop for calibration without putting policy logic inside P9.

---

# 141. API pagination and performance

All collection APIs must support:

```text
cursor pagination
limit
filters
sort
```

Avoid unbounded result sets.

---

# 142. Time-series query optimization

Index at minimum:

```text
entity_id + metric_name + reported_for_start
source + reported_for_start
experiment_id + unit_id
track_id + store + reporting_period
video_id + reporting_period
```

The exact indexing strategy may evolve after profiling.

---

# 143. Materialized views

Create materialized views or caches for hot queries:

```text
daily_channel_summary
daily_video_summary
track_catalog_summary
monthly_financial_summary
experiment_outcome_summary
```

Refresh incrementally where feasible.

---

# 144. Object storage

Retain source files in immutable object storage:

```text
raw/youtube/report_type/date/file.csv
raw/distributor/provider/statement_id/file.ext
```

Use content hashes.

---

# 145. Compression / retention

Large analytics datasets should have configurable retention.

Recommended:

```text
raw source files → long-term retention
normalized daily metrics → long-term
intraday high-frequency metrics → configurable shorter retention
```

Do not delete historical data needed to reproduce financial statements without explicit policy.

---

# 146. Backup

Back up:

```text
database
object storage metadata
financial ledgers
metric definitions
allocation policies
```

Test restoration.

---

# 147. Disaster recovery

Define:

```text
RPO
RTO
backup cadence
restore procedure
```

At minimum, the system must be able to restore:

```text
financial ledger
catalog identity mappings
P10 feature history
```

without reconstructing them from external services.

---

# 148. Migration strategy

All schema changes use migrations.

Never rename metric semantics without versioning.

Historical reports must remain interpretable after upgrades.

---

# 149. Implementation repository structure

Suggested structure:

```text
src/
  analytics/
    __init__.py
    config.py
    domain/
      metrics.py
      revenue.py
      costs.py
      attribution.py
      experiments.py
      enums.py
    connectors/
      base.py
      youtube_analytics.py
      youtube_reporting.py
      distributor.py
    ingestion/
      scheduler.py
      runners.py
      staging.py
      reconciliation.py
    normalization/
      youtube.py
      distributor.py
      currency.py
      identity.py
    features/
      youtube.py
      retention.py
      revenue.py
      costs.py
      catalog.py
      experiments.py
      cohorts.py
      forecasting.py
    attribution/
      engine.py
      policies.py
      confidence.py
    economics/
      pnl.py
      roi.py
      margins.py
      payback.py
      concentration.py
    quality/
      completeness.py
      freshness.py
      anomaly.py
      contamination.py
    forecasting/
      baselines.py
      evaluation.py
    reports/
      daily.py
      weekly.py
      monthly.py
    api/
      routes.py
      schemas.py
    jobs/
      daily.py
      backfill.py
      reconciliation.py
      reports.py
    observability/
      logging.py
      metrics.py

tests/
  unit/
  integration/
  contract/
  fixtures/
  regression/
  recovery/

migrations/

warehouse/

docs/
  P9_analytics_economics_engine_implementation_plan.md
```

Adapt to the existing repository's conventions where a better structure already exists.

---

# 150. Domain objects

At minimum define:

```text
MetricObservation
MetricFact
RevenueRecord
CostRecord
AttributionRecord
ExperimentOutcome
CohortObservation
ForecastRecord
DataQualityRecord
ReconciliationIssue
```

Do not overload one class with accounting, analytics and raw-source behavior.

---

# 151. Revenue record schema

Suggested contract:

```json
{
  "revenue_id": "rev_001",
  "source": "distributor_a",
  "provider_record_id": "stmtrow_123",
  "period_start": "2026-07-01",
  "period_end": "2026-07-31",
  "currency": "USD",
  "gross_amount": 102.31,
  "net_amount": 97.40,
  "status": "REPORTED",
  "store": "spotify",
  "territory": "US",
  "release_id": "rel_123",
  "track_id": "trk_456",
  "isrc": "US-XXX-...",
  "source_statement_id": "statement_2026_07_abc",
  "statement_version": 1
}
```

Fields are illustrative; retain provider-specific fields separately.

---

# 152. Cost record schema

```json
{
  "cost_id": "cost_001",
  "cost_type": "music_generation",
  "provider": "suno",
  "amount": 12.0,
  "currency": "USD",
  "incurred_at": "2026-09-20T09:00:00Z",
  "allocation_scope": "generation_job",
  "allocation_target_id": "job_123",
  "source_reference": "usage_record_123",
  "cost_version": 1
}
```

---

# 153. Experiment outcome schema

```json
{
  "experiment_id": "exp_001",
  "unit_id": "video_123",
  "arm_id": "arm_rnb_001",
  "window": "7d",
  "maturity": "MATURE",
  "primary_metric": "net_contribution",
  "value": 143.21,
  "measurement_status": "ELIGIBLE",
  "confidence": 0.76,
  "contamination": false,
  "fidelity_score": 0.97,
  "metric_version": "revenue_v2"
}
```

---

# 154. Reconciliation record

```json
{
  "reconciliation_id": "rec_001",
  "entity_type": "track",
  "entity_id": "trk_123",
  "source_a": "p8_delivery",
  "source_b": "distributor_statement",
  "status": "MATCHED",
  "variance": 0,
  "checked_at": "2026-09-20T06:00:00Z"
}
```

---

# 155. P9 → P10 contract

P10 must be able to request a stable representation of:

```text
market prediction
experiment assignment
observed outcome
maturity
quality
uncertainty
cost
revenue
```

Minimum contract:

```json
{
  "unit_id": "video_123",
  "experiment_id": "exp_001",
  "arm_id": "arm_rnb_001",
  "content_genome_id": "genome_001",
  "observation_window": "7d",
  "maturity": "MATURE",
  "primary_outcome": {
    "metric": "net_contribution",
    "value": 143.21,
    "status": "REPORTED"
  },
  "secondary_outcomes": {
    "views": 85000,
    "watch_time_hours": 612.4,
    "subscribers_gained": 179,
    "spotify_streams": 1300
  },
  "quality": {
    "data_completeness": 0.98,
    "fidelity": 0.96,
    "contamination": false,
    "attribution_confidence": 1.0
  }
}
```

---

# 156. P9 → P10 semantics

P9 provides facts and documented derived metrics.

P10 decides:

```text
how much to explore
which arm to allocate
how to update priors
when to retire a concept
```

Do not duplicate these decisions in P9.

---

# 157. P9 → operator semantic contract

The operator must be able to answer:

```text
How much money did we make?
How much did we spend?
Where did it come from?
Which assets are contributing?
How certain are the numbers?
What data is missing?
Which content is mature enough to evaluate?
```

---

# 158. No unsupported rankings

P9 can sort by any selected metric:

```text
highest revenue
highest views
highest contribution
```

It must not label content "best" without stating the metric and window.

Example:

```text
Top by 28-day contribution
```

not:

```text
Best videos
```

---

# 159. Data status banner

Every dashboard should show:

```text
Data as of: YYYY-MM-DD HH:MM UTC
YouTube freshness: ...
DSP statement freshness: ...
Revenue status: estimated/reported/final
```

This avoids false real-time impressions.

---

# 160. Test strategy overview

Testing must cover:

```text
unit
property
integration
contract
reconciliation
financial arithmetic
backfill
recovery
load
security
```

Financial calculations require exhaustive edge-case tests.

---

# 161. Unit tests — arithmetic

Test:

```text
sum revenue
sum cost
net contribution
ROI
margin
revenue per 1k views
revenue per stream
payback
FX conversion
```

including:

```text
zero
negative correction
null
mixed currencies
very small values
very large values
```

---

# 162. Unit tests — aggregation

Verify:

```text
sum additive metrics
weighted ratio aggregation
window aggregation
cohort aggregation
```

Test that naive averaging of ratios does not accidentally enter production calculations.

---

# 163. Unit tests — attribution

Cases:

```text
explicit ISRC match
video-to-track mapping
multi-track playlist
uncertain fuzzy match
duplicate track IDs
missing mapping
```

---

# 164. Unit tests — maturity

Verify:

```text
future window = NOT_READY
7d before maturity = EARLY_SIGNAL
mature windows = MATURE
```

---

# 165. Unit tests — revenue statuses

Verify that:

```text
ESTIMATED
REPORTED
FINALIZED
MODELLED
```

remain distinct in APIs and aggregation queries.

---

# 166. Property tests

Useful invariants:

```text
revenue aggregation is associative for additive records
allocation totals equal source cost within configured rounding
ROI never changes when display rounding changes
metric values never become NaN/Inf
negative costs are rejected unless explicitly a correction/credit
```

---

# 167. Contract tests — YouTube

Mock current provider schemas for:

```text
analytics query
report job
report retrieval
monetary report
traffic source report
geography report
```

The adapter must fail loudly when expected fields disappear.

---

# 168. Contract tests — distributor

Fixtures should represent:

```text
single release
album
multi-territory
multiple stores
royalty statement
corrected statement
missing ISRC
```

---

# 169. Integration test — complete daily cycle

Fixture:

```text
5 videos
8 tracks
2 playlists
3 experiments
1 distributor statement
1 corrected statement
2 countries
```

Run:

```text
ingest
→ normalize
→ reconcile
→ calculate economics
→ produce reports
```

Expected no duplicate revenue and correct revised totals.

---

# 170. Recovery tests

Kill the process at:

```text
file download
CSV parse
DB staging
normalization
reconciliation
P&L calculation
report generation
```

Restart.

The final result must be identical to a clean run.

---

# 171. Backfill tests

Import the same report twice.

Expected:

```text
no double-counting
```

Import a corrected version.

Expected:

```text
historical value retained
current value updated
P&L recalculated
reconciliation updated
```

---

# 172. Financial safety tests

Test:

```text
currency mismatch
negative royalty correction
duplicate statement
partial payment
unreceived revenue
```

No silent financial corruption is acceptable.

---

# 173. Data-quality tests

Inject:

```text
missing dates
negative views
future timestamps
impossible durations
schema changes
duplicate IDs
```

Expected quarantine/failure behavior.

---

# 174. Anomaly tests

Synthetic scenarios:

```text
10x view spike
100x revenue spike
sudden all-zero YouTube metrics
massive cost jump
```

Verify alerts trigger without deleting data.

---

# 175. Privacy tests

Ensure:

```text
OAuth tokens absent from logs
financial files access-controlled
no unnecessary raw user IDs
```

---

# 176. Performance targets

Initial targets should be configurable.

Suggested starting goals:

```text
daily analytics refresh < 15 min for current catalog
monthly financial recompute < 30 min
10-year historical query < 2 sec for common aggregate views
full P9 rebuild from normalized facts < 60 min for MVP-scale dataset
```

Do not prematurely optimize before measuring actual scale.

---

# 177. Observability

Metrics:

```text
ingestion_runs_total
records_ingested_total
records_rejected_total
source_freshness_seconds
source_completeness_ratio
reconciliation_open_total
revenue_records_total
cost_records_total
p10_features_eligible_total
forecast_error
```

---

# 178. Logs

Each job log should contain:

```text
run_id
source
entity_scope
period
provider
parser_version
metric_version
rows_received
rows_valid
rows_rejected
revenue_amount_processed
cost_amount_processed
```

Never log secrets.

---

# 179. Traceability

A dashboard number should be drillable:

```text
monthly revenue
 ↓
source breakdown
 ↓
statement
 ↓
release
 ↓
track
 ↓
video
```

This is a core product requirement, not an optional debug feature.

---

# 180. Operator override

Some mappings may require human correction.

Example:

```text
Track mapping uncertain.
```

Human can select canonical track.

Store:

```text
override_actor
override_timestamp
reason
previous_value
new_value
```

Never overwrite the underlying source record.

---

# 181. Human exception queue

Route to human only:

```text
identity conflict
financial statement conflict
unresolved provider schema
high-value ambiguous attribution
security issue
```

Normal analytics should remain fully autonomous.

---

# 182. AI report validation

If an LLM creates an executive summary, require:

```text
numeric claim verification
source citation/reference
no unsupported causal language
```

Example prohibited narrative transformation:

```text
Observed:
R&B arm had +22% median views.

Unverified narration:
R&B causes audiences to stay longer.
```

The second claim is unsupported.

---

# 183. Market-model feedback

P9 must store links between:

```text
P1 market opportunity score
P2 allocation decision
P3 experiment
actual observed performance
```

This enables model calibration.

---

# 184. Decision-quality dataset

Create a dataset with:

```text
prediction
allocation
cost
observed_result
prediction_error
```

P10 can later analyze whether decisions were systematically biased.

---

# 185. Opportunity-to-revenue funnel

Compute:

```text
market opportunities discovered
→ experiments launched
→ successful content produced
→ published
→ monetized
→ profitable
```

At each stage calculate conversion.

This allows the business to identify whether the bottleneck is:

```text
research
production
packaging
distribution
audience
monetization
```

---

# 186. End-to-end funnel metrics

Required funnel stages:

```text
opportunity
production directive
candidate generated
candidate passed QA
playlist built
video published
video monetized
track distributed
streamed
revenue recognized
positive contribution
```

Track conversion and drop-off by phase.

---

# 187. Failure economics

Record how much cost is consumed by failed work.

Examples:

```text
rejected music generations
failed renders
failed uploads
rejected releases
```

Calculate:

```text
failure_cost
failure_rate
cost_per_successful_publish
```

This allows P10/P11 to optimize operational efficiency.

---

# 188. Waste categories

At minimum:

```text
GENERATION_WASTE
QA_WASTE
RENDER_WASTE
PUBLISH_WASTE
DISTRIBUTION_WASTE
DATA_WASTE
```

Do not treat unavoidable experimentation as waste without distinguishing it from preventable failure.

---

# 189. Catalog velocity

Track:

```text
new tracks/week
new releases/week
new videos/week
retired assets/week
```

Compare catalog growth to revenue growth.

---

# 190. Catalog efficiency

Useful ratios:

```text
monthly revenue / active track
monthly revenue / active video
monthly contribution / generation hour
monthly streams / catalog track
```

These are descriptive efficiency metrics, not universal industry benchmarks.

---

# 191. Channel economics

For each channel:

```text
revenue
cost
margin
views
watch time
subscribers
catalog contribution
```

Multi-channel future support should not require separate database designs.

---

# 192. Brand economics

For each brand:

```text
revenue
cost
contribution
catalog size
audience size
cross-platform streams
```

This supports eventual portfolio decisions.

---

# 193. Country economics

For each country with enough data:

```text
views
watch time
revenue
revenue per 1k views
streams
royalty
```

Use minimum sample thresholds to avoid overreacting to tiny countries.

---

# 194. Platform economics

Compare:

```text
YouTube
Spotify
Apple Music
other DSPs
```

Metrics:

```text
revenue
cost
margin
revenue per audience unit
```

Do not compare platform numbers without acknowledging different accounting/reporting semantics.

---

# 195. Release economics

For each release:

```text
number of tracks
release cost
distribution status
streams
revenue
contribution
```

---

# 196. Track economics with reuse

A track appearing in 10 videos should have:

```text
one creation-cost record
multiple placement records
```

Attribution should not duplicate generation cost ten times.

---

# 197. Shared-track revenue model

If a track appears in multiple mixes:

```text
video revenue = observed
track attributed revenue = modelled if needed
```

Use a configurable attribution policy.

Do not retroactively change the revenue ledger merely because the attribution policy changed; version the modeled allocation.

---

# 198. Attribution-version history

For each modeled allocation store:

```text
attribution_policy_version
computed_at
source_revenue_id
allocated_entities
allocation_weights
```

A new policy creates a new allocation version.

---

# 199. No retroactive data destruction

If P9 changes a formula:

```text
old result remains reproducible
new result becomes current
```

Never destroy the prior calculation solely because it is inconvenient.

---

# 200. Reporting export

Support export:

```text
CSV
JSON
Parquet
```

for:

```text
metrics
revenue
costs
experiments
catalog
```

Include schema/version metadata with exports.

---

# 201. Report templates

Maintain versioned templates:

```text
daily_v1
weekly_v1
monthly_v1
p10_snapshot_v1
```

A report template change should not alter the underlying metric definitions.

---

# 202. Auditability requirement

Any number shown to the operator as financial or experiment-critical must be explainable by a chain of records.

Example:

```text
₩3,200,000 monthly contribution
```

must be traceable to:

```text
revenue records
minus
cost records
under
metric/allocation versions
```

---

# 203. Implementation phases

## P9.1 — Domain contracts

Define entities, enums, metric definitions and statuses.

## P9.2 — Database schema

Implement metric facts, revenue, cost, attribution, experiments, quality and reconciliation tables.

## P9.3 — YouTube Analytics connector

Implement authenticated queries and normalized ingestion.

## P9.4 — YouTube Reporting connector

Implement report-type discovery, jobs, downloads and revised-data ingestion.

## P9.5 — Distributor report connector

Implement statement parser abstraction and one real provider adapter.

## P9.6 — Identity / reconciliation

Map P7/P8 objects to analytics records.

## P9.7 — Feature engine

Implement windows, cohorts, quality, anomalies and economics.

## P9.8 — Attribution engine

Implement explicit mapping and versioned modelled allocation.

## P9.9 — P&L / economics

Implement revenue, cost, margin, ROI and payback views.

## P9.10 — P10 feature API

Expose clean learning-ready outcomes.

## P9.11 — Dashboards/reports

Build command center and data-health reporting.

## P9.12 — Testing / recovery

Complete end-to-end, correction, backfill and failure tests.

---

# 204. Rollout plan

Use staged deployment.

### Stage A — fixture mode

No real accounts.

### Stage B — read-only production

Pull metrics but do not use them for automated decisions.

### Stage C — reconciled production

Validate financial totals and experiment links.

### Stage D — P10 shadow mode

P10 consumes analytics features but cannot change allocation.

### Stage E — autonomous learning

After data integrity is proven, P10 may consume P9 automatically.

---

# 205. Definition of done — data ingestion

P9 is not complete until:

```text
YouTube analytics ingests successfully
YouTube report files ingest successfully where authorized
distributor statement ingests successfully
raw evidence is retained
revisions are handled
duplicates are prevented
```

---

# 206. Definition of done — economics

Must correctly calculate:

```text
revenue
cost
contribution
ROI
margin
payback
catalog economics
```

for at least:

```text
video
track
release
channel
month
```

---

# 207. Definition of done — experiment measurement

P9 must correctly answer:

```text
What experiment produced this asset?
What treatment was intended?
What actually happened?
What are the 1d/7d/28d outcomes?
Is the result mature?
Is the result contaminated?
Is it eligible for learning?
```

---

# 208. Definition of done — financial reconciliation

For a test dataset with:

```text
3 YouTube revenue records
5 DSP royalty records
10 cost records
1 corrected DSP statement
```

P9 must produce:

```text
correct current revenue
correct corrected historical value
no duplicate revenue
correct P&L
visible reconciliation trail
```

---

# 209. Definition of done — P10 interface

P10 can retrieve:

```text
experiment outcome
maturity
quality
uncertainty
cost
revenue
predicted-vs-actual
```

with one stable API contract.

---

# 210. What the implementation AI may choose

The implementation AI may choose:

- SQL vs. analytical warehouse optimizations;
- ORM/query-builder;
- synchronous vs. asynchronous ingestion implementation;
- exact Python libraries;
- exact object-store structure;
- materialized-view strategy;
- exact anomaly detection implementation;
- exact forecasting baseline implementation;
- dashboard technology;
- observability stack;
- internal class decomposition;
- queue implementation, if it preserves durable semantics.

---

# 211. What the implementation AI must not decide silently

Do not silently change:

- metric semantics;
- accounting periods;
- currency policy;
- revenue status semantics;
- source-vs-derived distinction;
- attribution hierarchy;
- experiment eligibility semantics;
- historical immutability;
- P9/P10 ownership boundaries;
- null/zero/not-ready semantics;
- financial calculation formulas;
- data-quality handling;
- provider-source provenance requirements.

If an implementation detail materially changes these behaviors, record a decision and version the affected contract.

---

# 212. Acceptance criteria

## AC-01 — Reproducible ingestion

The same source report imported twice does not double-count.

## AC-02 — Corrections

A corrected source report updates current truth while preserving prior evidence.

## AC-03 — YouTube data

Authenticated YouTube metrics can be ingested into canonical facts.

## AC-04 — DSP revenue

At least one authorized distributor statement can be ingested and reconciled to tracks/releases.

## AC-05 — Identity

Track/video/release identity conflicts are surfaced rather than silently merged.

## AC-06 — P&L

Video/track/release/channel/month economics are correct under test fixtures.

## AC-07 — Experiment linkage

Each measurable asset can be traced to its experiment context when available.

## AC-08 — Windowing

1d/7d/28d/lifetime windows are correctly calculated and `NOT_READY` is respected.

## AC-09 — Quality

Incomplete, stale, revised and anomalous data remain labeled.

## AC-10 — Attribution

Observed vs modelled revenue are separated and versioned.

## AC-11 — P10 API

P10 can retrieve clean outcome records without touching raw provider schemas.

## AC-12 — Reports

Daily, weekly and monthly reports can be generated from the same canonical facts.

## AC-13 — Reconciliation

Source-to-platform and platform-to-financial reconciliation reports show all unresolved variance.

## AC-14 — Security

No tokens or secrets appear in logs or exports.

## AC-15 — Recovery

Interrupted ingestion resumes without duplicate economic records.

## AC-16 — Backfill

Historical reprocessing is deterministic and auditable.

## AC-17 — Tests

All mandatory arithmetic, ingestion, reconciliation and recovery tests pass.

---

# 213. Final operating boundary

The final P9 boundary is:

```text
P7 / P8
   ↓
OBSERVED PLATFORM + DISTRIBUTION DATA
   ↓
P9
   ↓
NORMALIZED MEASUREMENT LEDGER
   ↓
ATTRIBUTION + ECONOMICS + EXPERIMENT OUTCOMES
   ↓
QUALITY / UNCERTAINTY / FORECASTS
   ↓
P10
   ↓
AUTONOMOUS POLICY / ALLOCATION
```

The core invariant is:

> **P9 measures reality; P10 decides what to do about it.**

That separation is mandatory because the autonomous loop is only as trustworthy as the measurement layer feeding it.

---

# 214. Practical implementation guidance

The implementation AI should prioritize a **correct, auditable measurement system over a visually impressive dashboard**.

A correct MVP with:

```text
YouTube daily metrics
DSP statement import
cost ledger
identity mapping
P&L
experiment outcome API
```

is more valuable than a sophisticated BI interface that cannot reproduce its numbers.

Build the boring accounting/measurement foundation first.

---

# 215. Suggested first end-to-end acceptance scenario

Use one real or sandbox-compatible channel with:

```text
3 published videos
3 underlying tracks
1 playlist mix
1 distributed release
1 available royalty statement
```

Run:

```text
P7 publication records
→ P8 release records
→ P9 analytics ingestion
→ P9 revenue ingestion
→ P9 cost ingestion
→ P9 attribution
→ P9 P&L
→ P9 experiment outcome
→ P10 feature API
```

Verify that every identifier can be followed across the complete chain.

---

# 216. Strategic objective

P9 should eventually make it possible to ask:

> "For the past 90 days, which combinations of content concept, genre, use case, language, video format, visual package and distribution strategy generated the highest realized contribution per unit of production cost, with enough evidence to justify learning from the result?"

The system should answer that question from stored evidence and documented calculations rather than from an LLM's intuition.

That is the purpose of P9.
