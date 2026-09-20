# Control Plane — Operator Frontend & Human Intervention System
## Detailed Implementation Plan

**Status:** Implementation-ready blueprint  
**Role:** Cross-cutting system spanning P1–P9 (and future P10)  
**Primary goal:** Let one operator supervise the autonomous music business from one browser application, while routine work remains fully automated and human actions are limited to exceptions, approvals, policy changes, and emergency controls.

---

# 1. Executive objective

The system already has specialized phases:

```text
P1  Market Intelligence
P2  Autonomous A&R
P3  Content Genome / Experiments
P4  Music Generation
P5  Quality / Rights / Release Gate
P6  Playlist / Visual / Video Factory
P7  YouTube Publishing
P8  DSP Distribution
P9  Analytics / Economics
P10 Recursive Optimization (future)
```

The frontend must not become another phase in this chain.

Instead, create a **Control Plane** sitting above the phases:

```text
                         ┌─────────────────────────┐
                         │  OPERATOR CONTROL PLANE  │
                         │                         │
                         │ Observe / Investigate   │
                         │ Review / Approve        │
                         │ Pause / Resume          │
                         │ Retry / Repair          │
                         │ Change Policy / Budget  │
                         │ Emergency Stop          │
                         └────────────┬────────────┘
                                      │
       ┌──────────────┬───────────────┼──────────────┬──────────────┐
       ▼              ▼               ▼              ▼              ▼
      P1             P2              P3             P4             P5...
```

The human should **supervise**, not manually operate the factory.

The target daily experience is:

```text
Open dashboard
↓
Check overall system health
↓
Resolve exceptional cases
↓
Inspect business performance
↓
Leave system running
```

not:

```text
choose genre
→ write prompt
→ generate song
→ make thumbnail
→ upload
→ schedule
→ upload DSP
```

---

# 2. Product philosophy

## 2.1 Exception-driven UI

The default state is:

```text
No intervention required.
```

The UI should make this obvious.

Example:

```text
SYSTEM
● Autonomous
0 actions required
```

If an exception appears:

```text
SYSTEM
⚠ 2 actions required
```

## 2.2 Evidence before action

Every actionable case should show:

```text
What happened
Why automation stopped
What evidence exists
What policy applies
What the system recommends
What the possible consequences are
What actions are available
```

## 2.3 Backend remains authoritative

The browser is a client of the orchestration system.

It is never the authority for:

```text
workflow state
financial state
rights state
experiment assignment
provider state
publication state
```

## 2.4 Commands, not arbitrary mutation

The UI sends explicit commands such as:

```text
retry_job
approve_review
request_refinement
pause_system
publish_video
reschedule_release
request_takedown
```

Never expose a generic arbitrary object mutation endpoint to the UI.

---

# 3. Recommended technical stack

## 3.1 Next.js App Router + TypeScript

Use current Next.js App Router conventions.

Prefer:

```text
Server Components
```
for initial/read-heavy rendering and:

```text
Client Components
```
for interactive controls, live state, forms, players and local UI state.

The current Next.js guidance emphasizes deliberate server/client boundaries, Suspense, error boundaries, and avoiding unnecessary data waterfalls.

Reference:

https://nextjs.org/docs/app

## 3.2 TanStack Query

Use TanStack Query for client-side server-state management, mutation state, cache invalidation and selective refetching.

Current documentation identifies queries, mutations and invalidation as core concepts and recommends typed usage in TypeScript. citeturn910128search0turn910128search4

Use query keys that contain every variable which materially changes the query. citeturn910128search5

## 3.3 Live updates: SSE first

Use Server-Sent Events for operational event streaming:

```text
job progress
state transitions
new exceptions
publishing events
provider health
system pause/resume
```

Use normal HTTP commands for mutations.

Do not add WebSockets unless a concrete requirement appears.

## 3.4 Styling

Use:

```text
Tailwind CSS
accessible component primitives / shadcn-style components
```

Do not lock the UI to a highly stylized consumer-music design.

## 3.5 Charts/tables

Choose the library already used in the repository if one exists.

Otherwise use a mature React charting library and a proper table implementation with virtualization only where data volume requires it.

---

# 4. Top-level architecture

```text
                         Browser
                            │
                            │ HTTPS
                            │ SSE
                            ▼
                 ┌───────────────────────┐
                 │ Next.js Control Plane │
                 │                       │
                 │ App Router            │
                 │ Server Components     │
                 │ Client Components     │
                 │ TanStack Query        │
                 │ SSE client            │
                 └───────────┬───────────┘
                             │
                      typed API/BFF
                             │
                             ▼
                 ┌───────────────────────┐
                 │ Control Plane API     │
                 │                       │
                 │ auth                  │
                 │ authorization         │
                 │ read aggregation      │
                 │ command validation    │
                 │ event stream          │
                 └───────────┬───────────┘
                             │
                 ┌───────────┴────────────┐
                 ▼                        ▼
       Domain services / workflows     Read models
                 │                        │
                 ▼                        ▼
          P1–P9 / Temporal             PostgreSQL
                                        Object Store
```

The frontend should not need direct access to Temporal, PostgreSQL, object storage, or provider APIs.

---

# 5. Responsibilities by layer

## Frontend

Responsible for:

```text
presentation
navigation
forms
local UI state
visualization
accessibility
command initiation
live status presentation
```

## Control Plane API/BFF

Responsible for:

```text
authentication/session
authorization checks
API aggregation
command contracts
SSE fan-out
correlation IDs
frontend-specific response shaping
```

## Domain backend

Responsible for:

```text
business rules
workflow state
policy enforcement
financial integrity
provider interactions
```

## Workflow engine

Responsible for:

```text
durable execution
retries
timeouts
reconciliation
long-running jobs
```

---

# 6. Non-negotiable architectural boundary

The frontend must never become a second orchestration engine.

Bad:

```text
React page decides:
if retry count < 3 then call provider
```

Good:

```text
React page
→ retry_job command
→ backend/workflow policy decides what retry means
```

The frontend should never encode autonomous business logic that can drift from the backend.

---

# 7. Route structure

Recommended Next.js structure:

```text
app/
  (control)/
    layout.tsx
    page.tsx                         # Home

    market/
      page.tsx
      [armId]/page.tsx

    ar/
      page.tsx
      [armId]/page.tsx

    experiments/
      page.tsx
      [experimentId]/page.tsx

    production/
      page.tsx
      [planId]/page.tsx
      candidates/[candidateId]/page.tsx

    quality/
      page.tsx
      [evaluationId]/page.tsx

    media/
      page.tsx
      [packageId]/page.tsx

    publishing/
      page.tsx
      [attemptId]/page.tsx

    distribution/
      page.tsx
      [releaseId]/page.tsx

    analytics/
      page.tsx

    catalog/
      page.tsx
      tracks/[trackId]/page.tsx
      releases/[releaseId]/page.tsx

    exceptions/
      page.tsx
      [caseId]/page.tsx

    system/
      page.tsx

    audit/
      page.tsx

    settings/
      page.tsx
```

Exact file placement may vary with repository conventions.

---

# 8. Global application shell

Every authenticated page should use a shared shell:

```text
┌──────────────────────────────────────────────────────────────┐
│ logo | system status | channel/brand | exceptions | account │
├───────────────┬──────────────────────────────────────────────┤
│ sidebar       │ main content                                 │
│               │                                              │
│ Home          │                                              │
│ Market        │                                              │
│ A&R           │                                              │
│ Experiments   │                                              │
│ Production    │                                              │
│ Quality       │                                              │
│ Media         │                                              │
│ Publishing    │                                              │
│ Distribution  │                                              │
│ Analytics     │                                              │
│ Catalog       │                                              │
│ Exceptions    │                                              │
│ System        │                                              │
│ Audit         │                                              │
│ Settings      │                                              │
└───────────────┴──────────────────────────────────────────────┘
```

The shell must be stable while route content changes.

---

# 9. Navigation hierarchy

The sidebar should separate:

### Operating

```text
Home
Exceptions
Production
Publishing
Distribution
```

### Intelligence

```text
Market
A&R
Experiments
Analytics
Catalog
```

### Governance

```text
Quality
System
Audit
Settings
```

This is easier to operate than simply mirroring P1–P9 as menu items.

---

# 10. Global header

Show:

```text
system name
current brand/channel
system health
running workflows
open exceptions
user
```

Example:

```text
AUTONOMOUS MUSIC ENGINE

● Autonomous     8 running     1 action required

[ NEON SEOUL ▼ ]
```

---

# 11. Global system status

Possible states:

```text
AUTONOMOUS
AUTONOMOUS_WITH_EXCEPTIONS
DEGRADED
PAUSED
EMERGENCY_PAUSED
MAINTENANCE
```

Do not use a generic `online/offline` indicator. That hides too much operational meaning.

---

# 12. System-status click behavior

Clicking global status should open:

```text
System Health
```

The page should show:

```text
why the state exists
when it started
who/what triggered it
what is affected
what actions are available
```

---

# 13. Home / Command Center

Home is the most important screen.

It should answer within seconds:

```text
Is the business running?
Is the automation healthy?
What is happening now?
Do I need to act?
Are we spending abnormally?
What happened recently?
```

---

# 14. Home layout

Recommended structure:

```text
SYSTEM STATE
┌───────────────────────────────────────────────────────┐
│ ● AUTONOMOUS   8 workflows   1 exception   ₩... cost │
└───────────────────────────────────────────────────────┘

BUSINESS SNAPSHOT
┌─────────┬─────────┬─────────┬─────────┬─────────────┐
│ Revenue │ Views   │ Streams │ Subs    │ Contribution│
└─────────┴─────────┴─────────┴─────────┴─────────────┘

PIPELINE                     ACTION REQUIRED
┌──────────────────────┐     ┌──────────────────────────┐
│ P1 ● researching     │     │ HIGH — Artist conflict  │
│ P2 ● allocating      │     │ MED — License review    │
│ P3 ● 3 experiments   │     │                          │
│ P4 ● 17 generating   │     │ [Review all]             │
│ P5 ● 9 evaluating    │     └──────────────────────────┘
│ P6 ● 2 rendering     │
│ P7 ● 1 publishing    │
│ P8 ● 4 distributing  │
│ P9 ● measuring       │
└──────────────────────┘

RECENT EVENTS
...
```

