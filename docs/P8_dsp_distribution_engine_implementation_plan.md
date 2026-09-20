# P8 — DSP / Music Distribution Engine
## Detailed Implementation Plan

**Document status:** Implementation-ready specification  
**Phase:** P8 of the Autonomous AI Music Media Engine  
**Primary objective:** Automatically deliver P5-cleared/P6-packaged music releases to approved DSPs and stores through a provider-neutral distribution control plane, track delivery state, reconcile remote status, manage corrections/takedowns/redeliveries, and preserve complete release/rights lineage.

---

# 1. Purpose

P8 is the system's **music-delivery control plane**.

P4 creates music.  
P5 determines whether it is technically/rights/provenance eligible for release.  
P6 packages the media.  
P7 publishes YouTube content.  

P8 takes the cleared recording/release package and turns it into a **properly identified, metadata-complete, distributor-submitted release** with durable tracking through downstream store ingestion.

The system must answer:

> "What release are we delivering, to which stores/territories, under which artist identity, using which rights metadata, with which files, under which version, and what is the latest verified delivery state?"

P8 is not merely an HTTP client for one distributor. It must be a durable orchestration layer that can survive provider changes, store rejections, metadata corrections, partial delivery, API outages, duplicate requests, and takedown/re-release workflows.

---

# 2. Scope

## 2.1 In scope

P8 must implement:

1. P5/P6 release-manifest ingestion.
2. Release identity validation.
3. Artist identity management.
4. Track/release metadata normalization.
5. Territory and platform policy mapping.
6. ISRC/UPC/other identifier handling.
7. Audio/artwork/lyrics/credits package validation at the distribution boundary.
8. Distributor-provider abstraction.
9. Release submission workflow.
10. Release update/correction workflow.
11. Release takedown workflow.
12. Release re-delivery/resubmission workflow.
13. Pre-save/future release scheduling metadata where supported.
14. Provider response persistence.
15. Remote-state polling/reconciliation.
16. Partial-delivery handling.
17. Rejection classification.
18. Retry and backoff.
19. Idempotency and duplicate-submission prevention.
20. Provider/account capability management.
21. Delivery evidence and provenance.
22. Distribution status API for P9 and the operator dashboard.
23. Raw report/event ingestion where provider supports it.
24. Cost accounting for distribution services.
25. Security/secrets handling.
26. Audit logs.
27. Automated alerts and human exception queues.
28. Full automated test coverage for normal and failure cases.

## 2.2 Explicitly out of scope

Do not implement in P8:

- YouTube channel publishing; P7 owns this.
- YouTube long-form video assembly; P6 owns this.
- Music generation; P4 owns this.
- Music quality judgment; P5 owns this.
- Market research; P1 owns this.
- A&R/bandit decisions; P2 owns this.
- Content-experiment design; P3 owns this.
- General business intelligence / revenue optimization; P9 owns this.
- Artificial-stream generation or listener manipulation.
- Paid services promising guaranteed streams or playlist placement.
- Scraping private artist analytics where no authorized API exists.

P8 may ingest distribution-status and royalty/report files when an authorized distributor exposes them, but P9 remains the canonical analytics/economics layer.

---

# 3. Core architectural principle

Use a **distribution control plane** rather than direct coupling to any one distributor.

```text
P5 cleared release
       ↓
P6 publishable media package
       ↓
P8 Release Compiler
       ↓
Canonical Release Package
       ↓
Policy / Identity / Metadata Validation
       ↓
Distributor Adapter
       ↓
Distributor
       ↓
DSPs / Stores
       ↓
Delivery Status / Reports
       ↓
P8 Reconciliation
       ↓
P9 Analytics / Economics
```

The system should be able to change distributors without changing:

- release identity;
- artist identity;
- canonical metadata;
- internal release lifecycle;
- experiment linkage;
- provenance model;
- P9 interfaces.

Only the adapter/provider-specific mapping should change.

---

# 4. Current platform constraints that affect architecture

Music distribution is not equivalent to using a consumer-facing streaming-service API. Spotify's developer platform has undergone major API and access changes in 2026, including tighter Development Mode limits and endpoint changes. Spotify explicitly states that Development Mode is intended for experimentation/personal projects and should not be relied on as the foundation for scaling a commercial business. Extended Quota Mode has different treatment. citeturn513289search0turn513289search15

Therefore:

**Do not design P8 around Spotify's consumer Web API as a music-upload mechanism.**

Spotify's Web API is for supported application/catalog functionality; music delivery should be performed through an authorized distributor/label delivery path.

Similarly, YouTube Music Art Tracks are associated with music-label/distributor delivery mechanisms using the DDEX ERN standard or supported bulk-delivery processes, rather than ordinary YouTube Data API video uploads. citeturn513289search6turn513289search9

For Apple Music, Apple directs independent artists to approved distributors, and distributor choice affects features such as lyrics, credits, analytics, motion artwork and immersive-audio delivery. citeturn513289search3turn513289search5

**Implementation consequence:** P8 must support distributor-driven delivery and must not assume every DSP exposes a public upload API.

---

# 5. Provider strategy

## 5.1 Provider-neutral interface

Implement a common provider interface:

```text
validate_capabilities()
get_account_status()
create_release()
submit_release()
get_release()
list_release_events()
update_release()
request_takedown()
restore_release() if supported
retry_release()
get_release_status()
get_delivery_targets()
get_rejection_details()
get_reports()
```

Not every provider must support every operation.

The capability model must expose support explicitly:

