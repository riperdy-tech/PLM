# Autonomous AI Music Media Engine — Master Architecture

## Target operating model

The system should be designed as an **autonomous AI music company**, not a content-generation script.

Once infrastructure is configured, human involvement should be approximately:

**Human**

> accounts → permissions → budget ceiling → policy preferences → emergency decisions

Everything else:

**AI**

> research → strategy → creation → QA → packaging → publishing → distribution → analytics → optimization → iteration

The target is **95%+ operational automation**, while keeping hard-stop human review for payment changes, KYC, platform-policy disputes, copyright disputes, account-security events, and similarly high-risk exceptions.

---

# 0. The system architecture

```text
                    ┌─────────────────────┐
                    │     DATA WORLD      │
                    │                     │
                    │ YouTube             │
                    │ Spotify             │
                    │ Google Trends       │
                    │ Search trends       │
                    │ Charts              │
                    │ Competitor channels │
                    │ Social signals      │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ MARKET INTELLIGENCE │
                    │       ENGINE         │
                    │                     │
                    │ Demand              │
                    │ Growth              │
                    │ Saturation          │
                    │ Competition         │
                    │ Monetization        │
                    │ Cross-platform      │
                    └──────────┬──────────┘
                               │
                               ▼
                 ┌──────────────────────────┐
                 │ OPPORTUNITY / BANDIT      │
                 │        ENGINE             │
                 │                          │
                 │ Genre                    │
                 │ Mood                     │
                 │ Language                 │
                 │ Use case                 │
                 │ Vocal/instrumental       │
                 │ BPM / energy             │
                 │ Duration                 │
                 │ Visual concept           │
                 │ Title archetype          │
                 └────────────┬─────────────┘
                              │
                              ▼
                  ┌──────────────────────┐
                  │   CONTENT PLANNER    │
                  │                      │
                  │ Playlist concept     │
                  │ Track briefs         │
                  │ Track sequencing     │
                  │ Brand consistency    │
                  └──────────┬───────────┘
                             │
                             ▼
        ┌───────────────────────────────────────────┐
        │              CONTENT FACTORY             │
        │                                           │
        │ LLM → lyrics / concepts / metadata       │
        │ Music model → generation                  │
        │ Image model → artwork                     │
        │ Audio processor → mastering               │
        │ Video engine → visualizer                 │
        │ Thumbnail engine                           │
        └────────────────────┬──────────────────────┘
                             │
                             ▼
                   ┌─────────────────────┐
                   │     QA / GATE       │
                   │                     │
                   │ Audio quality       │
                   │ Similarity          │
                   │ Metadata            │
                   │ Rights              │
                   │ Policy              │
                   │ Brand consistency  │
                   └──────────┬──────────┘
                              │
                              ▼
               ┌────────────────────────────┐
               │      DISTRIBUTION          │
               │                            │
               │ YouTube                   │
               │ Spotify                   │
               │ Apple Music               │
               │ YouTube Music             │
               │ TikTok / Reels             │
               └────────────┬───────────────┘
                            │
                            ▼
                ┌──────────────────────────┐
                │    PERFORMANCE ENGINE   │
                │                          │
                │ Views                    │
                │ CTR                      │
                │ Watch time               │
                │ Retention                │
                │ Revenue                  │
                │ Subscribers              │
                │ Spotify streams          │
                │ Playlist activity        │
                └─────────────┬────────────┘
                              │
                              ▼
                 ┌─────────────────────────┐
                 │ LEARNING / OPTIMIZATION │
                 │                         │
                 │ Update priors           │
                 │ Kill weak concepts      │
                 │ Expand winners          │
                 │ Explore new concepts    │
                 │ Reallocate production   │
                 └────────────┬────────────┘
                              │
                              └──────────────► back to MARKET
```

The final feedback arrow is the critical feature: the system never stops learning.

---

# PART I — MARKET INTELLIGENCE ENGINE

## P1. Build the automated market-research layer

The intelligence layer answers:

> "What music should this business make?"

### Data sources

### YouTube

Use the YouTube Data API and YouTube Analytics/Reporting APIs where authorized. Build a competitor dataset containing approximately:

```text
channel
video
published_at
views
likes
comments
channel_subscribers
video_duration
title
description
category
language/country signals
playlist relationships
```

Derive normalized measures such as:

```text
views/day
views/subscriber
views/channel_age
likes/view
comments/view
velocity_24h
velocity_72h
velocity_7d
```

Raw view count is not sufficient. A small channel with unusually fast growth can be a more useful signal than a very large established channel with slow growth.

---