---

# 15. Home pipeline cards

Each phase card should expose:

```text
phase
current state
active count
blocked count
failed count
oldest active job age
```

Example:

```text
P4 Music
17 active
1 failed
oldest: 8m
```

Clicking deep-links to filtered details.

---

# 16. Action Required inbox

This is the primary human-control surface.

It should contain only:

```text
human_required = true
```

Do not put routine warnings there.

---

# 17. Action Required card anatomy

Every card contains:

```text
severity
category
short title
object
problem
why automation stopped
recommended action
age
```

Example:

```text
HIGH
ARTIST IDENTITY CONFLICT
Release REL-1042

Possible mismatch with existing artist profile.
Automated mapping confidence: 0.62.

Automation stopped to avoid misdelivery.

[Review Identity]
```

---

# 18. Exception priorities

Use:

```text
CRITICAL
HIGH
MEDIUM
LOW
```

Do not use arbitrary “urgent” visual labels disconnected from backend severity.

---

# 19. Exception categories

Initial categories:

```text
RIGHTS
QUALITY
PUBLISHING
DISTRIBUTION
IDENTITY
FINANCE
SYSTEM
SECURITY
STRATEGY
```

---

# 20. Exception detail page

Every case gets a dedicated page containing:

```text
Summary
Current status
Evidence
Timeline
Related objects
Policy
Recommended action
Available commands
Audit history
```

---

# 21. Exception state machine

UI states:

```text
OPEN
IN_REVIEW
WAITING_FOR_EVIDENCE
RESOLVED
EXPIRED
CANCELLED
```

Do not allow the frontend to invent states.

---

# 22. Human review workflow

Standard:

```text
Open case
↓
Inspect evidence
↓
Read recommendation
↓
Choose action
↓
Confirm if required
↓
Backend command
↓
Command accepted
↓
Workflow progresses
↓
Result appears live
```

---

# 23. Command envelope

Every mutation should conceptually look like:

```json
{
  "command": "review.approve",
  "target_type": "qa_evaluation",
  "target_id": "eval_123",
  "reason": "Verified license evidence",
  "expected_state_version": 7,
  "idempotency_key": "..."
}
```

The backend validates the command.

---

# 24. Command response

Return:

```json
{
  "command_id": "cmd_123",
  "accepted": true,
  "status": "QUEUED",
  "workflow_id": "wf_123"
}
```

The UI must then observe actual state through query/event updates.

---

# 25. Stale-state protection

This is mandatory.

Every state-changing command should carry:

```text
expected_state_version
```

If the record changed since the browser loaded it:

```text
STALE_COMMAND
```

The UI must show:

```text
This item changed while you were reviewing it.

[Refresh and Review]
```

Never blindly overwrite newer state.

---

# 26. Confirmation levels

## Level 1 — Routine

No confirmation:

```text
retry failed job
acknowledge notice
open detail
```

## Level 2 — Consequential

Confirm:

```text
cancel job
approve QA
change schedule
```

## Level 3 — Destructive/high impact

Confirm with impact summary, reason, and optionally typed confirmation:

```text
public publish
artist remap
ISRC change
audio replacement
takedown
large budget increase
policy activation
```

---

# 27. Emergency controls

Global controls should include:

```text
Pause autonomous system
Pause public publishing
Pause distribution
Pause generation
Resume
```

These commands must change backend policy/state.

Disabling UI buttons is not an emergency control.

---

# 28. Emergency pause semantics

`Pause autonomous system` should:

```text
prevent creation of new autonomous work
block new high-impact commands
preserve queues
allow safe in-flight work to finish according to backend policy
```

Do not blindly kill every process.

---

# 29. Emergency banner

When paused:

```text
⏸ AUTONOMOUS EXECUTION PAUSED

New autonomous work is blocked.
Started workflows are continuing according to safety policy.

[Resume]
```

---

# 30. Market page

P1-facing read model.

Pages/sections:

```text
Opportunity Map
Opportunity Table
Market Changes
Research Runs
Source Health
```

---

# 31. Opportunity table

Columns:

```text
Arm
Market score
Confidence
Demand
Growth
Competition
Freshness
Sample size
Status
```

All score definitions should be accessible from tooltips/details.

---

# 32. Opportunity map

Recommended:

```text
X = competition
Y = demand
point size = sample size
point opacity = confidence
```

Optional color:

```text
growth band
```

Do not encode a hidden “best” dimension.

---

# 33. Market detail

Show:

```text
taxonomy
market score
confidence
component scores
source evidence
observation count
freshness
changes over time
```

---

# 34. A&R page

P2-facing.

Show:

```text
current allocation
exploit/explore mix
arm posterior summaries
cluster concentration
baseline allocation
fatigue
recent selection history
```

---

# 35. A&R decision explanation

Show:

```text
Selected for:
EXPLOIT

Posterior expected reward: ...
Uncertainty: ...
P1 market score: ...
Recent fatigue: low
Constraint impact: none
```

Use exact model terms rather than vague prose such as “AI likes this.”

---

# 36. Experiments page

P3-facing.

Filters:

```text
Queued
Active
Production
Measuring
Mature
Contaminated
Needs Review
```

Columns:

```text
Experiment
Arm
Type
Hypothesis
Treatments
Progress
Maturity
Primary metric
Integrity
```

---

# 37. Experiment detail

Show complete lineage:

```text
P2 decision
↓
P3 experiment
↓
treatments
↓
content packages
↓
P4 candidates
↓
P5 evaluation
↓
P6 media
↓
P7/P8 distribution
↓
P9 outcomes
```

---

# 38. Experimental-variable visualization

Use chips/sections:

```text
FIXED
CONTROLLED
EXPERIMENTAL
INCIDENTAL
FREE
```

Example:

```text
FIXED
Language = English

EXPERIMENTAL
Title archetype = A vs B

BOUNDED
BPM = 82–96

FREE
Instrumentation
```

---

# 39. Experiment integrity

Show a prominent indicator:

```text
✓ Integrity intact
```

or:

```text
⚠ Integrity issue
Title and thumbnail both changed although title was the only experimental factor.
```

---

# 40. Production page

P4-facing.

Show:

```text
Generation queue
Active jobs
Candidate bank
Provider health
Credits
Cost
Failure rate
```

---

# 41. Generation queue

Columns:

```text
Track
Experiment
Role
Target
Generated / Target
Provider
Status
Elapsed
Cost
```

---

# 42. Reliable progress rule

Only display a numeric percentage if the backend has a real progress source.

If not:

```text
PROCESSING
```

Do not invent:

```text
73%
```

---

# 43. Generation detail

Show:

```text
TrackSpec
GenerationPlan
Creative Variant
Provider
Model
Attempts
Prompt version
Provider job ID
Cost
Artifact
P5 status
```

---

# 44. Prompt viewer

Display:

```text
Canonical structured brief
Provider prompt
Parameters
Prompt version
```

Canonical structured input should be the default.

---

# 45. Candidate bank

Show:

```text
candidate
waveform
play button
duration
provider
model
P5 status
cost
created at
```

A candidate should be auditioned through a secure backend media endpoint.

---

# 46. Audio player

Support:

```text
play/pause
seek
volume
waveform
current time
finding markers
```

Optional:

```text
[02:13] vocal artifact
```

Clicking a marker should seek to the relevant timestamp.

---

# 47. Quality page

P5-facing.

Filters:

```text
Passed
Warnings
Quarantined
Rejected
Rights Unknown
Similarity Risk
Review Required
```

---

# 48. Quality detail

Sections:

```text
Disposition
Technical
Musical
Vocal
Lyrics
Provenance
Rights
Policy
Evidence
History
```

---

# 49. Evidence display

Every finding should show:

```text
code
severity
confidence
source/tool
version
timestamp
location
```

Example:

```text
MEDIUM — VOCAL_ARTIFACT
Confidence: 0.84
02:11–02:13
Detector: vocal-qc-v3
```

---

# 50. QA review layout

Desktop layout:

```text
┌─────────────────────────┬──────────────────────────┐
│ Audio / waveform        │ Findings                 │
│                         │                          │
│                         │ Evidence                 │
│                         │ Policy                   │
│                         │                          │
│                         │ [Approve] [Reject]       │
│                         │ [Refine] [More evidence] │
└─────────────────────────┴──────────────────────────┘
```

This minimizes navigation during review.

---

# 51. Rights evidence display

Show separately:

```text
Commercial-use status
License evidence
Provider/model
Generation timestamp
Voice status
Similarity findings
Provenance status
Copyright-protection status
```

Use precise wording:

```text
Commercial-use evidence: PRESENT
Internal similarity screen: PASS
Copyright protection: NOT DETERMINED
```

---

# 52. Media page

P6-facing.

Show:

```text
Media packages
Rendering
Previews
Final videos
Thumbnail variants
Artwork
Metadata
```

---

# 53. Media package detail

Show:

```text
tracks
sequence
visual plan
brand version
render profile
preview
full video
thumbnail variants
metadata
checksums
QA
```

---

# 54. Playlist timeline

Read-only by default:

```text
00:00 Track 1
04:11 Track 2
08:22 Track 3
```

Overlay:

```text
intro
transition
chapter
outro
```

Do not make casual drag-and-drop reordering available in v1 because it can invalidate experiment lineage.

---

# 55. Publishing page

P7-facing.

Views:

```text
Ready
Uploading
Processing
Scheduled
Published
Failed
Blocked
```

---

# 56. Publishing detail

Show state timeline:

```text
✓ Preflight
✓ Upload session
✓ Upload complete
✓ Video ID captured
✓ Processing
✓ Thumbnail
✓ Playlist
✓ Scheduled
● Awaiting publication
```

Each step has:

```text
timestamp
remote ID
relevant evidence
```

---

# 57. Public-publish safety