```text
CAP_CREATE_RELEASE
CAP_SUBMIT_RELEASE
CAP_UPDATE_METADATA
CAP_AUDIO_REPLACEMENT
CAP_ARTWORK_REPLACEMENT
CAP_TAKEDOWN
CAP_REDELIVERY
CAP_SCHEDULED_RELEASE
CAP_LYRICS
CAP_CREDITS
CAP_TERRITORY_CONTROL
CAP_ANALYTICS
CAP_ROYALTY_REPORTS
CAP_WEBHOOKS
CAP_POLLING
CAP_ARTIST_MAPPING
```

Unsupported capabilities must produce an explicit provider capability error. Do not silently emulate destructive operations.

## 5.2 Preferred integration order

1. Official provider API.
2. Official provider file-transfer or DDEX interface.
3. Provider-supported import/export workflow.
4. Manual operator exception queue only as final fallback.

Do not reverse-engineer private endpoints or automate undocumented account internals for production.

---

# 6. Canonical release model

P8 needs a canonical internal release model independent of any distributor.

## `distribution_releases`

Required fields:

```text
id
release_id
artist_id
release_type
release_title
release_version
release_date
original_release_date
label_name
copyright_line
publisher_line
primary_language
explicit_status
territories_policy
status
created_at
updated_at
```

`release_type` examples:

```text
single
EP
album
compilation
```

`status` examples:

```text
DRAFT
READY_FOR_DELIVERY
VALIDATING
SUBMISSION_PENDING
SUBMITTED
PROCESSING
PARTIALLY_DELIVERED
DELIVERED
REJECTED
TAKEDOWN_PENDING
TAKEN_DOWN
RESUBMISSION_REQUIRED
CANCELLED
```

---

# 7. Canonical track model

## `distribution_tracks`

Fields:

```text
id
track_id
release_id
track_number
disc_number
title
version_title
isrc
isrc_source
language
explicit_status
lyrics_status
instrumental_flag
primary_artists
featured_artists
remixer_artists
producer_credits
composer_credits
lyricist_credits
audio_asset_id
created_at
updated_at
```

Do not assume an ISRC is always internally generated. The source may be:

```text
EXISTING_PROVIDER_ASSIGNED
INTERNAL_ALLOCATION
USER_SUPPLIED
UNKNOWN
```

If an authoritative provider identifier is assigned later, update the mapping while retaining the earlier lifecycle event.

Never silently regenerate an ISRC for an existing recording during a metadata correction.

---

# 8. Release identifiers

P8 must distinguish at least:

```text
ISRC = recording identifier
UPC/EAN/GRid = release/product identifier where applicable
internal_release_id = platform-independent internal ID
provider_release_id = distributor-specific ID
store_release_id = store-specific external ID
```

A release can have multiple external identifiers.

Store them as mappings rather than copying IDs into ad hoc fields.

## `external_release_identifiers`

```text
id
release_id
identifier_type
value
namespace
provider_id
store_id
is_primary
observed_at
source
```

---

# 9. Artist identity model

AI-generated music may be published under fictional/virtual artist identities. The system must therefore treat artist identity as a first-class object.

## `artists`

```text
id
canonical_name
normalized_name
artist_type
brand_id
status
created_at
updated_at
```

`artist_type` examples:

```text
human
virtual
project
brand
ensemble
```

## `artist_platform_mappings`

```text
artist_id
provider_id
store_id
external_artist_id
external_artist_url
mapping_status
verification_status
created_at
updated_at
```

Never assume that the same artist name represents the same store identity.

Artist-name collisions must be treated as a normal operational risk.

---

# 10. Artist identity safety rules

Before submission:

1. Confirm the internal artist identity is intentional.
2. Verify that required provider/store artist mapping is available if the provider requires it.
3. Flag possible collisions with existing artist names if the provider exposes collision/search information.
4. Do not automatically attach a release to another artist merely because the display name matches.
5. Require manual review when identity matching is ambiguous.

A false artist mapping can cause a release to appear on another artist's page and is therefore a hard-stop condition.

---

# 11. Release compiler

P8 must compile the P5/P6 outputs into one immutable `CanonicalReleasePackage`.

Example:

```json
{
  "release_id": "rel_123",
  "release_version": 3,
  "artist": {
    "artist_id": "artist_001",
    "name": "NEON SEOUL"
  },
  "release": {
    "title": "After Midnight",
    "type": "single",
    "release_date": "2026-10-15",
    "explicit": false
  },
  "tracks": [
    {
      "track_id": "trk_001",
      "title": "After Midnight",
      "isrc": "...",
      "audio_asset_id": "asset_abc"
    }
  ],
  "artwork_asset_id": "art_001",
  "lyrics_asset_ids": [],
  "credits": [],
  "territories": "WORLDWIDE",
  "source_manifest_ids": ["p5_manifest_123", "p6_manifest_456"]
}
```

This package becomes the immutable source snapshot for the specific distribution submission.

If metadata changes later, create a new release-package version.

---

# 12. Distribution package validation

Before any provider submission, validate:

## Identity

```text
artist identity valid
release identity valid
track identity valid
ISRC format valid where present
release identifier valid where present
```

## Metadata

```text
title present
artist names present
track ordering valid
release type valid
language valid where required
explicit status valid
release date valid
copyright/publishing fields valid
```

## Media

```text
audio exists
artwork exists
artwork dimensions/format meet provider rules
lyrics are internally consistent if included
```

## Rights