# P2. Don't just search "AI music"

The crawler should build a multidimensional music taxonomy.

```text
GENRE
×
MOOD
×
USE CASE
×
LANGUAGE
×
VOCAL MODE
×
TEMPO
×
VIDEO LENGTH
×
VISUAL WORLD
×
TITLE ARCHETYPE
```

Example arm:

```text
R&B
+
melancholic
+
late-night driving
+
English
+
female vocal
+
90 BPM
+
60 min
+
rainy Seoul
+
"2AM ..."
```

versus:

```text
deep house
+
luxury
+
hotel lounge
+
instrumental
+
118 BPM
+
90 min
+
Tokyo rooftop
+
"Luxury ..."
```

These are treated as different experimental arms.

---

# P3. Demand intelligence

Calculate an initial `MarketOpportunityScore` from:

```text
25% Demand
20% Recent Growth
15% Competitor Velocity
15% Saturation / Competition
10% Audience Quality
10% Cross-platform confirmation
 5% Production Fit
```

These weights are starting assumptions. They must later become configurable and eventually learnable.

### Demand

Estimate using multiple independent signals such as:

- YouTube search/result density
- query occurrence
- related search terms
- YouTube autosuggest where permitted
- Google Trends or equivalent trend data
- chart appearance
- music-market signals

### Growth

Compare recent and historical periods, e.g.:

```text
30-day activity
vs
90-day activity
vs
180-day activity
```

### Competition

Measure:

```text
number of active channels
median views
median views/subscriber
new uploads/week
top-channel concentration
```

The objective is to discover audience demand with tolerable competition, not simply the largest category.

---

# P4. Cross-platform confirmation

Use Spotify as a second market signal, but do not treat any single Spotify field as ground truth. Spotify's public Web API surface and fields can change, and several popularity-related fields were deprecated/removed in 2026.

Keep the Spotify adapter isolated from the model-training layer. Use permitted numeric/metadata observations as market signals rather than copying catalog audio/content into model training.

---

# P5. Build a music opportunity database

Every potential content concept becomes a structured object:

```json
{
  "arm_id": "rnb_latenight_drive_en_vocal_60",
  "genre": "rnb",
  "mood": "melancholic",
  "use_case": "late_night_drive",
  "language": "english",
  "vocals": true,
  "tempo_range": [82, 96],
  "duration": 60,
  "visual": "rainy_asian_city",
  "market_score": 82.6,
  "competition_score": 44.3,
  "growth_score": 91.2,
  "confidence": 0.64
}
```

This database becomes the AI's production opportunity queue.

---

# PART II — THE AI "A&R" ENGINE

## P6. Don't have Claude simply choose the genre

Use Bayesian experimentation / contextual bandits.

Each content concept is an `arm`.

Initial belief:

```text
R&B night drive       expected performance = 61
Deep house luxury     expected performance = 58
Rainy city pop        expected performance = 72
Jazz cafe             expected performance = 48
```

After observed performance, update the posterior and reallocate future production while retaining exploration.

A starting allocation could be:

```text
70% exploitation
20% adjacent exploration
10% radical exploration
```

The exact policy should remain configurable.

---

# P7. Learn what "success" actually means

Do not optimize purely for views.

Target:

```text
Expected Economic Value
=
Revenue
+
Subscriber Value
+
Catalog Value
+
Spotify Conversion
+
Future Discovery Value
-
Production Cost
```

Early-stage proxy:

```text
view velocity
+
watch time
+
retention
+
subscriber conversion
+
engagement
```

Later, use actual channel-level revenue and catalog economics.

---

# P8. Create a structured content genome

Each successful track should be represented by machine-readable attributes:

```text
Genre
Subgenre
Mood
Use case
BPM
Key
Energy
Vocal type
Vocal gender
Language
Lyric density
Song structure
Hook strength
Instrumentation
Production style
Duration
Visual concept
Thumbnail concept
Title archetype
```

The system learns which combinations produce successful outcomes.

---

# PART III — MUSIC GENERATION FACTORY

## P9. Music-generation abstraction layer

Do not hard-code the business around one provider. Define a provider-neutral interface such as:

```text
generate()
extend()
edit()
stems()
download()
metadata()
license()
```

Potential adapters:

```text
SunoProvider
LocalMusicProvider
FallbackMusicProvider
```

Validate commercial rights, API terms, rate limits, and automation permissions before production use.

---

# P10. Generation strategy

Generate many candidates, cheaply score them, and reserve expensive refinement for promising outputs.

Example funnel:

```text
100 raw generations
        ↓
technical QA
        ↓
60 survive
        ↓
music-quality model
        ↓
25 survive
        ↓
commercial/originality checks
        ↓
12 survive
        ↓
LLM A&R ranking
        ↓
6 final songs
```

Maintain a candidate bank so production work is not wasted.

---

# P11. AI music QA

### Technical

```text
LUFS
peak
clipping
silence
sample rate
duration
stereo integrity
```

### Musical

```text
intro quality
hook timing
structure
vocal coherence
lyric coherence
ending quality
repetition
artifact detection
```

### Similarity / rights

Reject or quarantine outputs where the system detects high-risk artist/song references, lyric similarity, uncertain sample provenance, unauthorized voice cloning, or other rights conflicts.

Do not prompt by naming living or protected artists as style targets. Use descriptive musical attributes instead.

---

# PART IV — AUTONOMOUS PLAYLIST ASSEMBLY

## P12. Playlist composer

Optimize the sequence rather than simply sorting by song score.

Consider:

```text
BPM continuity
key compatibility
energy transition
vocal density
instrument density
mood progression
listener fatigue
```

The goal is a coherent listening experience.

---

# PART V — VIDEO FACTORY

## P13. Generate the complete audiovisual package

Each playlist should automatically produce:

```text
audio master
video
thumbnail A
thumbnail B
thumbnail C
title candidates
description
chapters
tracklist
hashtags
tags
pinned comment
Shorts candidates
```

Video should use motion/procedural animation rather than repeatedly publishing nearly identical static-image videos.

---

# P14. Build an actual visual brand

Create a brand bible covering:

```text
color system
typography
thumbnail grid
character rules
camera rules
visual motifs
logo
animation rules
```

All automated visual generation must conform to the relevant brand profile.

---

# PART VI — PACKAGING / SEO

## P15. Title generation as optimization

Create title archetypes such as:

```text
[Time] + [Place] + [Mood]

2AM Seoul | Late Night R&B Mix
```

```text
[Activity] + [Genre]

Night Drive | Dreamy Asian R&B
```

```text
[Scene]

Rainy Tokyo | 1 Hour Night Drive Mix
```

Use experiments rather than assuming one title style is always best.

---

# P16. Thumbnail optimization

Generate multiple candidates, score them for clarity, brand conformity, mobile readability and visual clutter, then measure actual CTR after publication where appropriate. The thumbnail selection model should learn from observed performance.

---

# PART VII — AUTONOMOUS YOUTUBE PUBLISHING

## P17. Publishing engine

Create and manage:

```text
video
playlist
metadata
thumbnail
AI disclosure
schedule
```

Programmatic YouTube uploads require the appropriate OAuth setup and project verification/audit considerations.

---

# P18. Automatic AI disclosure

AI-generated music should be handled through the platform's applicable synthetic-media disclosure process. The publishing engine should keep provenance and disclosure metadata as first-class fields rather than treating them as an afterthought.

---

# PART VIII — SPOTIFY DISTRIBUTION

## P19. API-capable distribution

Create a replaceable distributor adapter:

```text
submit_release()
get_release_status()
get_isrc()
get_streams()
get_revenue()
get_errors()
```

Candidates can change over time; provider selection must not leak into the rest of the system.

---

# P20. Catalog management

Every release should map across platforms:

```text
track_id
ISRC
UPC
artist
release
YouTube_video
Spotify_URL
publish_date
streams
royalty
```

Treat every released track as a persistent catalog asset.

---

# PART IX — ANALYTICS

## P21. Pull YouTube performance automatically

Collect, where authorized and available:

```text
views
watch time
average view duration
average percentage viewed
likes
comments
shares
subscribers gained/lost
estimated revenue
geography
traffic sources
device
```

---

# P22. Performance database

Every published video must retain its experimental context:

```text
video
 ├── concept arm
 ├── genre
 ├── mood
 ├── language
 ├── duration
 ├── playlist structure
 ├── thumbnail
 ├── title archetype
 ├── songs
 ├── visual concept
 │
 └── results
      ├── CTR
      ├── views
      ├── watch time
      ├── retention
      ├── subs
      ├── revenue
      ├── geography
      └── traffic sources
```

---

# PART X — RECURSIVE LEARNING LOOP

## P23. Automated weekly review

Use multiple specialized agents:

```text
MARKET AGENT
A&R AGENT
CONTENT AGENT
GROWTH AGENT
FINANCE AGENT
RISK AGENT
CEO AGENT
```

The high-level reasoning model should synthesize these reports into the next production allocation, while cheap local models handle extraction and routine classification where practical.