Make it prominent:

```text
PUBLIC PUBLISHING
ENABLED
```

or:

```text
PUBLIC PUBLISHING
DISABLED
Reason: API audit / readiness not complete
```

Do not hide this in Settings only.

---

# 58. Publish confirmation

The modal should show:

```text
WHAT WILL HAPPEN

Channel: ...
Video: ...
Visibility: Public
Publish time: ...
Disclosure: AI-generated music
Playlist: ...

[Cancel]
[Confirm Public Publish]
```

---

# 59. Distribution page

P8-facing.

Display per-store status:

```text
Spotify        DELIVERED
Apple Music    DELIVERED
YouTube Music  PROCESSING
Other DSP      REJECTED
```

---

# 60. Distribution detail

Show:

```text
canonical release
artist
ISRC
UPC/EAN/etc.
distributor
store IDs
territory
release date
rejections
corrections
takedowns
```

---

# 61. Dangerous distribution actions

Require strong confirmation for:

```text
artist remap
ISRC change
audio replacement
territory expansion
takedown
release cancellation
```

---

# 62. Analytics page

P9-facing.

Start with:

```text
Revenue
Contribution
Views
Watch time
Streams
Subscribers
Catalog
Experiment outcomes
Data health
```

---

# 63. Analytics time ranges

Provide:

```text
Today
7d
28d
90d
Month
Quarter
Custom
```

Always display:

```text
Data as of ...
```

---

# 64. Observed vs estimated vs modelled

Every analytical visualization must distinguish:

```text
OBSERVED
ESTIMATED
MODELLED
FORECAST
```

Never combine them into one unlabeled line.

---

# 65. Economics dashboard

Show:

```text
Revenue
Variable cost
Contribution
Margin
ROI
Payback
Revenue concentration
Cost per successful asset
```

Drill-down:

```text
Company
→ Brand
→ Channel
→ Release
→ Track
→ Video
```

---

# 66. Experiment analytics

Show:

```text
arm
sample size
maturity
primary metric
observed outcome
uncertainty
experiment fidelity
contamination
revenue
cost
contribution
```

Use metric-specific labels rather than a generic “winner” badge.

---

# 67. Catalog page

Search by:

```text
title
artist
ISRC
UPC
experiment
arm
video ID
store ID
release ID
```

---

# 68. Track detail

This is the most important lineage page.

Show:

```text
Track identity
Generation history
P5 evaluations
Media usages
YouTube videos
DSP releases
Streams
Revenue
Costs
Experiment history
```

---

# 69. System Health page

Show:

```text
Workflow engine
Database
Object storage
Event stream
P1 sources
P4 providers
P7 YouTube
P8 distributors
P9 analytics
GPU/compute
Queues
```

---

# 70. Queue health

For each phase:

```text
queued
running
blocked
failed
oldest age
```

Example:

```text
P4 Generation
Queued: 12
Running: 4
Failed: 1
Oldest: 6m
```

---

# 71. Provider health

Show:

```text
status
latency
error rate
quota/credits
last success
last failure
```

Do not show credentials.

---

# 72. Audit page

Searchable append-only history.

Filters:

```text
actor
phase
action
target
severity
date
```

---

# 73. Audit detail

Show:

```text
actor
command
before state
requested change
after state
reason
policy version
software version
correlation ID
```

---

# 74. Settings

Sections:

```text
General
Brands
Channels
Policies
Budgets
Providers
Notifications
Security
Automation
```

---

# 75. Policy editor

Never edit an active policy directly.

Workflow:

```text
Draft
↓
Validate
↓
Diff
↓
Shadow
↓
Activate
↓
Rollback if needed
```

---

# 76. Policy diff

Show machine-readable and human-readable differences.

Example:

```text
P5 Rights Policy v3

commercial_use_unknown:
QUARANTINE → REJECT

voice_identity_unknown:
REVIEW → QUARANTINE
```

---

# 77. Budget page

Show separately:

```text
Actual
Reserved
Forecast
Remaining
```

By:

```text
P4
P5
P6
P7
P8
P9
```

and provider.

---

# 78. Budget change workflow

Require:

```text
current
new limit
impact
reason
confirmation
```

Backend applies the change.

---

# 79. Global search

Support canonical IDs:

```text
arm
experiment
candidate
track
artifact
media package
video
publish attempt
release
ISRC
UPC
provider job
```

Search result should show:

```text
object type
name
status
brand
related lineage
```

---

# 80. Search behavior

Exact IDs should take precedence over fuzzy text matches.

Search ranking:

```text
exact ID
exact title/name
normalized title/name
fuzzy similarity
```

Do not let a fuzzy title match masquerade as an exact identity mapping.

---

# 81. Activity feed

Show high-value events:

```text
experiment created
generation completed
candidate quarantined
video published
release delivered
statement imported
system paused
```

Technical heartbeat events belong only on system diagnostics views.

---

# 82. Activity event contract

Recommended normalized event:

```json
{
  "event_id": "evt_123",
  "event_version": "1",
  "event_type": "youtube_video_published",
  "occurred_at": "2026-09-20T07:20:00Z",
  "entity_type": "youtube_video",
  "entity_id": "abc123",
  "phase": "P7",
  "severity": "INFO",
  "summary": "YouTube video published",
  "payload": {
    "experiment_id": "exp_123"
  }
}
```

---

# 83. SSE endpoint

Recommended:

```text
GET /api/v1/control-plane/events/stream
```

Support:

```text
Last-Event-ID
heartbeat
filters
reconnect
```

---

# 84. SSE event filters

Allow filtering by:

```text
brand
channel
phase
severity
entity_type
```

Default subscription for Home should receive only operationally meaningful events.

---

# 85. SSE reconnection

Behavior:

```text
connected
↓
receive events
↓
network loss
↓
polling fallback
↓
reconnect
↓
resync missed state
```

The UI must converge to backend truth.

---

# 86. Event deduplication

Use:

```text
event_id
```

to prevent duplicate activity events.

The backend should also avoid sending contradictory duplicate events where possible.

---

# 87. TanStack Query strategy

Use query keys such as:

```text
['home', channelId]
['exceptions', filters]
['production', filters]
['generation-plan', planId]
['candidate', candidateId]
['qa-evaluation', evaluationId]
['media-package', packageId]
['youtube-publish', attemptId]
['distribution-release', releaseId]
['analytics', scope, timeRange]
```

Use targeted invalidation when events arrive.

---

# 88. Cache invalidation map

Example:

```text
music_generation_completed
→ production
→ candidate bank
→ experiment progress

music_candidate_quarantined
→ quality
→ exceptions
→ experiment progress

youtube_video_published
→ publishing
→ home
→ analytics anchors

distribution_delivered
→ distribution
→ catalog
→ analytics
```

Keep this mapping in one module.

---

# 89. Polling policy

Recommended initial defaults:

```text
operational pages: 15–30s fallback
historical analytics: manual or long stale time
system health: 15–30s
```

These are defaults only.

Do not globally poll every query every five seconds.

---

# 90. Freshness indicators

Operational:

```text
Live
Updated 12s ago
```

Analytics:

```text
Data as of Sep 19 2026
```

Financial:

```text
Latest distributor statement: Jul 2026
```

Use the actual backend timestamp.

---

# 91. Server/client rendering strategy

Use Server Components for:

```text
initial dashboard shell
stable summary tables
historical analytics pages
```

Use Client Components for:

```text
filters
modals
audio/video player
SSE connection
interactive charts
command buttons
forms
```

Do not mark entire pages `'use client'` just because one child is interactive.

---

# 92. Suspense strategy

Independent Home sections should load independently:

```text
System status
Pipeline
Exceptions
Business metrics
Recent events
```

A slow analytics source should not blank out system status.

---

# 93. Error boundaries

Provide route-level and section-level boundaries.

Every failure state should offer:

```text
Retry
View system health
```

---

# 94. API typing

Prefer:

```text
OpenAPI
→ generated TypeScript client/types
```

Avoid manually duplicating all backend interfaces in frontend source.

---

# 95. API versioning

Use:

```text
/api/v1/...
```

or equivalent semantic contract versioning.

Never make frontend behavior depend on undocumented backend fields.

---

# 96. Authentication

Use a proper authenticated session.

Do not implement:

```text
?admin=true
local hard-coded password
frontend-only secret
```

The exact auth provider can be chosen according to the broader infrastructure.

---

# 97. Authorization

Initial role can be:

```text
OPERATOR
```

but permissions should be capability-based:

```text
view:* 
execute:retry
execute:approve
execute:publish
execute:distribution
execute:policy
execute:budget
execute:destructive
```

Backend authorization is authoritative.

---

# 98. Session expiry

When auth expires:

```text
queries may become stale
mutations must stop
```

Show:

```text
Session expired
[Sign in again]
```

Do not silently retry privileged commands.

---

# 99. Environment indicator

Support:

```text
LOCAL
STAGING
PRODUCTION
```

Display it in the shell.

Local/staging should make high-impact controls visibly disabled.

---

# 100. Environment guard

Default:

```text
LOCAL
Public YouTube publishing: disabled
DSP distribution: disabled

STAGING
Public YouTube publishing: disabled
DSP distribution: test-only if supported

PRODUCTION
Policy controlled
```

---

# 101. Media access

Audio/video should be served through secure application endpoints or short-lived signed URLs.

Example:

```text
GET /api/v1/media/{artifactId}/stream
```

The browser must never receive object-store credentials.

---

# 102. Media playback security

Use authenticated access checks.

Do not put permanent public URLs in the database response unless the asset is intentionally public.

---

# 103. Frontend file upload

For future operator uploads:

```text
rights evidence
artwork replacement
distributor statement
```

Require backend handling for:

```text
size limit
MIME validation
checksum
virus/malware scanning
ownership linkage
```

---

# 104. Human-upload metadata

Every upload should attach:

```text
actor
purpose
related entity
case/release ID
checksum
timestamp
```

---

# 105. Forms

Use schema-driven forms.

Frontend validation:

```text
UX only
```

Backend validation:

```text
authoritative
```

---

# 106. Mutation UX

After a mutation:

```text
command accepted
```

not:

```text
Done
```

until backend state confirms completion.

Example:

```text
Retry queued
```

then live transition:

```text
Running
```

then:

```text
Completed
```

---

# 107. Optimistic UI

Only use optimistic state for trivial local UI changes.

Never optimistically set:

```text
published
approved
paid
released
```

---

# 108. Destructive confirmation content

Every high-risk confirmation should contain:

```text
Object
Current state
Requested change
Expected effect
Potential side effect
Reversibility
Reason
```

---

# 109. Takedown confirmation example

```text
REQUEST TAKEDOWN

Release: REL-1042
Scope: All stores
Artist: NEON SEOUL

This requests removal through the distributor.
Propagation time varies by store.

Reason: __________________

Type TAKEDOWN to confirm.

[Cancel]  [Request Takedown]
```

---

# 110. Artist remapping confirmation

```text
CHANGE ARTIST MAPPING

Current:
Artist ID A

New:
Artist ID B

This may change the storefront artist page to which the release is delivered.

Reason: __________________

[Cancel] [Confirm]
```

---

# 111. Budget confirmation

Show:

```text
Current monthly cap: ₩500,000
New cap: ₩1,000,000
Change: +100%

Reason required.
```

---

# 112. Command audit flow

Every state-changing action:

```text
UI
 ↓
command API
 ↓
authentication
 ↓
authorization
 ↓
policy validation
 ↓
stale-state validation
 ↓
command ledger
 ↓
workflow
 ↓
audit event
```

---

# 113. Command idempotency

Generate an idempotency key for every mutation request.

Repeated submission of the same command must not create duplicate work.

---

# 114. Command errors

Map backend errors to clear UI messages:

```text
AUTH_REQUIRED
FORBIDDEN
NOT_FOUND
STALE_COMMAND
INVALID_STATE
POLICY_BLOCKED
BUDGET_BLOCKED
QUOTA_BLOCKED
PROVIDER_UNAVAILABLE
REQUIRES_HUMAN_REVIEW
CONFLICT
VALIDATION_ERROR
INTERNAL_ERROR
```

---

# 115. Error UX

Example:

```text
Could not retry this job.

Reason: the generation plan was already completed by another worker.

[Refresh]
```

Avoid raw stack traces in normal UI.

Technical diagnostics can be available under a details section.

---

# 116. Technical diagnostics

Advanced users should be able to see:

```text
correlation ID
workflow ID
job ID
provider request ID
error code
retry count
last event
```

This reduces the need to open logs.

---

# 117. Global command palette

Safe commands:

```text
Go Home
Go Exceptions
Search Track
Show Failed Jobs
Show System Health
Show Upcoming Releases
```

Do not allow destructive commands to execute directly from one keystroke.

---

# 118. Keyboard shortcuts

Safe examples:

```text
/ → search
G H → Home
G E → Exceptions
G P → Production
G S → System
```

---

# 119. Accessibility

Must support:

```text
keyboard navigation
visible focus
screen reader labels
semantic headings
reduced motion
contrast
non-color status indicators
```

---

# 120. Status indicators

Every status should use:

```text
icon + text
```

not color alone.

Example:

```text
✓ PASSED
⚠ QUARANTINED
✕ REJECTED
● RUNNING
```

---

# 121. Motion policy

Use animation for:

```text
progress transition
status change
live event arrival
```

Respect reduced-motion preferences.

Do not constantly animate dashboard cards.

---

# 122. Responsive design

Primary operator experience:

```text
desktop 1280px+
```

Secondary support:

```text
1024px
768px
```

Mobile behavior should prioritize:

```text
exceptions
system status
simple approvals
```

rather than full analytics tables.

---

# 123. Visual design direction

The product should feel like:

```text
operations console
+ media studio
+ financial dashboard
```

not:

```text
consumer music streaming app
```

Use:

```text
neutral surfaces
high information density
strong typography
compact tables
subtle status accents
```

Avoid excessive:

```text
neon gradients
glassmorphism
animated backgrounds
huge decorative cards
```

---

# 124. Typography

Use a clear sans-serif UI font.

For IDs/logs:

```text
monospace
```

Keep body text comfortably readable.

Do not use ultra-light font weights for important data.

---

# 125. Layout density

The control plane should fit meaningful state above the fold.

Home should show:

```text
system status
exceptions
pipeline
key business metrics
```

without requiring scrolling to discover whether the system is broken.

---

# 126. Cards

Use cards only when grouping meaningful information.

Do not put every tiny number in a floating card.

Tables are often better for:

```text
jobs
experiments
releases
metrics
```

---

# 127. Charts

Every chart needs:

```text
metric name
unit
range
as-of time
legend
```

If forecast/modelled values are present, use explicit line style/legend differences.

---

# 128. Data table standards

Every major table should support:

```text
search
filter
sort
pagination
column preference
```

Persist only useful filter state in URLs.

---

# 129. URL state

Use query params for shareable views:

```text
?status=failed
?brand=neon-seoul
?date=28d
?experiment=exp_123
```

---

# 130. Deep-link requirement

Every major object needs a stable route.

Examples:

```text
/experiments/EXP-123
/candidates/CAND-123
/evaluations/EVAL-123
/media/PKG-123
/publishing/PUB-123
/distribution/REL-123
/catalog/tracks/TRK-123
```

---

# 131. Drawer vs page

Use a drawer for:

```text
quick inspection
recent event
small context preview
```

Use a full page for:

```text
rights review
financial investigation
complex experiments
multi-step corrections
```

---

# 132. Data aggregation for Home

Create a dedicated backend read model:

```text
control_plane_home_summary
```

The frontend should not make ten unrelated API calls just to paint Home.

Possible response:

```json
{
  "system": {...},
  "pipeline": {...},
  "exceptions": {...},
  "business": {...},
  "recent_events": [...],
  "as_of": "..."
}
```

---

# 133. Operational read model

Consider backend materialized/read views for:

```text
pipeline status
open exceptions
active jobs
provider health
upcoming publication
```

These are operationally hot and should not require expensive joins on every browser refresh.

---

# 134. Analytics read model

Likewise use backend aggregate views for:

```text
daily channel summary
monthly economics
experiment outcomes
catalog summary
```

---

# 135. Do not compute business metrics in React

Do not implement:

```text
revenue = sum(raw rows)
ROI = revenue / cost
```

inside components.

The UI displays backend-calculated metrics with definitions.

---

# 136. Do not compute workflow status in React

Do not infer:

```text
if progress = 100 then done
```

Use backend lifecycle state.

---

# 137. P1–P9 status aggregation

Create a shared phase-status object:

```json
{
  "phase": "P4",
  "state": "RUNNING",
  "queued": 5,
  "running": 4,
  "blocked": 0,
  "failed": 1,
  "oldest_active_age_seconds": 322
}
```

The Home page can then render all phases consistently.

---

# 138. Human intervention registry

Every exception-capable backend module should register:

```text
case type
severity
human required
available actions
confirmation level
expiry
```

The frontend consumes this rather than hard-coding every case type.

---

# 139. Action-schema driven UI

Where practical, the backend can expose:

```json
{
  "case_id": "case_123",
  "actions": [
    {
      "id": "approve",
      "label": "Approve",
      "confirmation_level": 2,
      "requires_reason": true
    }
  ]
}
```

The frontend renders permitted commands but never determines whether they are actually authorized.

---

# 140. Benefits of action schemas

This allows new exception types to appear without a giant frontend conditional:

```text
if case.type == X
if case.type == Y
if case.type == Z
```

But the backend remains authoritative over command semantics.

---

# 141. Action expiration

Some actions should expire.

Example:

```text
Approve publication
expires in 20 minutes
```

The backend must enforce expiry; UI only displays it.

---

# 142. Recommended notification system

Primary:

```text
Action Required inbox
```

Secondary:

```text
email / other notification channel
```

Use notifications for:

```text
critical exception
security issue
system pause
publication failure
budget breach
```

Not every normal event.

---

# 143. Browser notifications

Optional later feature.

If implemented:

```text
only high-priority actionable events
user-controlled
```

Do not request browser notification permission on first load without context.

---

# 144. AI-generated explanations

The UI may display LLM-generated summaries.

Label them:

```text
Automated explanation
```

But show structured evidence beneath.

---

# 145. AI explanation safety

Do not display unsupported language such as:

```text
AI says this song will definitely succeed.
```

Prefer:

```text
P2 posterior estimate: 0.73
P1 market score: 82
```

---

# 146. Financial explanation safety

Never let the LLM independently calculate:

```text
revenue
ROI
margin
```

The backend sends authoritative numbers to the narrative layer.

---

# 147. Evidence-first report generation

Recommended pipeline:

```text
backend facts
→ structured summary
→ optional LLM explanation
→ numeric claim validator
→ UI
```

---

# 148. Frontend observability

Track:

```text
page errors
API latency
mutation failures
SSE disconnects
JS errors
browser performance
```

Do not collect sensitive content unnecessarily.

---

# 149. Frontend telemetry privacy

Never send to analytics systems by default:

```text
full lyrics
provider prompts
OAuth tokens
financial statement contents
private audio
```

---

# 150. Security headers

Where applicable configure:

```text
CSP
secure cookies
HSTS
frame protections
referrer policy
```

Use environment-specific policy carefully because media playback and provider callbacks may need explicit origins.

---

# 151. XSS safety

Treat provider/LLM/user text as untrusted.

Render escaped text by default.

Avoid `dangerouslySetInnerHTML` unless content has passed an explicit sanitization path.

---

# 152. URL security

Only permit expected schemes/origins for browser navigation and media.

Reject:

```text
javascript:
file:
unknown custom protocols
```

unless explicitly supported.

---

# 153. Media download security

Downloads must be authorized server-side.

Do not expose:

```text
bucket credentials
permanent signed URLs
provider API keys
```

---

# 154. Error details security

Normal users see:

```text
what failed
how to fix it
correlation ID
```

Debugging roles may see:

```text
provider request ID
workflow ID
```

Never expose:

```text
stack traces with secrets
headers
tokens
internal network credentials
```

---

# 155. Testing strategy overview

Use:

```text
unit tests
component tests
integration tests
contract tests
E2E browser tests
visual regression
accessibility tests
performance tests
failure/recovery tests
security tests
```

---

# 156. Unit tests — command clients

Test:

```text
correct endpoint
correct payload
correct headers
idempotency
expected-state version
error mapping
```

---

# 157. Unit tests — query hooks

Test:

```text
cache keys
filters
stale state
refetch
error state
invalidation
```

---

# 158. Component tests — status UI

Test all shared states:

```text
RUNNING
QUEUED
PROCESSING
PAUSED
BLOCKED
PASSED
PASSED_WITH_WARNINGS
QUARANTINED
REJECTED
FAILED_RETRYABLE
FAILED_PERMANENT
PUBLISHED
DELIVERED
STALE
UNKNOWN
```

---

# 159. Component tests — confirmation modals

Test:

```text
cancel
confirm
reason required
typed confirmation
loading state
backend rejection
```

---

# 160. Integration tests — exception workflow

Scenario:

```text
case OPEN
→ open detail
→ approve
→ command accepted
→ backend changes state
→ SSE event
→ query invalidation
→ UI shows RESOLVED
```

---

# 161. Integration tests — stale command

Scenario:

```text
Browser A loads state version 7
Browser B changes version to 8
Browser A submits command with 7
```

Expected:

```text
STALE_COMMAND
no mutation
UI prompts refresh
```

---

# 162. Integration tests — SSE outage

Scenario:

```text
SSE connected
→ event
→ network loss
→ polling fallback
→ event stream reconnects
→ state convergence
```

Verify no duplicate activity events.

---

# 163. Integration tests — emergency pause

Scenario:

```text
AUTONOMOUS
→ pause command
→ backend state PAUSED
→ new autonomous commands stop
→ browser refresh
→ still PAUSED
```

---

# 164. E2E — production retry

```text
Production page
→ failed generation
→ retry
→ command accepted
→ live state RUNNING
→ completed
```

---

# 165. E2E — QA review

```text
Exceptions
→ rights/quality case
→ inspect evidence
→ listen to track
→ request refinement
→ confirm
→ live P4 update
```

---

# 166. E2E — YouTube publication

```text
Publishing
→ READY
→ review package
→ confirm scheduled publication
→ state updates
→ published
```

The frontend must never call YouTube directly.

---

# 167. E2E — distribution takedown

```text
Distribution
→ release
→ request takedown
→ impact confirmation
→ command
→ TAKEDOWN_PENDING
→ reconciled
```

---

# 168. E2E — system pause

```text
Home
→ pause autonomous system
→ confirmation
→ paused banner
→ reload
→ paused persists
```

---

# 169. E2E — permissions

Attempt:

```text
Viewer → public publish
```

Expected:

```text
backend denies
UI explains permission
```

Do not rely on hiding the button.

---

# 170. Visual regression

Snapshot:

```text
Home
Exceptions
Production
Quality review
Publishing
Distribution
System Health
Policy diff
```

Use representative desktop layouts.

---

# 171. Accessibility testing

Minimum:

```text
tab order
focus trap in modal
screen reader labels
heading structure
button names
contrast
reduced motion
```

---

# 172. Performance test dataset

Synthetic environment:

```text
1,000 experiments
10,000 candidates
1,000 releases
100,000 metric rows
5,000 live events/hour
```

Frontend should remain responsive because only page-relevant/paginated data is sent to the browser.

---

# 173. Browser performance targets

Initial targets:

```text
authenticated shell < 2s on local/LAN
Home usable < 2s after server response
route navigation < 1s perceived after warm cache
no full-app reload on normal mutation
```

Treat these as targets to measure, not guarantees.

---

# 174. SSE load test

Simulate:

```text
1,000 events/hour
5,000 events/hour
20,000 events/hour
```

Verify:

```text
browser remains responsive
no unbounded event memory growth
query invalidation is batched/debounced where appropriate
```

---

# 175. Event batching

If many events arrive together:

```text
coalesce invalidations
```

Example:

```text
10 generation_completed events
→ one production-summary refetch
```

Do not issue ten identical network requests unnecessarily.

---

# 176. Global refresh behavior

Never implement:

```text
every event → reload entire page
```

Refresh only affected domains.

---

# 177. Offline/disconnected state

If the browser loses network:

```text
show disconnected indicator
preserve currently displayed data
block mutations until reconnect
```

Do not queue privileged/destructive commands in the browser for later execution.

---

# 178. Browser tab duplication

Two tabs may supervise the same system.

Commands must be safe because backend state-version checks protect against stale actions.

The browser should not attempt cross-tab coordination as a correctness requirement.

---

# 179. Session/tab coordination

Optional optimization:

```text
BroadcastChannel
```

for local UI coordination.

Not a source of backend truth.

---

# 180. Data export

Provide explicit export options:

```text
experiment CSV
financial CSV
QA evidence bundle
media manifest
publish manifest
audit export
```

Each export requires authorization.

---

# 181. Export metadata

Show:

```text
scope
period
filters
format
data freshness
```

---

# 182. Export security

Use short-lived download URLs or application streaming.

Do not create permanent public export links for financial or rights data.

---

# 183. Frontend code organization

Recommended:

```text
src/
  app/
  components/
    shell/
    status/
    tables/
    charts/
    forms/
    dialogs/
    media/
    timeline/
  features/
    home/
    market/
    ar/
    experiments/
    production/
    quality/
    media/
    publishing/
    distribution/
    analytics/
    catalog/
    exceptions/
    system/
    audit/
    settings/
  lib/
    api/
    auth/
    events/
    query/
    formatting/
    permissions/
  types/
  hooks/
```

Adapt to an existing codebase where appropriate.

---

# 184. Feature-module rule

Each domain feature should own:

```text
queries
mutations
view models
components
```

Shared components should remain generic.

Avoid a giant `components.tsx` containing the whole application.

---

# 185. Shared UI primitives

Create reusable:

```text
StatusBadge
SeverityBadge
MetricCard
DataTable
EmptyState
ErrorState
ConfirmDialog
CommandButton
ActivityItem
Timeline
EvidencePanel
FreshnessLabel
```

---

# 186. Shared domain display types

Centralize:

```text
status labels
severity labels
phase colors/icons
number formats
time formats
currency formats
```

Do not recreate them separately in every page.

---

# 187. Number formatting

Support:

```text
₩1,234,567
$1,234.56
1.2M views
```

Exact value available on hover/details.

---

# 188. Time formatting

Primary operator timezone can be configured.

Display:

```text
16:25 KST
```

Detailed:

```text
2026-09-20T07:25:00Z
```

---

# 189. Localization

Initial UI languages:

```text
English
Korean
```

The architecture should permit future languages.

Internal IDs/status enums remain stable in English.

---

# 190. Localization rule

Do not translate machine identifiers.

Do translate:

```text
labels
help text
error descriptions
buttons
```

---

# 191. Business terminology glossary

The UI should use consistent terms:

```text
Market Opportunity
Arm
Experiment
Treatment
Candidate
Evaluation
Media Package
Publish Attempt
Release
Store Delivery
Contribution
Forecast
```

Avoid synonyms that confuse users.

---

# 192. Status terminology consistency

Use backend status directly whenever practical.

Example:

```text
QUARANTINED
```

not:

```text
Held
Paused for Review
Pending Approval
```

for the same underlying state.

---

# 193. Human-readable secondary label

You can add:

```text
QUARANTINED
Needs evidence before downstream release.
```

The machine label remains stable.

---

# 194. Frontend feature flags

Use flags for:

```text
public publishing
policy editor
new detector
new provider
new analytics views
new control commands
```

Prefer a centralized feature flag service/config.

---

# 195. Feature-flag safety

Default dangerous features to:

```text
OFF
```

in local/staging.

---

# 196. Preview/experimental UI

For unfinished capabilities:

```text
Beta
```

or feature-flag-hidden.

Do not expose unfinished destructive workflows simply because backend support exists.

---

# 197. Read-only mode

The UI should have an environment/global mode:

```text
READ_ONLY
```

Useful for:

```text
incident response
staging
financial review
maintenance
```

---

# 198. Read-only banner

```text
READ-ONLY MODE
Commands are disabled. Monitoring continues.
```

---

# 199. Operator workflow — normal day

```text
1. Open Home
2. Check system status
3. Check Action Required
4. Resolve true exceptions
5. Inspect recent business results
6. Review system health only if abnormal
7. Leave automation running
```

---

# 200. Operator workflow — incident

```text
1. Alert arrives
2. Open Home
3. Identify affected phase
4. Open System Health
5. Inspect affected job/provider
6. Pause only the affected capability if safe
7. Open exception/evidence
8. Execute bounded repair or escalate
9. Verify recovery
10. Resume capability
```

---

# 201. Operator workflow — policy change

```text
Draft policy
↓
Validate
↓
Diff
↓
Shadow
↓
Review impact
↓
Activate
↓
Monitor
```

---

# 202. Operator workflow — rights exception

```text
Action Required
↓
Open case
↓
Review license/provenance
↓
Listen to artifact if necessary
↓
Approve / reject / request evidence / request refinement
↓
Audit recorded
```

---

# 203. Operator workflow — budget incident

```text
Budget alert
↓
Open Economics/System
↓
Identify source
↓
Inspect reserved vs actual
↓
Pause affected provider/workflow if necessary
↓
Repair/configure
↓
Resume
```

---

# 204. Operator workflow — publication incident

```text
Publishing exception
↓
Open P7 detail
↓
Inspect remote state
↓
Determine whether upload actually occurred
↓
Reconcile
↓
Repair or retry
↓
Verify no duplicate publication
```

---

# 205. No browser-side business decisions

Do not let the frontend decide:

```text
which arm wins
whether a candidate is rights-safe
whether a release is profitable
which provider should be selected
whether a retry is safe
```