```text
P5 release gate = cleared
commercial-use eligibility = known/cleared
territory rights = known
voice/samples risks cleared
```

## Experiment integrity

Distribution metadata must not alter the musical/content variables that P3/P2 are measuring.

---

# 13. Metadata normalization

Use canonical metadata internally and provider-specific transformations externally.

Never allow provider-specific formatting rules to pollute the canonical model.

Example:

```text
canonical:
explicit = false

provider A:
explicit_flag = "No"

provider B:
explicit_flag = false

provider C:
parental_advisory = "NONE"
```

The adapter maps these representations.

---

# 14. Credits model

Credits should be structured rather than one free-form string.

Examples:

```text
composer
lyricist
producer
performer
featured_artist
remixer
engineer
publisher
label
```

Every credit should have:

```text
person/entity ID if known
name
role
sequence
provenance
```

Do not fabricate human credits merely because a provider requires a populated field.

If a downstream store requires metadata that cannot be legitimately supplied, quarantine the release and surface an actionable exception.

---

# 15. Lyrics model

Lyrics handling must support:

```text
instrumental
original lyrics
no-lyrics
lyrics pending
lyrics excluded
```

Never automatically manufacture lyricist identities.

If lyrics are generated by an AI system, retain their provenance internally even when the public DSP metadata only exposes standard songwriter/lyric fields.

P8 does not determine copyright ownership of AI-only lyrics; P5's rights/provenance gate remains authoritative.

---

# 16. Territory model

Represent territories as a policy object, not an unstructured comma-separated string.

Examples:

```text
WORLDWIDE
INCLUDE(list)
EXCLUDE(list)
PROVIDER_DEFAULT
```

Resolve the policy against the distributor's supported representation.

Do not assume all stores support identical territory granularity.

If a provider cannot represent the canonical territory policy without unintended over-distribution, block submission.

---

# 17. Release timing

Support:

```text
ASAP
SPECIFIC_DATE
SPECIFIC_DATETIME if provider supports it
```

Before submission verify:

```text
release date is in accepted range
local timezone rules are understood
provider lead-time requirements are met if documented
```

Do not promise a store-level exact publish time if the provider/store only guarantees an approximate delivery window.

Store:

```text
requested_release_date
provider_target_date
first_verified_live_date
```

These are distinct.

---

# 18. Submit workflow

The submission workflow should be durable.

```text
RELEASE_READY
      ↓
PRECHECK
      ↓
IDENTITY_CHECK
      ↓
METADATA_CHECK
      ↓
ASSET_CHECK
      ↓
RIGHTS_CHECK
      ↓
TERRITORY_CHECK
      ↓
PROVIDER_CAPABILITY_CHECK
      ↓
IDEMPOTENCY_CHECK
      ↓
RESERVE_SUBMISSION
      ↓
CREATE_PROVIDER_RELEASE
      ↓
UPLOAD/ATTACH_ASSETS
      ↓
SUBMIT
      ↓
PERSIST_PROVIDER_IDS
      ↓
POLL/RECEIVE_EVENTS
      ↓
RECONCILE
```

Every state transition must be persisted before moving to the next external side effect where practical.

---

# 19. Idempotency

Duplicate release submission is one of the highest-risk automation failures.

Create a deterministic submission fingerprint from:

```text
canonical release ID
release version
artist ID
track IDs
ISRCs
asset checksums
release metadata hash
territory policy hash
provider ID
```

Example:

```text
submission_fingerprint = SHA256(canonicalized_submission_payload)
```

Before creating a provider release, search local submission records for the same fingerprint.

If a provider exposes an idempotency key, send one.

If the network fails after a create request, **do not blindly create another release**. First attempt reconciliation using provider-side identifiers/search capabilities.

---

# 20. Asset upload strategy

The system must treat audio/artwork uploads as content-addressed artifacts.

For each asset store:

```text
sha256
mime_type
byte_size
duration if audio
sample_rate
channels
codec
```

The provider adapter decides how to transfer it.

Do not modify an asset after submission without creating a new immutable artifact version.

If a provider requires transcoding, preserve:

```text
source_asset_id
provider_transcoded_asset_id
transcoding_spec
provider_report
```

---

# 21. Preflight validation vs provider validation

Keep these separate.

```text
INTERNAL_PRECHECK
```
means:

> our own validation believes the release is eligible.

```text
PROVIDER_ACCEPTED
```
means:

> the distributor accepted the submission.

```text
STORE_DELIVERED
```
means:

> the downstream store confirms successful delivery.

Never collapse these into one `approved=true` flag.

---

# 22. Delivery state machine

Use a normalized state machine that can represent provider differences.

```text
NOT_SUBMITTED
SUBMISSION_PENDING
SUBMITTED
PROCESSING
REVIEWING
DELIVERY_PARTIAL
DELIVERED
REJECTED
TAKEDOWN_PENDING
TAKEN_DOWN
CORRECTION_REQUIRED
UNKNOWN
```

Every state change needs:

```text
previous_state
new_state
source
observed_at
provider_event_id if any
reason_code if any
raw_event_reference
```

---

# 23. Per-store delivery state

A release-level `DELIVERED` state is insufficient.

Create:

## `store_deliveries`

```text
id
release_id
provider_id
store_id
store_release_id
territory
status
status_reason
first_submitted_at
last_checked_at
first_delivered_at
last_error_at
next_check_at
raw_status_reference
```

Example:

```text
Spotify       DELIVERED
Apple Music   DELIVERED
YouTube Music PROCESSING
Tidal         REJECTED
```