---

# PART XI — AUTONOMOUS BUDGET ALLOCATION

## P24. Production capital allocation

Given a configurable weekly budget, allocate production spend across experimental arms based on posterior expected economic value while preserving exploration.

Example:

```text
R&B night drive       40%
Rainy city pop        25%
Luxury deep house     15%
Experimental          10%
New discovery         10%
```

After performance updates, the distribution changes automatically.

---

# PART XII — SHORT-FORM ENGINE

## P25. Automatically derive Shorts

Each long-form playlist can feed an automated clip-selection stage:

```text
song clips
hook clips
emotional moments
visual loops
```

Select clips based on predicted first-seconds impact and musical/visual hook strength.

---

# PART XIII — COMMUNITY / RETENTION

## P26. Convert audience feedback into research data

Monitor comments and classify:

```text
requests
positive reactions
negative reactions
genre requests
song requests
complaints
copyright claims
```

Low-risk interactions can be automated; sensitive or high-risk cases should escalate.

---

# PART XIV — FAILURE MANAGEMENT

## P27. Durable workflow states

Example lifecycle:

```text
IDEA
 ↓
RESEARCHED
 ↓
POLICY_CHECKED
 ↓
GENERATING
 ↓
GENERATED
 ↓
QA
 ↓
REJECTED / PASSED
 ↓
ASSEMBLING
 ↓
PACKAGED
 ↓
UPLOADING
 ↓
PROCESSING
 ↓
PUBLISHED
 ↓
ANALYZING
 ↓
LEARNING
```

Persist retry counts, errors, timestamps, provider IDs, costs, inputs and outputs.

---

# PART XV — RIGHTS / COMPLIANCE ENGINE

## P28. Track provenance

For every asset store:

```text
asset_id
generator
model
model_version
account
subscription_status
prompt
generation_timestamp
source_inputs
license_terms
human_contribution
commercial_use_eligibility
```

Keep commercial-use entitlement separate from copyright-protection status.

---

# P29. Hard-stop publication criteria

Stop publication when:

```text
artist reference detected
copyrighted lyric similarity high
sample provenance uncertain
commercial license uncertain
unauthorized voice cloning detected
AI policy conflict
distributor rejection
platform claim
```

These are the main categories that should require human intervention.

---

# PART XVI — FINANCIAL ENGINE

## P30. Track each song as an economic asset

Store:

```text
generation_cost
editing_cost
visual_cost
distribution_cost
YouTube_revenue
Spotify_revenue
other_revenue
total_revenue
ROI
lifetime_value
```

The system should learn which content types make money, not merely which ones attract views.

---

# PART XVII — DASHBOARD

The operator dashboard should have six primary views:

1. Command Center — revenue, views, streams, subscribers, catalog size, current best/worst arms.
2. Market Map — demand vs. competition vs. growth.
3. Production Queue — generation, QA, rendering, publishing, blocked jobs.
4. Experiment Board — live opportunity arms and posterior estimates.
5. Economics — revenue, costs and ROI by track/genre/channel.
6. Exceptions — only items requiring human intervention.

---

# PART XVIII — HUMAN INTERVENTION MODEL

The operator should not routinely decide genres, individual songs, titles or upload timing.

The interface is exception-driven.

Example:

```text
ACTION REQUIRED

Distributor rejected release.
Reason: artist identity conflict.
Recommended actions: create a new artist identity or map to an existing one.

[APPROVE] [REJECT]
```

and:

```text
REVIEW

Potential melodic similarity detected.
Confidence: 74%.
No upload performed.

[RELEASE] [DISCARD]
```

---

# PART XIX — TECHNOLOGY STACK

Recommended baseline:

### Orchestration

Python + Temporal for durable workflows, retries, state and schedules.

### Backend

FastAPI.

### Database

PostgreSQL.

### Object storage

MinIO for audio, stems, images, videos and thumbnails.

### AI router

Claude for high-level reasoning; local models such as Qwen for bulk classification/extraction where appropriate.

### Similarity

Local embedding/similarity models.

### Audio

FFmpeg, librosa, pydub and loudness normalization.

### Video

FFmpeg plus GPU encoding/procedural motion graphics.

### Research

YouTube Data/Analytics/Reporting APIs, permitted trend sources, permitted music-market APIs, and isolated browser automation for sources without suitable APIs.

### Distribution

A replaceable API-capable distributor provider.

### Monitoring

Prometheus, Grafana, structured logs, cost alerts and error alerts.

---

# PART XX — DATABASE MODEL

Core tables:

```text
market_sources
market_queries
market_observations

content_arms
content_arm_priors
content_arm_results

songs
song_generations
song_versions
song_qc

playlists
playlist_tracks

visual_assets
thumbnail_variants
video_assets

youtube_videos
youtube_metrics

spotify_releases
spotify_metrics
royalty_records

experiments
experiment_assignments

licenses
provenance

jobs
job_runs
failures

budgets
costs
revenue

decisions
decision_log
```

Every production decision should be traceable.

---

# PART XXI — DECISION LOG

Every major decision should record:

```text
DATE
DECISION
INPUT DATA
MODEL
CONFIDENCE
ACTION
RESULT
```

Example:

```text
2026-11-03

Decision:
increase rainy-night R&B allocation

Reason:
7-day normalized views +82%
watch time +49%
subscriber conversion +31%

Confidence:
0.81

Budget:
+25%
```

Later record the realized outcome and assess decision quality.

---

# PART XXII — EXPERIMENTAL DESIGN

## Phase 1 — Exploration

Start with approximately 30–50 videos across 8–12 genre/use-case families. The objective is to map the market rather than maximize early revenue.

## Phase 2 — Identification

Kill weak arms, expand promising arms and continue controlled exploration. Identify:

```text
best genre
best mood
best use case
best language
best duration
best visual
best title
best thumbnail pattern
```

## Phase 3 — Exploitation

Illustrative allocation:

```text
50% strongest category
25% adjacent category
15% second-best category
10% completely new experiment
```

---

# PART XXIII — MULTI-CHANNEL EXPANSION

Do not start with multiple channels. The backend should nevertheless support multiple brands later:

```text
Brand A — NEON SEOUL
Brand B — MIDNIGHT TOKYO
Brand C — RAINY TAIPEI
Brand D — HOTEL NOIR
```

Each channel must have an independent brand/content genome while sharing the common intelligence and production infrastructure.

---

# PART XXIV — REVENUE TARGET ARCHITECTURE

Planning assumptions from the initial business model:

| Stage | Target | Approx. YouTube views/month | Approx. Spotify streams/month |
|---|---:|---:|---:|
| Stage A | ₩1M/mo | ~320k | ~41k |
| Stage B | ₩3M/mo | ~840k | ~184k |
| Stage C | ₩10M/mo | ~2.4M | ~816k |

These are base-case planning assumptions, not guaranteed rates.

---

# PART XXV — MOST IMPORTANT STRATEGIC PRINCIPLE

Do not build an "AI music YouTube channel."

Build an:

> **Autonomous music discovery and publishing engine.**

The defensible asset should become:

```text
MARKET DATA
     ↓
CONTENT GENOME
     ↓
EXPERIMENT DATA
     ↓
PROPRIETARY PERFORMANCE HISTORY
     ↓
OPTIMIZATION MODEL
     ↓
CATALOG
     ↓
AUDIENCE
```

The moat should not be knowledge of how to prompt one music model. It should be accumulated market intelligence, experimentation history, a catalog and an audience.

---

# PART XXVI — P0 → P12 IMPLEMENTATION ROADMAP

| Phase | Objective | Human input |
|---|---|---|
| **P0** | Accounts, OAuth, policies, infrastructure | High |
| **P1** | Market intelligence crawler | Low |
| **P2** | Opportunity scoring / A&R engine | None |
| **P3** | Content genome + experiment framework | None |
| **P4** | Music generation factory | None after provider auth |
| **P5** | Music QA / provenance / rights | Exception only |
| **P6** | Playlist + visual + video factory | None |
| **P7** | YouTube publishing engine | None |
| **P8** | Spotify/DSP distribution engine | Exception only |
| **P9** | Analytics + economics engine | None |
| **P10** | Recursive learning / autonomous allocation | None |
| **P11** | Shorts/social/community engine | None |
| **P12** | Multi-brand scaling | Exception only |

---

# PART XXVII — P1 → P10 BUILD ORDER

P0 is the infrastructure/permissions prerequisite. P1–P3 are the intelligence core. Production automation should not be built without the research/experiment layer because otherwise the system can mass-produce content without learning what the market rewards.

The intended dependency chain is:

```text
P0
 ↓
P1 Market Intelligence
 ↓
P2 Autonomous A&R
 ↓
P3 Experiment / Content Genome
 ↓
P4 Music Generation
 ↓
P5 QA / Rights
 ↓
P6 Media Factory
 ↓
P7 YouTube
 ↓
P8 Distribution
 ↓
P9 Analytics / Economics
 ↓
P10 Recursive Optimization
```