It can display backend decisions and request authorized commands.

---

# 206. No UI-only emergency stop

A red button that only sets React state is not a kill switch.

Emergency controls must alter backend execution policy.

---

# 207. No silent automatic intervention from UI

The frontend may not automatically trigger:

```text
retry every failure
publish when ready
approve quarantine
```

based on local browser logic.

---

# 208. No hidden scheduled actions

When a user schedules an action, the UI must show:

```text
when
what
where
current status
```

Do not create “background” browser jobs.

---

# 209. Backend-first command semantics

For every button ask:

```text
What backend command does this invoke?
What state transition occurs?
What audit entry is created?
What happens if the browser dies immediately after clicking?
```

If these questions cannot be answered, the button is not ready for production.

---

# 210. Backend state transition source

UI status should come from:

```text
backend state
```

not:

```text
button click history
```

---

# 211. Reconciliation-first UX

Whenever external state may be ambiguous:

```text
Reconcile
```

should be the first suggested operation.

Example:

```text
Upload response timed out.

Before retrying, reconcile remote YouTube state.
```

---

# 212. Provider status UX

Provider state should include:

```text
healthy
rate-limited
quota-constrained
degra​ded
offline
unknown
```

Do not display only “red/green.”

---

# 213. Operational severity vs business importance

A low-severity event can still have high business impact.

Example:

```text
Provider quota warning
```

may be medium operational severity but block a launch.

UI should allow both:

```text
severity
business impact
```

---

# 214. Exception recommendation pattern

Each recommendation should have:

```text
recommended action
reason
confidence
alternative actions
```

Example:

```text
Recommended: REQUEST_MORE_EVIDENCE
Confidence: 0.88
Alternative: REJECT
```

---

# 215. Human choice transparency

The UI must make it easy to understand what each action will do.

Example:

```text
[Request Refinement]
Creates a new P4 candidate revision and leaves the original artifact unchanged.
```

---

# 216. Audit after action

Immediately after a successful command, show:

```text
Recorded by: Operator
At: 16:26 KST
Reason: Verified license evidence
Command: review.approve
```

---

# 217. Action undo

Do not implement generic Undo.

Some actions are not safely reversible.

Instead provide explicit compensating commands where the backend supports them:

```text
resume
restore
revert policy
cancel scheduled publish
```

---

# 218. Action confirmation timeout

High-risk confirmation modals should expire if the underlying object changes.

On state change:

```text
close/disable confirmation
require re-review
```

---

# 219. UI consistency with backend policy versions

When a policy is changed, relevant UI labels/help should update with the policy version.

Example:

```text
YouTube disclosure policy: v4
```

---

# 220. Documentation links

Every complex policy-related UI can include:

```text
Why is this blocked?
```

→ internal policy explanation/documentation.

Do not rely on undocumented team knowledge.

---

# 221. P1/P2/P3 pages are mostly read-only

These pages should not accidentally acquire destructive buttons just because backend commands exist.

Allowed examples:

```text
inspect
freeze strategy if authorized
pause automation
```

---

# 222. P4 page intervention examples

Allowed:

```text
retry
cancel
inspect
request refinement
```

Not allowed by default:

```text
change the arm's core hypothesis
change experiment allocation
```

Those belong to P2/P3 policy workflows.

---

# 223. P5 page intervention examples

Allowed:

```text
approve
reject
request evidence
request refinement
```

---

# 224. P6 page intervention examples

Allowed:

```text
rebuild media package
rerender
replace approved visual asset
```

only where backend policy permits.

---

# 225. P7 page intervention examples

Allowed:

```text
retry upload
reconcile
reschedule
```

High-risk:

```text
publish
make public
```

---

# 226. P8 page intervention examples

Allowed:

```text
retry submission
reconcile
request correction
```

High-risk:

```text
artist remap
ISRC change
takedown
```

---

# 227. P9 page intervention examples

P9 should be predominantly read-only.

Possible actions:

```text
refresh
backfill
reconcile
export
```

Do not let the analytics page manually change financial facts.

---

# 228. API aggregation endpoints

Recommended convenience endpoints:

```text
GET /api/v1/control-plane/home
GET /api/v1/control-plane/pipeline
GET /api/v1/control-plane/exceptions
GET /api/v1/control-plane/system-health
GET /api/v1/control-plane/activity
```

These reduce frontend orchestration complexity.

---

# 229. Per-domain endpoints

Keep domain APIs consistent with P1–P9 contracts.

The control plane should aggregate where useful but not replace the domain APIs.

---

# 230. Correlation ID propagation

Every client request should send or receive a correlation ID.

Display it on error detail pages.

This should map through:

```text
frontend
→ BFF
→ domain service
→ workflow
→ provider
```

where supported.

---

# 231. Frontend request retry policy

Safe GETs may retry.

Mutations should not blindly auto-retry at the browser layer.

Mutation idempotency belongs to the backend.

---

# 232. Browser-side mutation safety

When a mutation times out:

Do not automatically resend.

Instead:

```text
unknown result
→ reconcile
```

This is especially important for:

```text
publishing
distribution
takedown
```

---

# 233. Event ordering

Events may arrive out of order.

The frontend should not assume:

```text
received_at order = business order
```

Backend state remains authoritative.

---

# 234. Event sequence numbers

Where feasible, event stream should include:

```text
sequence
or
cursor
```

so reconnection can detect missed events.

---

# 235. Activity feed ordering

Prefer backend event timestamp/sequence.

If a delayed event arrives:

```text
show actual event time
```

not browser receipt time as the event time.

---

# 236. Live-progress model

Three progress types:

```text
DETERMINATE
STAGED
INDETERMINATE
```

Examples:

```text
P4 7/8 = DETERMINATE
P8 3/5 stores = STAGED
YouTube processing = INDETERMINATE
```

---

# 237. Progress color/style

Use consistent styling for:

```text
known progress
waiting
paused
blocked
```

Do not make blocked look like “slow progress.”

---

# 238. Job age

Show:

```text
running for 7m
```

and optionally:

```text
longer than expected
```

only if backend has an expected-duration model.

---

# 239. Queue age alerts

System health should flag:

```text
oldest queue item > configured threshold
```

Do not embed hard-coded thresholds into React components.

---

# 240. Browser notifications and sound

Optional for:

```text
critical action required
```

Must be user configurable.

---

# 241. Frontend accessibility acceptance

All core tasks must work without a mouse:

```text
navigate
open exception
play/pause audio
submit review
confirm action
```

---

# 242. Internationalization acceptance

At minimum test:

```text
Korean UI
English UI
Korean/Japanese/Chinese title text
long artist names
Unicode symbols
```

---

# 243. Long-text behavior

Provider/LLM output may be enormous.

Use:

```text
collapsed sections
copy button
search within text
line numbers for technical payloads
```

Do not render massive JSON all at once by default.

---

# 244. JSON viewer

For technical evidence:

```text
JSON tree
collapse/expand
copy path
copy value
```

This is more useful than plain `<pre>` blocks.

---

# 245. Technical log viewer

Support:

```text
filter by level
search
copy correlation ID
expand event
```

Avoid streaming huge logs to the browser.

---

# 246. Audit filtering

Useful filters:

```text
actor
command
phase
entity
date
success/failure
```

---

# 247. Audit export

Export should include:

```text
filter scope
export time
data freshness
```

---

# 248. System health drill-down

Click:

```text
P7 degraded
```

→

```text
YouTube API health
quota
last failure
queue
last successful publish
```

---

# 249. Provider health drill-down

Click provider:

```text
Suno
```

→

```text
health
capabilities
credit usage
last success
last failures
current active jobs
```

No credentials.

---

# 250. Operational dependency map

System page may display:

```text
P1 → P2 → P3 → P4 → P5 → P6 → P7/P8 → P9
```

with current degradation indicators.

This should be a read-only systems view, not an interactive workflow editor.

---

# 251. Incident banner

For critical system incidents:

```text
CRITICAL SYSTEM INCIDENT

Public publishing paused because YouTube authentication is invalid.

[Open incident]
```

---

# 252. Incident detail

Show:

```text
start time
affected services
current mitigation
commands executed
recommended next action
```

---

# 253. Incident acknowledgment

Allow:

```text
Acknowledge
```

but acknowledgment does not resolve the incident.

Backend status controls actual resolution.

---

# 254. System pause reason

Always require/record reason for manual pause.

Example:

```text
Paused by operator
Reason: Provider quota anomaly
```

---

# 255. Resume workflow

Resume should show:

```text
What is currently blocked
What will resume
Any unresolved exceptions
```

Then confirm.

---

# 256. Readiness pages

Provide readiness summaries for:

```text
Public YouTube publishing
DSP distribution
Autonomous generation
Autonomous A&R
```

Each should show:

```text
PASS/FAIL
blocking reason
checks
```

---

# 257. Example readiness card

```text
AUTONOMOUS YOUTUBE PUBLISHING

READY

OAuth        ✓
API audit    ✓
Quota        ✓
Upload test  ✓
Thumbnail    ✓
Playlist     ✓
Reconcile    ✓
```

---

# 258. Frontend implementation phases

## UI-1 — Shell

```text
Next.js
layout
navigation
auth
status bar
```

## UI-2 — API clients

```text
typed clients
errors
auth
correlation IDs
```

## UI-3 — Home

```text
system
pipeline
exceptions
activity
business snapshot
```

## UI-4 — SSE

```text
stream
reconnect
fallback
invalidation
```

## UI-5 — Exceptions

```text
inbox
detail
review
commands
audit
```

## UI-6 — Operations

```text
production
quality
media
publishing
distribution
```

## UI-7 — Intelligence

```text
market
A&R
experiments
```

## UI-8 — Analytics/Catalog

```text
analytics
economics
catalog
```

## UI-9 — Governance

```text
system
audit
settings
policy
budget
```

## UI-10 — Hardening

```text
permissions
stale state
emergency pause
destructive confirmations
```

## UI-11 — Test/QA

```text
E2E
visual regression
accessibility
performance
security
```

---

# 259. MVP page order