The release can therefore be `DELIVERY_PARTIAL`.

---

# 24. Rejection classification

Normalize provider/store rejection messages into categories.

Example codes:

```text
INVALID_METADATA
INVALID_ARTWORK
INVALID_AUDIO
ARTIST_IDENTITY_CONFLICT
DUPLICATE_RELEASE
ISRC_CONFLICT
UPC_CONFLICT
LYRICS_ERROR
EXPLICIT_METADATA_ERROR
RIGHTS_QUESTION
TERRITORY_ERROR
DATE_ERROR
STORE_POLICY
PROVIDER_POLICY
UNKNOWN_REJECTION
```

Store the raw provider text in addition to the normalized code.

Never throw away provider-specific details.

---

# 25. Automated rejection handling

Safe automatic fixes may include:

```text
format normalization
missing optional metadata
whitespace/punctuation cleanup
date formatting where provider rules are deterministic
provider-required casing normalization
```

Potentially destructive or rights-related changes require a human exception:

```text
artist mapping
ISRC replacement
copyright ownership fields
territory expansion
territory reduction
release identity change
audio replacement
lyrics replacement
```

Do not allow an LLM to rewrite rights metadata autonomously just because a distributor rejected the submission.

---

# 26. Correction workflow

A correction must create a new immutable release-package version.

```text
Version 1
   ↓
store rejects artwork
   ↓
artwork corrected
   ↓
Version 2
   ↓
provider resubmission
```

Track:

```text
supersedes_version
change_reason
changed_fields
changed_asset_ids
```

Do not mutate the historical version in place.

---

# 27. Audio replacement rules

Treat audio replacement as a high-risk action because it can affect recording identity.

Before replacing audio:

```text
determine if recording remains same underlying work/recording
verify whether ISRC may remain
rerun P5 where required
compute new checksum
create new asset version
create new release-package version
```

If the change may constitute a different recording, require new recording identity handling rather than pretending it is a metadata correction.

---

# 28. Takedown workflow

Support an explicit lifecycle:

```text
TAKEDOWN_REQUESTED
      ↓
VALIDATE_SCOPE
      ↓
CHECK_PROVIDER_CAPABILITY
      ↓
SUBMIT_TAKEDOWN
      ↓
POLL
      ↓
TAKEN_DOWN
```

Takedown requests must include scope:

```text
release
track
store
territory
all_stores
```

A takedown must never be inferred from absence of a search result.

Require explicit evidence where possible.

---

# 29. Re-release after takedown

Do not automatically create a new release identity for a previously released recording unless the provider/store rules require it.

Prefer preserving:

```text
same artist mapping
same ISRC where appropriate
same recording identity
```

and use a new release/product identifier only when legitimately required.

This rule must be implemented through provider capabilities, not hard-coded assumptions.

---

# 30. Polling and reconciliation

P8 must reconcile state because external systems can change without our job receiving a callback.

Implement:

```text
webhook/event ingestion where supported
polling fallback
scheduled reconciliation
manual reconcile endpoint
```

Suggested polling cadence:

```text
first 24h: frequent
next 3 days: moderate
thereafter: daily
long-lived delivered releases: weekly/monthly verification
```

Make cadence configurable.

---

# 31. Webhook processing

When provider webhooks exist:

1. Authenticate/verify signature.
2. Store raw event immutably.
3. Deduplicate event IDs.
4. Parse event.
5. Map to normalized lifecycle state.
6. Apply state transition.
7. Trigger downstream actions if safe.

Never trust a webhook to be delivered exactly once.

---

# 32. Reconciliation algorithm

For every active release:

```text
load local expected state
load latest provider state
load latest store-delivery states where accessible
compare
```

Possible outcomes:

```text
MATCH
REMOTE_AHEAD
REMOTE_BEHIND
REMOTE_UNKNOWN
CONFLICT
```

Examples:

```text
local = SUBMITTED
remote = DELIVERED
→ update local to DELIVERED
```

```text
local = DELIVERED
remote = REJECTED
→ CONFLICT → exception queue
```

Do not automatically overwrite local history. Record the reconciliation event.

---

# 33. Artist mapping workflow

Implement a dedicated artist-mapping workflow because this is a major source of distribution mistakes.

```text
NEW_ARTIST
  ↓
IDENTITY_DISCOVERY
  ↓
MAPPING_CANDIDATES
  ↓
CONFIDENT MATCH → automatic
  │
  └─ ambiguous → HUMAN_REVIEW
```

Automatic matching can use:

```text
internal artist ID
provider artist ID
verified URLs
prior successful mapping
exact catalog identity
```

A mere display-name match is insufficient.

---

# 34. Distributor account management

P8 should support multiple distribution accounts/providers.

## `distribution_providers`

```text
id
provider_name
provider_version
status
capabilities_json
created_at
updated_at
```

## `distribution_accounts`

```text
id
provider_id
account_alias
status
credential_reference
default_account
created_at
updated_at
```

Secrets are stored outside the database in the existing secret manager/environment system.

Never store API keys in release records.

---

# 35. Provider routing

A release can be routed based on a policy such as:

```text
default_provider
region/provider preference
store coverage
feature requirements
cost
rate limits
provider health
```

Example:

```text
preferred provider unavailable
        ↓
NO automatic fallback for destructive/id-sensitive operations
        ↓
route only when the fallback is known to preserve identity/metadata
```

Do not create duplicate delivery across providers unless the architecture explicitly intends multi-distributor redundancy and identity rules have been validated.