Implement first:

```text
1. Home
2. Exceptions
3. Production
4. Quality
5. Publishing
6. Distribution
7. System Health
```

Then:

```text
8. Experiments
9. Analytics
10. Market
11. A&R
12. Catalog
13. Audit
14. Settings
```

This order maximizes immediate operational value.

---

# 260. MVP backend prerequisites

Before the frontend becomes useful, backend should expose:

```text
home summary
phase status
exceptions
commands
activity events
job detail
system health
```

The frontend should not attempt to reconstruct these from multiple undocumented endpoints.

---

# 261. P1 frontend API dependency

Required:

```text
market opportunities
market runs
source health
```

---

# 262. P2 frontend API dependency

Required:

```text
allocation cycle
arm posterior summary
directives
policy
```

---

# 263. P3 frontend API dependency

Required:

```text
experiments
treatments
content genomes
lineage
integrity status
```

---

# 264. P4 frontend API dependency

Required:

```text
generation plans
attempts
candidates
cost
provider health
artifacts
```

---

# 265. P5 frontend API dependency

Required:

```text
evaluations
findings
evidence
review cases
dispositions
```

---

# 266. P6 frontend API dependency

Required:

```text
media packages
render jobs
thumbnail/artwork candidates
manifests
```

---

# 267. P7 frontend API dependency

Required:

```text
publish attempts
upload progress
remote state
schedule
thumbnail
playlist
```

---

# 268. P8 frontend API dependency

Required:

```text
distribution releases
store deliveries
rejections
corrections
takedowns
```

---

# 269. P9 frontend API dependency

Required:

```text
analytics summaries
financial summaries
experiment outcomes
catalog economics
data quality
```

---

# 270. Cross-phase lineage API

A single endpoint is highly useful:

```text
GET /api/v1/control-plane/lineage/{entityType}/{entityId}
```

Return the chain:

```text
P1
→ P2
→ P3
→ P4
→ P5
→ P6
→ P7/P8
→ P9
```

where applicable.

---

# 271. Why lineage deserves a dedicated endpoint

The frontend should not perform eight calls to construct one lineage view.

Backend should provide a compact graph/read model.

---

# 272. Lineage UI

Use a horizontal/vertical flow:

```text
Market Arm
   ↓
A&R Decision
   ↓
Experiment
   ↓
Candidate
   ↓
QA
   ↓
Media
   ↓
YouTube / DSP
   ↓
Revenue
```

Click any node to open its canonical page.

---

# 273. Entity status summary

Every object detail page should have a common header:

```text
Name
ID
Status
Brand
Experiment
Created
Updated
```

---

# 274. Object context ribbon

Example:

```text
EXPERIMENT EXP-123
Arm: R&B / Late Night Drive
Brand: NEON SEOUL
Status: Measuring
```

This maintains context while navigating.

---

# 275. Breadcrumbs

Use:

```text
Home / Experiments / EXP-123 / Track / TRK-456
```

---

# 276. Browser-title metadata

Set useful titles:

```text
EXP-123 — Experiments
REL-1042 — Distribution
VID-88 — YouTube Publishing
```

---

# 277. Shareable URLs

Every detail view should work from a copied URL when the user is authorized.

---

# 278. Deep-link permission behavior

If unauthorized:

```text
403 / Access denied
```

Do not leak object existence details beyond what policy permits.

---

# 279. Data access filtering

If future multiple brands/channels exist:

```text
selected scope
```

must be applied at backend authorization level.

Frontend filters are convenience only.

---

# 280. Brand selector

Global header should support:

```text
All brands
Brand A
Brand B
```

if backend supports multiple brands.

---

# 281. Channel selector

Similarly:

```text
All channels
Channel A
Channel B
```

The selected scope should affect page queries and query keys.

---

# 282. Scope persistence

Persist non-sensitive UI scope:

```text
last selected brand
last selected channel
```

Do not persist privileged commands or secrets in localStorage.

---

# 283. Local storage rule

Allowed:

```text
UI preferences
collapsed sections
table column preferences
```

Avoid persisting sensitive server data unnecessarily.

---

# 284. Browser cache rule

Do not cache:

```text
financial statements
rights evidence
provider credentials
private media
```

beyond necessary authenticated browser semantics.

---

# 285. Accessibility — media player

Audio player controls must have:

```text
accessible names
keyboard operation
focus states
current time announcement where appropriate
```

---

# 286. Accessibility — charts

Provide text summary for charts so non-visual users can access key data.

---

# 287. Accessibility — tables

Use:

```text
proper headers
sort indicators
row actions with accessible labels
```

---

# 288. Accessibility — dialogs

Dialogs must:

```text
trap focus
restore focus
have title
have clear cancel
```

---

# 289. Security — destructive command UI

Never hide the command token/confirmation in frontend code.

The backend must generate or validate any dangerous action token.

---

# 290. Security — CSRF

Use the chosen authentication architecture's correct CSRF protection for cookie-based sessions.

Do not invent an insecure custom pattern.

---

# 291. Security — rate limiting

High-impact UI commands should be rate-limited server-side.

Example:

```text
takedown
public publish
policy activation
```

---

# 292. Security — command authorization

Authorization must be checked at execution time, not only page load.

---

# 293. Security — object access

Access to:

```text
media
financials
rights evidence
```

must be authorized per request.

---

# 294. Testing — security

Test:

```text
unauthenticated route
unauthorized command
cross-brand access
expired session
CSRF
XSS
malicious URL
malicious file
```

---

# 295. Testing — API contract

The frontend CI should run against a mocked or test backend generated from the same API schema.

Any contract mismatch should fail CI.

---

# 296. Testing — browser E2E environment

Provide a deterministic fixture backend containing:

```text
normal jobs
failed jobs
exceptions
published video
partial distribution
financial data
system pause
```

---

# 297. Testing — visual golden states

Create deterministic snapshots for:

```text
healthy
warning
paused
critical
empty
loading
partial
error
```

---

# 298. Testing — no-data cases

Every page must have an intentional empty state.

Examples:

```text
No active exceptions.

No DSP deliveries yet.

No current experiments.
```

---

# 299. Testing — degraded source

Home fixture:

```text
YouTube analytics unavailable
```

while:

```text
P4 healthy
P5 healthy
P8 healthy
```

The UI should preserve unaffected areas.

---

# 300. Testing — browser refresh

Refreshing any detail page must recover state from backend.

No important workflow status should depend on browser memory.

---

# 301. Testing — browser crash/reopen

After reopening the UI:

```text
open same URL
→ backend state restored
```

---

# 302. Testing — two-tab behavior

Simulate two tabs performing conflicting actions.

Backend state-version checks must protect correctness.

---

# 303. Testing — event replay

Reconnect SSE with a previous `Last-Event-ID`.

Verify:

```text
no missing important state
no duplicate activity
```

---

# 304. Deployment architecture

The control plane should be deployable as:

```text
Next.js app
```

plus backend API.

Possible deployment modes:

```text
Vercel + backend
self-hosted Docker
local Windows server
```

The frontend should not require cloud-only assumptions.

---

# 305. Local development

Provide:

```text
npm/pnpm dev
```

with environment variables for:

```text
backend URL
auth
SSE URL
environment
feature flags
```

---

# 306. Staging deployment

Staging should use:

```text
synthetic/test provider credentials where possible
public publishing disabled
DSP production submission disabled
```

---

# 307. Production deployment

Production requires:

```text
HTTPS
secure session cookies
backend authorization
monitoring
error tracking
feature flags
backup/recovery
```

---

# 308. Build pipeline

CI should run:

```text
typecheck
lint
unit tests
component tests
build
E2E smoke
accessibility checks
```

---

# 309. Preview deployments

Useful for:

```text
UI changes
```

But dangerous commands must remain disabled in preview environments.

---

# 310. Frontend environment health

On startup expose:

```text
backend reachable
API schema compatible
SSE reachable
auth configured
```

If not:

```text
UI starts in degraded/read-only state
```

where safe.

---

# 311. API compatibility check

At build or startup:

```text
frontend expected API version
backend supported API version
```

Mismatch should be visible before operations fail mysteriously.

---

# 312. Release strategy

Use:

```text
feature branch
→ CI
→ preview
→ staging
→ production
```

High-risk command changes require additional E2E validation.

---

# 313. Rollback

The frontend should support deployment rollback without invalidating backend state.

Because commands are versioned and backend-authoritative, an older frontend should fail gracefully if it encounters unsupported states.

---

# 314. Forward compatibility

Unknown backend status should render as:

```text
UNKNOWN
```

with diagnostic context.

Do not crash because a new enum value appears.

---

# 315. Backward compatibility

If backend adds optional fields, older frontend must continue working.

---

# 316. Frontend version display

System page should show:

```text
Frontend version
Backend version
API version
```

This is important during incidents.

---

# 317. Control-plane version

The entire UI can expose:

```text
Control Plane v0.x.y
```

in a subtle footer or System page.

---

# 318. Incident support bundle

Allow authorized operator to export:

```text
correlation IDs
system status
active exceptions
recent relevant events
software/API versions
```

without secrets.

---

# 319. Support bundle safety

Never include:

```text
OAuth tokens
API keys
full private media
private statements
```

unless explicitly selected through a separate authorized export path.

---

# 320. Operator onboarding

First-run should explain:

```text
Home
Exceptions
Pause controls
System health
```

Do not create a long tutorial before the operator can see the dashboard.

---

# 321. Tooltips

Use concise tooltips for:

```text
market score
posterior
confidence
maturity
attribution
forecast
```

---

# 322. Glossary drawer

A global glossary can explain system terms without sending users to separate documentation.

---

# 323. Operator training mode

Optional later:

```text
simulation mode
```

where commands hit a sandbox and show what would happen.

This is useful for practicing high-risk workflows.

---

# 324. Simulation confirmation UI

Example:

```text
SIMULATION

This will NOT publish to YouTube.

Simulated result:
PUBLISH → WOULD_BE_QUEUED
```

---

# 325. Why simulation matters

The operator should be able to learn the control plane without risking production actions.

---

# 326. User preference settings

Allow:

```text
theme
language
timezone
compact/dense layout
notification preference
```

Do not allow:

```text
bypass safety gates
```

---

# 327. Theme

Light/dark may be supported.

Do not make system status ambiguous in either theme.

---

# 328. Dense layout

Because this is an operations console, offer:

```text
Comfortable
Dense
```

Especially useful for tables and queue monitoring.

---

# 329. Notification preference

Allow the operator to choose:

```text
critical only
critical + high
all actionable
```

Backend remains the source of what is actionable.

---

# 330. Final control philosophy

The UI should answer:

```text
WHAT IS HAPPENING?
WHY?
WHAT NEEDS ME?
WHAT HAPPENS IF I CLICK THIS?
```

It should not force the human to answer:

```text
WHAT GENRE SHOULD I MAKE?
WHAT PROMPT SHOULD I USE?
WHAT FILE SHOULD I UPLOAD?
```

Those are precisely the decisions the autonomous architecture is designed to remove.

---

# 331. Acceptance criteria — core architecture

## AC-01

Frontend never connects directly to PostgreSQL.

## AC-02

Frontend never calls Suno/YouTube/DSP provider APIs directly.

## AC-03

All mutations use explicit backend commands.

## AC-04

All privileged mutations are server-authorized.

## AC-05

Stale state cannot be overwritten.

---

# 332. Acceptance criteria — live system

## AC-06

Home displays P1–P9 operational state.

## AC-07

Live events appear without full-page reload.

## AC-08

SSE failure falls back to polling.

## AC-09

Reconnection converges to backend truth.

## AC-10

No fabricated numeric progress is displayed.

---

# 333. Acceptance criteria — exceptions

## AC-11

All `human_required = true` cases appear in Action Required.

## AC-12

Each case explains why automation stopped.

## AC-13

Human actions create durable backend commands.

## AC-14

Every action creates an audit record.

## AC-15

High-impact actions require configured confirmation.

---

# 334. Acceptance criteria — safety

## AC-16

Public publishing can be globally disabled from backend control state.

## AC-17

Emergency pause persists through browser reload.

## AC-18

High-risk distribution actions require confirmation.

## AC-19

Provider secrets never reach the browser.

## AC-20

Sensitive media is access-controlled.

---

# 335. Acceptance criteria — domain coverage

## AC-21

P1 opportunities visible.

## AC-22

P2 decisions visible.

## AC-23

P3 experiment lineage visible.

## AC-24

P4 production visible.

## AC-25

P5 evaluation/review visible.

## AC-26

P6 media visible.

## AC-27

P7 publishing visible.

## AC-28

P8 distribution visible.

## AC-29

P9 analytics/economics visible.

---

# 336. Acceptance criteria — investigation

## AC-30

An operator can trace any major asset end-to-end without using a terminal.

## AC-31

Technical correlation IDs are visible.

## AC-32

Relevant provider/job IDs are visible.

## AC-33

Evidence can be exported with authorization.

---

# 337. Acceptance criteria — UX

## AC-34

All core flows work with keyboard only.

## AC-35

Critical states do not rely on color alone.

## AC-36

Loading/empty/error/partial states are intentionally designed.

## AC-37

The UI does not require manual database/log inspection for routine operation.

---

# 338. Acceptance criteria — reliability

## AC-38

Duplicate commands are prevented.

## AC-39

Two-tab race conditions are safe.

## AC-40

Browser refresh restores correct state.

## AC-41

SSE reconnect does not duplicate actions/events.

## AC-42

A timed-out mutation requires reconciliation before safe retry.

---

# 339. Acceptance criteria — deployment

## AC-43

Local environment works without cloud-only dependencies.

## AC-44

Staging disables dangerous production commands.

## AC-45

Production readiness is visible.

## AC-46

Frontend/backend/API versions are inspectable.

---

# 340. Recommended implementation checklist

Build in this order:

```text
[ ] auth/session
[ ] application shell
[ ] shared status system
[ ] typed API client
[ ] Home read model
[ ] exceptions read model
[ ] command API
[ ] audit integration
[ ] SSE stream
[ ] production page
[ ] quality page
[ ] publishing page
[ ] distribution page
[ ] system page
[ ] experiments
[ ] market/A&R
[ ] analytics/catalog
[ ] settings/policy/budget
[ ] global search
[ ] lineage
[ ] accessibility
[ ] E2E
[ ] visual regression
[ ] security hardening
```

---

# 341. What the implementation AI may decide

The coding AI may choose:

- exact Next.js minor/patch version compatible with the repository;
- exact component library;
- exact chart library;
- exact table library;
- exact icon library;
- exact form library;
- exact TanStack Query configuration;
- exact SSE client implementation;
- exact OpenAPI-codegen tool;
- exact route grouping;
- exact CSS architecture;
- exact error-tracking product;
- exact auth integration, provided it matches the overall infrastructure;
- exact dashboard composition within the stated information hierarchy.

It may make the UI more polished or efficient without changing the safety/control semantics.

---

# 342. What the implementation AI must not silently change

Do not silently change:

- control-plane-vs-orchestrator separation;
- backend source-of-truth boundary;
- explicit command architecture;
- audit requirements;
- stale-state protection;
- emergency pause semantics;
- public publishing safety;
- rights/QA authority in P5;
- publication authority in P7;
- distribution authority in P8;
- economics authority in P9;
- P1/P2/P3 decision boundaries;
- provider-secret isolation;
- human-review semantics;
- observed/estimated/modelled distinctions;
- idempotency expectations;
- reconciliation-first behavior;
- destructive-action confirmation rules.

If implementation requires changing one of these, record the architectural decision and update this document before the change is treated as complete.

---

# 343. What should remain flexible

Do not over-specify:

```text
exact colors
exact chart style
exact card radius
exact animation
exact component library
exact API route names if existing conventions differ
```

The product should have a coherent visual identity, but implementation details may evolve.

---

# 344. Avoid overengineering

Do not initially build:

```text
BPMN/workflow editor
custom BI engine
mobile-native application
WebSocket infrastructure without need
full multi-tenant enterprise IAM
complex collaborative editing
custom desktop app
```

The first version should be a strong operator control plane, not a new platform inside the platform.

---

# 345. Suggested first end-to-end milestone

The first truly useful release should let the operator:

```text
open Home
→ see P1–P9 state
→ see exceptions
→ open an exception
→ inspect evidence
→ submit a human decision
→ watch the backend process it live
→ see audit result
→ return Home
```

This proves the control-plane concept before building every analytical page.

---

# 346. Suggested second milestone

Add:

```text
P4 production monitoring
P5 QA review
P7 publishing monitoring
P8 distribution monitoring
```

The operator can now supervise the majority of the real-world operational loop.

---

# 347. Suggested third milestone

Add:

```text
P1 market
P2 A&R
P3 experiments
P9 analytics
Catalog
```

Now the same application covers both:

```text
operations
```

and:

```text
strategy/learning
```

---

# 348. Suggested fourth milestone

Add:

```text
policy editor
budget controls
emergency controls
advanced audit
simulation mode
```

This completes governance.

---

# 349. Human-intervention coverage test

Before declaring the control plane complete, enumerate every backend condition currently marked:

```text
human_required = true
```

For each one verify:

```text
case appears in UI
case has detail page
case explains reason
case exposes permitted action
case has confirmation level
case creates audit event
case receives live result
```

This should become a generated test matrix where possible.

---

# 350. Systemwide intervention principle

A user should never have to ask:

> "What exactly am I supposed to do here?"

The exception UI should make the decision surface obvious.

Example:

```text
PROBLEM
Artist identity ambiguous.

WHY AUTOMATION STOPPED
Avoid accidental delivery to another artist page.

EVIDENCE
2 candidate artist mappings.

RECOMMENDED ACTION
Review identity.

OPTIONS
Approve mapping A
Approve mapping B
Keep unresolved
```

---

# 351. Ultimate definition of done

The Control Plane is complete when one authenticated operator can open a single browser application and answer, without opening a terminal or individual provider dashboard:

```text
Is the autonomous business healthy?
What is every phase doing?
What needs me?
Why does it need me?
What happens if I approve/reject/retry?
How much are we spending?
What was published/distributed?
What is earning money?
Can I pause the system safely?
Can I trace any asset back to its experiment and market hypothesis?
```

And when the operator takes an action, the system must:

```text
authorize it
validate state
execute it in the backend
show live progress
persist the outcome
record the audit trail
```

The desired end-state is:

> **an aircraft cockpit for the autonomous music business — high visibility, deliberate controls, minimal routine human work, and no ambiguity about what the system is doing or why.**

---

# 352. Decisions I would lock before coding the UI

These are not blockers for the architecture above, but they are the remaining product decisions worth explicitly locking before implementation:

1. **Deployment target:** local Windows-only first, self-hosted Docker, Vercel, or hybrid.
2. **Authentication:** single-user local authentication now, or production-grade identity from day one.
3. **Scope model:** one brand/one YouTube channel initially, or multi-brand/multi-channel from the first release.
4. **Operator language:** English only initially, or English + Korean from v1.
5. **Live updates:** SSE-only initially, or an event broker already exists in the backend.
6. **Media access:** authenticated streaming endpoint vs short-lived signed URLs.
7. **How aggressive should the UI be with destructive controls:** operator can publish/takedown directly after confirmation, or high-risk actions always require a second review step.
8. **Whether you want a “simulation mode” exposed in the UI from the first version.**
9. **Whether the Home page should be the primary daily cockpit or whether you want a more analytics-heavy financial dashboard as the default landing screen.**

The architecture does not need to be rewritten based on these answers; they mainly determine the first implementation configuration and UX priority.

---

# 353. Final architectural invariant

Keep this invariant throughout development:

```text
THE AI RUNS THE BUSINESS.

THE UI SUPERVISES THE AI.

THE BACKEND ENFORCES THE RULES.

THE DATABASE/EVENT LEDGER PRESERVES THE TRUTH.
```

That is the role of the frontend in this system.