---

# 36. Provider failover rules

Safe automatic failover:

```text
pre-submission transport failure
provider outage before external creation
```

Unsafe automatic failover:

```text
provider accepted release but status is ambiguous
provider assigned external release ID
provider created record but response was lost
store delivery is partial
artist mapping is uncertain
```

In ambiguous states, reconcile first.

---

# 37. Cost tracking

Every distribution operation should record:

```text
provider_fee if known
operation_type
bytes_transferred
request_count
processing_cost if known
external_invoice/reference
```

Do not fabricate provider fees. Use `NULL` when not available.

P9 will aggregate costs into business economics.

---

# 38. Artificial-streaming prohibition

P8 must contain explicit safeguards against actions that could manipulate streaming systems.

Never implement:

```text
automated play loops
bot listening
fake account creation
stream guarantees
paid guaranteed playlist placement
traffic manipulation
```

Spotify states that artificial streams include manipulation using automated processes such as bots/scripts, and warns that third-party services guaranteeing streams or playlist placement violate its rules and can result in music removal or other penalties. citeturn513289search7turn513289search8turn513289search10

The distribution system may perform legitimate delivery and authorized metadata/report operations only.

---

# 39. Release provenance

Every submission must preserve:

```text
p5_manifest_id
p6_manifest_id
canonical_release_package_version
track asset checksums
artwork checksum
lyrics source IDs
artist mapping version
territory policy version
provider account
provider release ID
store release IDs
submission fingerprint
submitted_at
submitted_by_system_version
```

The provenance chain should allow a future operator to answer:

> "Exactly what files and metadata were submitted to Apple/Spotify/etc. for this release?"

without reconstructing it from logs.

---

# 40. Distribution event ledger

Create an immutable event ledger.

## `distribution_events`

```text
id
release_id
store_delivery_id
provider_id
event_type
event_timestamp
received_at
provider_event_id
previous_state
new_state
reason_code
payload_reference
software_version
```

Event types might include:

```text
SUBMISSION_CREATED
SUBMISSION_ACCEPTED
SUBMISSION_REJECTED
ASSET_ACCEPTED
ASSET_REJECTED
DELIVERY_STARTED
DELIVERY_COMPLETED
TAKEDOWN_REQUESTED
TAKEDOWN_COMPLETED
CORRECTION_REQUESTED
REMOTE_STATUS_OBSERVED
```

Never delete lifecycle events.

---

# 41. P8 API

P9 and the dashboard must consume P8 through APIs rather than directly reading provider-specific tables.

## `GET /distribution/releases/{release_id}`

Return:

```text
canonical release
current normalized state
provider submission state
store delivery states
last reconciliation
pending actions
```

## `GET /distribution/releases/{release_id}/deliveries`

Return per-store status.

## `GET /distribution/releases/{release_id}/events`

Return normalized lifecycle events.

## `POST /distribution/releases/{release_id}/submit`

Queue submission after preflight validation.

## `POST /distribution/releases/{release_id}/reconcile`

Request reconciliation.

## `POST /distribution/releases/{release_id}/takedown`

Request takedown, with explicit scope.

## `GET /distribution/exceptions`

Return releases needing human action.

---

# 42. Submission endpoint behavior

`POST /submit` must be idempotent.

Possible responses:

```text
QUEUED
ALREADY_SUBMITTED
ALREADY_IN_PROGRESS
ALREADY_DELIVERED
BLOCKED_PREFLIGHT
REQUIRES_REVIEW
```

It should not synchronously wait for downstream DSP delivery.

Long-running work belongs in the durable workflow engine.

---

# 43. Job model

Use durable jobs such as:

```text
compile_release_package
validate_release
submit_release
upload_release_assets
poll_provider_status
reconcile_store_delivery
process_provider_webhook
retry_rejected_release
request_takedown
verify_takedown
sync_provider_catalog
sync_reports
```

Every job must record:

```text
job_id
release_id
attempt
started_at
finished_at
status
error_class
error_message
provider_request_reference
```

---

# 44. Retry policy

Retry only transient errors.

Retryable:

```text
network timeout
provider 5xx
429 / rate limiting
temporary upload failure
temporary store processing error
```

Non-retryable without intervention:

```text
invalid metadata
artist mismatch
rights rejection
bad artwork
invalid ISRC
unsupported territory
unsupported operation
provider policy rejection
```

Use exponential backoff with jitter.

Honor provider-specific `Retry-After` when available.

---

# 45. Rate limiting

Implement provider-scoped rate limiters:

```text
requests/minute
requests/hour
concurrent uploads
concurrent release submissions
```

Track:

```text
429 count
quota exhaustion
average request latency
provider error rate
```

Do not let one failing provider consume all worker capacity.

---

# 46. Concurrency controls

Use release-scoped locks for destructive operations.

Example:

```text
release rel_123

SUBMIT running
TAKEDOWN requested
```

The takedown must not proceed concurrently with submission unless the provider explicitly supports that transaction and the workflow models it.

At most one active mutation workflow should own a release at a time.

Read-only reconciliation may run concurrently.

---

# 47. Manual exception queue

The system should require human action only when a safe autonomous decision is unavailable.

Examples:

```text
artist identity ambiguous
rights metadata ambiguous
provider rejection with unclear resolution
ISRC conflict
duplicate identity suspected
territory conflict
provider requires account-level verification
```

Exception object:

```text
id
release_id
severity
category
reason
evidence
action_options
created_at
resolved_at
resolution
resolved_by
```

Do not merely emit an email and lose the workflow state.

---

# 48. Exception resolution semantics

Human actions must be explicit:

```text
APPROVE_RETRY
EDIT_METADATA
REMAP_ARTIST
REPLACE_ASSET
CHANGE_TERRITORY
CANCEL_RELEASE
REQUEST_TAKEDOWN
```

Every override should record:

```text
who
when
what changed
why
previous value
new value
```

---

# 49. Store discovery / verification

Where store-side public lookup is available and permitted, the system may verify:

```text
artist page exists
release appears
track appears
release date
track order
cover art
artist mapping
```

But:

- absence from search immediately after delivery must not be treated as failure;
- indexing delay must be modeled;
- a store page found by name alone must not be treated as proof of correct identity.

Use external identifiers whenever possible.

---

# 50. Post-delivery verification

For a successfully delivered release, P8 should schedule delayed verification.

Example:

```text
T+24h
T+72h
T+7d
```

Verify:

```text
store availability
correct artist mapping
correct title
correct artwork
track count/order
territory visibility where testable
```

If a mismatch is detected, raise an exception rather than silently modifying the release.

---

# 51. Report ingestion

Where distributor APIs/files support:

```text
royalty statements
stream reports
store delivery reports
rejection reports
```

P8 may ingest the raw provider report and expose normalized references to P9.

It must **not** become the canonical financial analytics engine.

Store:

```text
raw_report
provider_period
received_at
report_checksum
source_reference
```

P9 owns aggregation, economics and trend analysis.

---

# 52. Report deduplication

Reports may be re-delivered or corrected by providers.

Use:

```text
provider_report_id if available
report period
checksum
provider version
```

A corrected report must create a new version rather than overwrite the original.

---

# 53. Security requirements

Credentials must never appear in:

```text
Git
source code
database records
LLM prompts
logs
exception screenshots
reports
```

Use the existing secret-management pattern.

Logs must redact:

```text
API keys
OAuth tokens
signed URLs if sensitive
provider secret headers
```

Provider callbacks/webhooks must be authenticated.

Downloaded provider files must be malware-scanned/validated before processing where practical.

---

# 54. Artifact integrity

Before submission, verify:

```text
expected checksum
expected file size
expected MIME type
expected media metadata
```

After transfer, where provider checksum is available, compare.

Any mismatch places the submission into `ASSET_TRANSFER_ERROR` rather than silently continuing.

---

# 55. Provider policy cache

Provider-specific rules should be represented as versioned configuration:

```text
provider_rule_version
accepted_audio_formats
accepted_artwork_formats
metadata_rules
territory_rules
required_fields
lead_time_rules
artist_rules
identifier_rules
```

Do not scatter provider rules throughout business logic.

When provider rules change:

```text
new configuration version
↓
new validation
↓
existing releases can be re-evaluated where relevant
```

---

# 56. Release validation versioning

Every submission records:

```text
validation_engine_version
provider_rule_version
canonical_schema_version
metadata_mapping_version
territory_mapping_version
```

This is necessary to explain why an older release passed under an earlier ruleset.

---

# 57. LLM usage boundary

LLMs may assist with:

```text
metadata normalization
provider rejection interpretation
classification of free-text rejection reasons
drafting operator explanations
```

LLMs must not independently decide:

```text
artist identity mapping
ISRC replacement
rights ownership
territory expansion
whether a release should ignore a provider policy
whether a failed rights gate should be bypassed
```

Critical distribution state transitions must be deterministic.

An LLM can recommend an action, but the workflow engine must validate it against policy/capabilities before execution.

---

# 58. Release metadata sanity checks

Implement deterministic checks for:

```text
title length
empty/blank fields
duplicate track titles where problematic
track-number gaps
invalid release date
invalid language codes
invalid explicit flags
unescaped characters if provider-specific
illegal control characters
duplicate artist entries
inconsistent album/track artist rules
```

Do not over-normalize creative titles.

For example, punctuation, casing and non-Latin scripts should not be destroyed merely to simplify implementation.

---

# 59. Internationalization

The system must support Unicode end-to-end.

Do not assume:

```text
English-only titles
ASCII artist names
Latin-script lyrics
US date formats
```

Internally use ISO/UTC representations for dates/times.

Provider adapters convert to their required formats.

---

# 60. Duplicate-release detection

Before submission, compare the canonical package against prior catalog records using:

```text
artist identity
ISRC
release title
track titles
asset checksums
release metadata fingerprint
```

Possible outcomes:

```text
EXACT_DUPLICATE
LIKELY_DUPLICATE
NEW_RELEASE
```

Exact duplicate should not submit twice.

Likely duplicate should be reviewed if the provider identity semantics are ambiguous.

---

# 61. Catalog lineage

Every release should connect to:

```text
content_arm_id
experiment_id
playlist_id if applicable
track IDs
song generation lineage
P5 gate
P6 media package
P7 YouTube video if applicable
P8 DSP deliveries
P9 analytics records
```

This allows the full business to answer:

> "Which market experiment produced this streaming release and what did it earn?"

without manual reconciliation.

---

# 62. Distribution scheduling policy

P2/P3 may define a target release window, but P8 controls operational feasibility.

Example:

```text
P3 requested: 2026-10-15
P8 provider lead time insufficient
```

P8 must produce:

```text
schedule_conflict
```

rather than silently moving the release date.

A new date may only be chosen automatically if the business policy explicitly allows the scheduler to optimize within a defined window.

---

# 63. Safe automation ladder

Deploy P8 progressively.

## Mode 0 — Simulation

No real provider mutation.

## Mode 1 — Package validation

Compile and validate only.

## Mode 2 — Provider sandbox/test account

Submit test assets where provider supports it.

## Mode 3 — One real release, manually monitored

## Mode 4 — Small batch

## Mode 5 — Autonomous production

The mode should be configuration-driven and visible in the dashboard.

---

# 64. Operational dashboard

P8 should expose:

```text
Releases awaiting submission
Submissions in progress
Store deliveries processing
Rejected releases
Takedowns pending
Artist mapping exceptions
Identifier conflicts
Provider health
Provider queue depth
Upcoming releases
Failed jobs
```

Each release should have a timeline view.

---

# 65. Provider health monitor

Track:

```text
availability
latency
5xx rate
429 rate
rejection rate
average processing time
webhook delay
polling success rate
```

A provider can be:

```text
HEALTHY
DEGRADED
BLOCKED
UNKNOWN
```

Provider health affects only safe routing choices.

It must never trigger destructive failover automatically when release identity is uncertain.

---

# 66. Observability

Metrics:

```text
releases_submitted_total
releases_delivered_total
releases_rejected_total
releases_partial_total
releases_takedown_total
submission_latency
provider_api_latency
webhook_processing_latency
retry_count
artist_mapping_exceptions
identifier_conflicts
asset_transfer_failures
```

Logs must include:

```text
release_id
provider_id
store_id
job_id
correlation_id
state
```

Never log secrets.

---

# 67. Alerts

Alert when:

```text
provider error rate spikes
submission queue is stuck
release approaching date with unresolved delivery
artist mapping exception exists
ISRC conflict exists
store delivery conflict exists
same release repeatedly rejected
provider webhook ingestion stops
```

Do not alert on every transient retry.

Alert on actionable conditions.

---

# 68. Testing strategy

## Unit tests

Cover:

```text
metadata normalization
identifier validation
territory normalization
release fingerprinting
duplicate detection
state transitions
retry classification
rejection classification
provider capability mapping
provider payload construction
provider response parsing
```

## Property tests

Examples:

```text
canonicalizing the same release twice produces the same fingerprint
changing an audio checksum changes submission fingerprint
changing only provider formatting does not change canonical package identity
state transitions never produce invalid states
```

## Integration tests

Mock provider:

```text
create release
upload assets
submit
process
reject
partial delivery
takedown
```

## Recovery tests

Simulate process crash after:

```text
provider create
asset upload
submit response
webhook received
local DB write
```

The system must reconcile without duplicate release creation.

---

# 69. Mandatory failure scenarios

At minimum test:

### F01 — Network failure before provider request

Expected: safe retry.

### F02 — Network failure after provider accepted request

Expected: reconcile first; never blindly create duplicate.

### F03 — Provider 429

Expected: obey backoff/rate limits.

### F04 — Provider 500

Expected: retry within bounded policy.

### F05 — Invalid metadata

Expected: reject locally before mutation where detectable.

### F06 — Artist identity ambiguity

Expected: human exception.

### F07 — ISRC conflict

Expected: hard stop.

### F08 — Store-specific rejection

Expected: per-store state only; other successful deliveries remain successful.

### F09 — Partial delivery

Expected: normalized `DELIVERY_PARTIAL`.

### F10 — Takedown during processing

Expected: serialized mutation workflow.

### F11 — Provider callback duplication

Expected: idempotent event handling.

### F12 — Corrected report

Expected: new report version, not overwrite.

---

# 70. Example end-to-end lifecycle

```text
P5
 ↓
release cleared
 ↓
P6
 ↓
cover + metadata + assets ready
 ↓
P8 compile
 ↓
canonical Release v1
 ↓
provider preflight
 ↓
artist identity verified
 ↓
submission fingerprint generated
 ↓
provider create
 ↓
provider release ID stored
 ↓
assets transferred
 ↓
release submitted
 ↓
store delivery states observed
 ↓
Spotify = DELIVERED
Apple Music = DELIVERED
YouTube Music = PROCESSING
 ↓
reconciliation
 ↓
YouTube Music = DELIVERED
 ↓
P8 post-delivery verification
 ↓
P9 analytics can associate streams/revenue
```

---

# 71. P8 → P9 contract

P9 should receive a stable distribution record:

```json
{
  "release_id": "rel_123",
  "artist_id": "artist_001",
  "release_version": 2,
  "isrcs": ["..."],
  "provider": "provider_a",
  "deliveries": [
    {
      "store": "spotify",
      "external_release_id": "...",
      "status": "DELIVERED",
      "first_delivered_at": "..."
    }
  ],
  "source_package_hash": "...",
  "status": "DELIVERED"
}
```

P9 must not need to know provider-specific response schemas.

---

# 72. P8 → P9 metric separation

P8 provides:

```text
release identity
store identity
delivery state
delivery timestamps
provider report references
raw report availability
```

P9 calculates:

```text
streams
revenue
RPM-equivalent metrics
track economics
catalog economics
ROI
lifetime value
```

Keep this boundary clean.

---

# 73. P8 → P7 linkage

P8 may expose the same recording identity to P7 for catalog reconciliation.

But P8 does not manage YouTube channel videos.

For each track, maintain optional mappings:

```text
youtube_video_id
youtube_music_art_track_id if later known
spotify_track_id if later known
apple_track_id if later known
```

These are external references, not proof of delivery until verified.

---

# 74. P8 → P11 future social linkage

P11 may consume:

```text
track_id
ISRC
release URL
artist URL
cover asset
preview asset
```

P8 should expose these through a stable catalog API rather than making P11 talk directly to the distributor.

---

# 75. Data migrations and versioning

All schema changes must use migrations.

Never repurpose an existing column to mean something different.

Use explicit:

```text
schema_version
release_version
mapping_version
provider_rule_version
```

Historical delivery records must remain interpretable after future upgrades.

---

# 76. Implementation sequence

Implement in this order.

## P8.1 — Domain contracts

Define release, track, artist, identifiers, store delivery, provider capability and event schemas.

## P8.2 — Database

Create migrations for canonical release and delivery tables.

## P8.3 — Release compiler

Build P5/P6 → CanonicalReleasePackage compilation.

## P8.4 — Validation engine

Implement deterministic metadata/media/identity/territory checks.

## P8.5 — Provider abstraction

Create interfaces and capability model.

## P8.6 — First provider adapter

Implement one production-capable distributor adapter.

Do not duplicate provider code throughout the application.

## P8.7 — Submission workflow

Implement durable submission and idempotency.

## P8.8 — Reconciliation

Implement polling/webhooks/event ledger.

## P8.9 — Rejection handling

Implement normalized rejection codes and safe correction workflows.

## P8.10 — Takedown/release correction

Implement destructive workflows with explicit locks and audit events.

## P8.11 — Store-delivery mapping

Track per-store states.

## P8.12 — Report ingestion

Add raw report storage and P9 handoff.

## P8.13 — Dashboard/API

Expose release state, delivery state and exceptions.

## P8.14 — Monitoring

Add provider health, queue depth, alerts and metrics.

## P8.15 — Failure/recovery tests

Complete all mandatory scenarios.

## P8.16 — Staged production rollout

Simulation → test account → single real release → small batch → autonomous.

---

# 77. P8 acceptance criteria

P8 is complete only when all of the following are true.

## AC-01 — Canonical release compilation

A valid P5/P6 package can be compiled into an immutable canonical release package.

## AC-02 — Artist identity

Artist mappings are explicit and ambiguous names do not silently auto-map.

## AC-03 — Identifiers

ISRC/release identifiers are preserved correctly and never silently regenerated.

## AC-04 — Provider abstraction

A second distributor adapter can be implemented without changing canonical release/domain code.

## AC-05 — Idempotent submission

Repeated submission requests do not create duplicate provider releases.

## AC-06 — Crash safety

A process crash after an external mutation does not produce duplicate releases when the workflow resumes.

## AC-07 — Partial delivery

Different DSP/store states are tracked independently.

## AC-08 — Reconciliation

Webhook and polling updates reconcile remote state into normalized lifecycle states.

## AC-09 — Correction versioning

Metadata/asset changes create a new immutable release-package version.

## AC-10 — Takedown

Takedown requests are scoped, serialized and auditable.

## AC-11 — Rights safety

A release cannot bypass the P5 rights gate through P8.

## AC-12 — Artificial-stream safety

No P8 workflow can generate or purchase artificial listening activity.

## AC-13 — Reports

Raw distributor reports can be stored/versioned and referenced by P9.

## AC-14 — Exceptions

Ambiguous/high-risk decisions enter a durable exception queue.

## AC-15 — Observability

Operators can determine what was submitted, where, when, under which version, and its current status.

## AC-16 — Security

Secrets are not exposed in logs/database/reports.

## AC-17 — Test coverage

All mandatory failure scenarios are automated tests.

## AC-18 — Recovery

A simulated crash at each external side-effect boundary can recover safely.

---

# 78. What the implementation AI may choose

The implementation AI may choose:

- ORM or query framework;
- HTTP client;
- object-storage library;
- exact queue/workflow implementation if the existing architecture differs;
- exact logging/metrics library;
- provider-specific SDK vs. raw HTTP;
- exact internal repository pattern;
- exact migration framework;
- exact dashboard component library.

Provided the external behavior and architectural boundaries stay intact.

---

# 79. What the implementation AI must not decide silently

Do not silently change:

- one-to-many store delivery semantics;
- release/track identity rules;
- ISRC preservation behavior;
- artist mapping safety;
- P5 gate authority;
- idempotency guarantees;
- correction versioning;
- takedown semantics;
- provider abstraction;
- event immutability;
- distinction between provider accepted and store delivered;
- artificial-stream prohibition;
- P8/P9 ownership boundary.

Any material deviation must be recorded in the decision log with justification and impact.

---

# 80. Reasonable implementation flexibility

The specification does not prescribe:

- one distributor;
- one DSP delivery protocol;
- one provider SDK;
- one database library;
- one queue framework;
- one webhook implementation;
- one report format.

Distribution providers and DSP requirements change. The system must therefore be designed around **capabilities and adapters**, not assumptions about one current provider.

---

# 81. Definition of done

P8 is done when the system can take a cleared release, compile a canonical immutable distribution package, safely submit it through an authorized distributor integration, survive normal failures without duplicate releases, track per-store delivery status, reconcile callbacks/polling, handle legitimate corrections and takedowns, preserve complete provenance, and expose normalized state to P9—while requiring human intervention only for genuinely ambiguous/high-risk exceptions.

The most important final property is:

> **The system must know exactly what it intended to distribute, exactly what it actually submitted, exactly what the distributor/store reported, and exactly which release/track identity each external object belongs to.**

That property is more important than maximizing submission throughput.
