# P7 — Autonomous YouTube Publishing Engine
## Detailed Implementation Plan

**Document status:** Implementation-ready specification  
**Phase:** P7 of the Autonomous AI Music Media Engine  
**Primary objective:** Take a validated P6 publish package and reliably turn it into a YouTube publication, including upload, metadata, thumbnail, synthetic-media disclosure, playlist placement, scheduling, verification, reconciliation and post-publication state capture, with minimal human intervention.

---

# 1. Purpose

P7 is the **YouTube distribution and publishing control plane**.

P6 creates a finished, publish-ready media package. P7 is responsible for putting that package onto the correct YouTube channel safely and reproducibly.

The system must answer, without requiring a human to manually operate YouTube Studio:

> Is this release actually allowed to publish?
> Which channel should receive it?
> What exact metadata should be submitted?
> Which thumbnail should be used?
> Should it publish immediately or be scheduled?
> What synthetic-media disclosure is required?
> Which playlist(s) should contain it?
> Did YouTube actually ingest and process the upload correctly?
> Was the resulting YouTube object reconciled with our internal release?
> If something fails, what is the safe next action?

P7 must be a **durable integration**, not a thin wrapper around `videos.insert`.

It must survive network failures, process restarts, API retries, partial writes, duplicate jobs, provider-side processing delays, quota constraints and changes in the external state of an already-created video.

---

# 2. Scope

## 2.1 In scope

P7 must implement:

1. YouTube channel/account configuration.
2. OAuth credential loading and lifecycle handling.
3. Publish-package ingestion from P6.
4. Pre-publication eligibility gates.
5. Metadata validation and rendering.
6. Deterministic publishing manifests.
7. YouTube video upload.
8. Resumable upload handling.
9. Upload idempotency and duplicate prevention.
10. Upload status reconciliation.
11. Synthetic/altered content disclosure handling.
12. Audience / made-for-kids field handling.
13. Privacy-state management.
14. Scheduling.
15. Custom thumbnail upload.
16. YouTube playlist creation/maintenance where required.
17. Playlist-item insertion and reconciliation.
18. Optional channel-section maintenance for the channel brand.
19. Publish-time verification.
20. Post-publication health checks.
21. Automatic retry/recovery.
22. Quota tracking and request budgeting.
23. Audit trail and provenance.
24. Human exception queue.
25. Safe operational modes: dry-run, private-only, staged, autonomous.
26. Unit, integration, contract, recovery and end-to-end tests.
27. P7 handoff to P8/P9.

## 2.2 Explicitly out of scope

Do not implement in P7:

- Market research.
- Genre selection.
- A&R decisions.
- Content-arm allocation.
- Music generation.
- Audio QA.
- Rights analysis itself.
- Video rendering.
- Spotify distribution.
- YouTube Analytics optimization logic.
- Thumbnail prediction model training.
- Autonomous comment replies.
- Ad campaign management.
- Subscriber-growth prediction.

P7 consumes the decisions and artifacts from P1–P6 and publishes them.

---

# 3. Core boundary

The logical boundary is:

```text
P5
  ↓
release cleared
  ↓
P6
  ↓
final media package
  ↓
P7
  ↓
YouTube API
  ↓
video/playlist published
  ↓
P9 analytics
```

P7 must never silently modify the content experiment itself.

For example, if P3 says:

```text
thumbnail_variant = B
```

P7 may validate and upload B, but must not decide to use C because an internal heuristic prefers it. Any optimization choice belongs upstream or to a later experimental policy.

Likewise, P7 must not rewrite the song sequence or visual package.

---

# 4. Current YouTube API constraints relevant to P7

The implementation must be based on the current official YouTube API surface, not an outdated code sample.

## 4.1 Video upload

The official `videos.insert` endpoint uploads a video and can set metadata. Current documentation states that uploads from unverified API projects created after July 28, 2020 are restricted to private viewing until the API project passes the required audit. The same documentation currently lists a maximum upload size of 256 GB.  
Source: https://developers.google.com/youtube/v3/docs/videos/insert

The API's current `videos.insert` documentation also exposes the synthetic-media disclosure field through `status.containsSyntheticMedia`.  
Source: https://developers.google.com/youtube/v3/docs/videos

## 4.2 Scheduling

YouTube allows `status.publishAt` to schedule a video while it is private, and the official documentation states that scheduling requires private visibility.  
Source: https://developers.google.com/youtube/v3/docs/videos

The consumer-facing YouTube Help documentation likewise explains that scheduled videos remain private until the scheduled publication time.  
Source: https://support.google.com/youtube/answer/1270709?hl=en

## 4.3 Thumbnails

The official `thumbnails.set` endpoint supports custom thumbnails and currently documents a maximum file size of 50 MB and supported MIME types including JPEG and PNG. Its current quota cost is approximately 50 units per call.  
Source: https://developers.google.com/youtube/v3/docs/thumbnails/set

## 4.4 Playlists

The official playlist API supports playlist creation and playlist-item insertion/update/delete. Current documentation lists 50 quota units for `playlists.insert`.  
Sources:
- https://developers.google.com/youtube/v3/docs/playlists/insert
- https://developers.google.com/youtube/v3/docs/playlistItems

## 4.5 Channel sections

The official API supports channel sections, with a current maximum of 10 shelves. Channel sections can expose playlists on the channel browse page where the channel configuration permits it.  
Source: https://developers.google.com/youtube/v3/docs/channelSections

## 4.6 Upload retry behavior

The official upload guide demonstrates resumable uploads and exponential-backoff handling. P7 should implement a stronger production-grade state machine around the same principle rather than treating an upload as one opaque HTTP call.  
Source: https://developers.google.com/youtube/v3/guides/uploading_a_video

---

# 5. Design principles

## 5.1 YouTube is an external system of record for YouTube state

The internal database is authoritative for our **intent**, provenance and workflow state.

YouTube is authoritative for what actually exists on the platform.

The system must reconcile both.

## 5.2 Intent must be immutable once execution begins

Once a publish attempt starts, the exact P6 package and intended YouTube metadata must be snapshotted.

Do not silently mutate the upload payload because the upstream package changed later.

A later version requires a new publish attempt or explicit revision workflow.

## 5.3 Idempotency before convenience

Running the same publishing job twice must not accidentally produce two public videos.

The workflow must look for an existing mapping before creating another remote object.

## 5.4 Private-first deployment

During initial rollout, publishing should default to private or otherwise non-public staging.

Autonomous public publishing should only become available after a configurable readiness gate.

## 5.5 Explicit risk gates

P7 should stop before upload when required information is missing or inconsistent.

It should not guess:

- target channel;
- privacy mode;
- disclosure value;
- audience designation;
- publish time;
- playlist assignment;
- thumbnail asset.

Defaults are acceptable only when they are explicitly configured by the channel profile.

## 5.6 Provider errors are not content failures

A 503 from YouTube does not mean the video is bad.

A content rejection may mean something different from a network failure.

Represent those classes separately.

---

# 6. High-level architecture

```text
               P6 Publish Package
                       │
                       ▼
              ┌───────────────────┐
              │ P7 Ingest / Lock  │
              └─────────┬─────────┘
                        ▼
              ┌───────────────────┐
              │ Preflight Gate    │
              └─────────┬─────────┘
                        ▼
             ┌─────────────────────┐
             │ Publish Manifest     │
             └──────────┬──────────┘
                        ▼
              ┌───────────────────┐
              │ Upload Scheduler  │
              └─────────┬─────────┘
                        ▼
             ┌─────────────────────┐
             │ Resumable Uploader   │
             └──────────┬──────────┘
                        ▼
                YouTube Video ID
                        │
         ┌──────────────┼────────────────┐
         ▼              ▼                ▼
     Metadata       Thumbnail        Disclosure
         │              │                │
         └──────────────┼────────────────┘
                        ▼
                Playlist Manager
                        ▼
                Schedule / Publish
                        ▼
                 Reconciliation
                        ▼
                Publish Verified
                        │
                        ▼
                  P9 Analytics
```

---

# 7. Repository structure

Adapt to the existing project if it already has an established structure, but preserve these logical modules:

```text
src/
  youtube_publishing/
    __init__.py
    config.py
    domain/
      entities.py
      enums.py
      value_objects.py
    auth/
      oauth.py
      token_store.py
      scopes.py
    manifests/
      models.py
      validator.py
      fingerprint.py
    preflight/
      gates.py
      validators.py
      policy.py
    client/
      base.py
      youtube_data.py
    upload/
      resumable.py
      session.py
      idempotency.py
      retry.py
    metadata/
      builder.py
      validator.py
      localization.py
    thumbnails/
      uploader.py
      validator.py
    playlists/
      manager.py
      reconciliation.py
    scheduling/
      planner.py
      validator.py
    reconcile/
      video.py
      playlist.py
      scheduler.py
    jobs/
      publish.py
      reconcile.py
      repair.py
    audit/
      events.py
      snapshots.py
    quota/
      tracker.py
      budget.py
    observability/
      logging.py
      metrics.py
    api/
      routes.py
      schemas.py

tests/
  unit/
  integration/
  contract/
  recovery/
  e2e/
  fixtures/

migrations/

docs/
  P7_youtube_publishing_engine_implementation_plan.md
```

---

# 8. Domain entities

## 8.1 `youtube_channel_profile`

Represents one configured publishing target.

Fields:

```text
id
channel_id
channel_name
brand_id
status
oauth_credential_ref
default_language
default_country
default_category_id
default_license
default_privacy_mode
default_notify_subscribers_policy
default_made_for_kids_policy
default_synthetic_media_policy
default_publish_timezone
active
configuration_version
created_at
updated_at
```

Never store client secrets or refresh tokens directly in this table.

---

# 9. Publish package contract from P6

P7 expects a package containing, at minimum:

```text
publish_package_id
content_bundle_id
experiment_id
content_arm_id
video_master_asset_id
video_sha256
thumbnail_asset_id
thumbnail_sha256
primary_title
title_variants
final_description
keywords/tags if used
language
category_id
playlist_intent
playlist_title_hint
chapter_map
tracklist
brand_profile_id
contains_synthetic_media
made_for_kids
scheduled_publish_at
publish_timezone
license_intent
provenance_manifest_ref
p6_manifest_version
created_at
```

The exact field names can differ from P6's implementation, but P7 must receive equivalent semantics.

## Required validation

P7 rejects packages with:

```text
missing video artifact
missing checksum
missing title
missing description when policy requires it
invalid target channel
unknown experiment ID
unknown content-arm ID
unsupported category
invalid scheduled timestamp
thumbnail missing when required
provenance missing
```

---

# 10. Publish manifest

Before any external write, freeze the input into a P7-specific immutable `youtube_publish_manifest`.

Example:

```json
{
  "manifest_id": "pub_2026_000184",
  "channel_id": "UC...",
  "publish_package_id": "pkg_...",
  "video": {
    "asset_id": "video_...",
    "sha256": "...",
    "size_bytes": 1839201840
  },
  "metadata": {
    "title": "2AM Seoul | Late Night R&B Mix",
    "description": "...",
    "tags": ["rnb", "night drive", "seoul"],
    "category_id": "10",
    "default_language": "en"
  },
  "status": {
    "privacy_status": "private",
    "publish_at": "2026-10-07T12:00:00Z",
    "contains_synthetic_media": true,
    "made_for_kids": false
  },
  "thumbnail": {
    "asset_id": "thumb_...",
    "sha256": "..."
  },
  "playlist": {
    "mode": "attach_existing",
    "playlist_id": "PL..."
  },
  "policy_snapshot": "youtube-policy-v1",
  "created_at": "2026-09-20T06:00:00Z"
}
```

The manifest must be content-addressable or otherwise fingerprintable.

---

# 11. Idempotency model

This is one of the most important parts of P7.

## 11.1 Internal idempotency key

Build a deterministic publish key from:

```text
channel_id
publish_package_id
video_sha256
p6_manifest_version
```

Hash the canonical representation.

If a job with the same key is already in progress or completed, do not create a second upload.

## 11.2 Remote-video reconciliation

Before starting a new upload after a crash/retry, look for an existing internal mapping:

```text
publish_attempt
 → youtube_video_id
```

If found, resume reconciliation instead of uploading again.

Do not attempt to identify remote videos by title alone. Titles are not unique.

---

# 12. Publish-state machine

Use a durable state machine.

Recommended states:

```text
RECEIVED
LOCKED
PREFLIGHT
BLOCKED_PREFLIGHT
READY
UPLOAD_SESSION_CREATING
UPLOADING
UPLOADED
PROCESSING
METADATA_VERIFYING
THUMBNAIL_PENDING
THUMBNAIL_SET
PLAYLIST_PENDING
PLAYLIST_ATTACHED
SCHEDULED
PUBLISHED
VERIFIED
FAILED_RETRYABLE
FAILED_PERMANENT
QUARANTINED
CANCELLED
```

Transitions must be explicit.

No arbitrary state mutation from an admin UI.

---

# 13. Preflight gates

Preflight should run before any irreversible external action.

## Gate A — channel

Verify:

```text
channel configuration exists
channel ID is configured
OAuth credential is present
credential is usable
channel mapping is current
```

## Gate B — artifact

Verify:

```text
file exists
checksum matches
file size matches
container is valid
codec is supported
```

P6 should already have rendered and validated the media, but P7 must still verify the file before transmitting it.

## Gate C — metadata

Validate:

```text
title length
Unicode validity
description format
tag constraints
language code
category ID
```

Do not silently truncate or rewrite metadata unless the manifest explicitly authorizes deterministic normalization.

If a provider limit is exceeded, block and report exactly what failed.

## Gate D — audience setting

The configured `made_for_kids` value must be explicit.

Do not infer it from the music genre.

## Gate E — synthetic content

If the content is AI-generated and the publish manifest requires synthetic-media disclosure, the final request must carry that disclosure.

The official YouTube API exposes `status.containsSyntheticMedia` for this purpose.  
Source: https://developers.google.com/youtube/v3/docs/videos

## Gate F — schedule

If scheduling is requested:

```text
publish_at is timezone-normalized
publish_at is in the future
privacy_status = private
```

The official API documentation requires `publishAt` to be used with private privacy state and notes that a past scheduled time results in immediate publication.  
Source: https://developers.google.com/youtube/v3/docs/videos

## Gate G — playlist

If playlist attachment is required:

```text
playlist exists or can be created
playlist belongs to configured channel
playlist policy allows the intended item
```

---

# 14. Metadata construction

P7 must not invent content metadata.

It receives authoritative metadata from P6.

The metadata builder should have two layers:

```text
P6 authored fields
      ↓
P7 schema normalization
      ↓
YouTube request payload
```

## Allowed deterministic transformations

Examples:

```text
normalize line endings
normalize Unicode
remove invalid control characters
normalize language code
serialize chapter timestamps
```

## Disallowed silent transformations

Do not:

```text
rewrite title for SEO
change genre labels
invent hashtags
add unrelated keywords
translate without an explicit localization payload
change experiment text
```

SEO optimization is a separate concern.

---

# 15. Description structure

P6 should supply the final description, but P7 may validate its structure.

For music mixes, expected sections may include:

```text
short editorial intro

Tracklist
00:00 Track 1
04:12 Track 2
...

AI disclosure / production note when required

Streaming links when configured

Brand links when configured
```

Do not assume these sections exist if P6 deliberately chooses another format.

---

# 16. Tags and metadata policy

Tags should be treated as optional supporting metadata, not the primary discovery strategy.

The system must not keyword-stuff.

Reject obvious mismatches such as:

```text
video about chill R&B

but tags include:
news, politics, Taylor Swift, breaking news
```

The validator should check semantic consistency between the P3/P6 content genome and metadata.

---

# 17. Upload implementation

Use the official YouTube Data API `videos.insert` operation.

The implementation must use resumable upload semantics so large videos can survive network interruption.

The official YouTube upload guide demonstrates resumable uploads and retry/backoff patterns.  
Source: https://developers.google.com/youtube/v3/guides/uploading_a_video

## Upload phases

```text
create request
 ↓
receive/resume upload session
 ↓
transmit chunks
 ↓
receive final video ID
 ↓
store remote ID immediately
```

The remote ID must be persisted as soon as returned, before any subsequent metadata or playlist step.

---

# 18. Chunking strategy

Do not hard-code one chunk size for all environments.

Support configuration based on:

```text
network quality
file size
host environment
retry history
```

Default to a sensible resumable chunk configuration.

The upload layer should expose:

```text
bytes_uploaded
bytes_total
percent_complete
current_attempt
retry_count
last_error
estimated_throughput
```

Do not report an upload as complete until the final API response is received and contains the remote video ID.

---

# 19. Upload error classification

Classify external errors as:

```text
AUTH_FAILURE
PERMISSION_DENIED
QUOTA_EXCEEDED
RATE_LIMIT
NETWORK_TRANSIENT
SERVER_TRANSIENT
INVALID_METADATA
INVALID_MEDIA
POLICY_REJECTION
COPYRIGHT_REJECTION
ACCOUNT_RESTRICTION
DUPLICATE_OR_CONFLICT
UNKNOWN_PROVIDER_ERROR
```

Retry only classes that are actually retryable.

Do not retry content or authorization failures indefinitely.

---

# 20. Backoff

Use exponential backoff with jitter.

Recommended conceptual progression:

```text
1s
2s
4s
8s
16s
...
max_backoff
```

Exact values must be configurable.

Apply a maximum retry count and overall deadline.

For resumable uploads, prefer resuming from the existing session rather than starting over when the provider allows it.

---

# 21. Upload session recovery

Persist enough information to attempt session recovery after process restart.

Store:

```text
upload_session_id if obtainable
publish_attempt_id
file_sha256
file_size
chunk_size
bytes_uploaded_known
created_at
last_activity_at
```

If session recovery is impossible, mark the attempt accordingly and determine whether a safe fresh upload can be initiated after checking for an existing remote video mapping.

---

# 22. Post-upload processing

A successful `videos.insert` response means the upload was accepted, not necessarily that the final video is fully processed.

P7 must transition to:

```text
UPLOADED
→ PROCESSING
```

then poll the video's status through the appropriate read endpoint.

The API exposes `status.uploadStatus`, including states such as `uploaded`, `processed`, `failed` and `rejected`, together with failure/rejection reason fields.  
Source: https://developers.google.com/youtube/v3/docs/videos

Do not declare `VERIFIED` merely because a video ID exists.

---

# 23. Processing polling

Use bounded polling.

Example starting policy:

```text
30s
60s
120s
240s
...
```

Cap the total processing wait time according to configurable file-size expectations.

During processing, do not immediately trigger downstream actions that depend on processing completion unless the relevant API explicitly permits them.

If processing remains unresolved after the deadline:

```text
status = PROCESSING_TIMEOUT
```

and queue reconciliation rather than treating it as a permanent content failure.

---

# 24. Metadata reconciliation

After upload and again before final publication, read the remote video state and compare it against the publish manifest.

Verify at least:

```text
video_id
channel_id
privacy_status
title
description
tags if set
category_id
made_for_kids
contains_synthetic_media
publish_at if scheduled
```

Any discrepancy must be classified:

```text
expected provider normalization
safe mismatch
unexpected mismatch
```

Unexpected mismatch should block automatic publication finalization.

---

# 25. Thumbnail flow

After a valid video ID exists, upload the designated thumbnail through `thumbnails.set`.

The official API currently documents:

```text
max file size: 50 MB
JPEG/PNG supported
quota cost: ~50 units
```

Source: https://developers.google.com/youtube/v3/docs/thumbnails/set

P7 must verify:

```text
thumbnail asset exists
checksum matches
file size within documented limits
MIME type accepted
```

After setting it, read remote state where practical to confirm the expected thumbnail has been applied.

Never blindly cycle through thumbnail variants during P7. Variant experimentation belongs to the experiment framework.

---

# 26. Playlist handling

P6 may specify one of:

```text
attach_existing
create_and_attach
no_playlist
```

## `attach_existing`

Verify that the playlist is accessible and belongs to the target channel where relevant.

Insert the new video using the appropriate playlist-item operation.

## `create_and_attach`

Create the playlist through the official API, persist its remote ID immediately, then insert the video.

Current official documentation lists 50 quota units for playlist creation.  
Source: https://developers.google.com/youtube/v3/docs/playlists/insert

## No playlist

Do nothing.

Do not infer one merely because the title resembles an existing playlist.

---

# 27. Playlist ordering

P6 should provide ordering intent.

P7 should preserve it when the API supports the requested position.

If concurrent uploads target the same playlist, use a distributed lock or serialized playlist mutation workflow.

Avoid race conditions such as:

```text
Video A sees position 0
Video B sees position 0
A inserts
B inserts
result: unintended ordering
```

The playlist reconciler must verify final order after writes.

The official API supports `playlistItems.insert`, `playlistItems.update`, and `playlistItems.delete`.  
Source: https://developers.google.com/youtube/v3/docs/playlistItems

---

# 28. Channel-section maintenance

Channel sections are optional brand infrastructure and should not be rebuilt on every video upload.

If configured, implement a separate low-frequency reconciliation job that ensures the intended playlists remain surfaced correctly.

The official API currently supports channel-section management and documents a maximum of 10 shelves.  
Source: https://developers.google.com/youtube/v3/docs/channelSections

Do not repeatedly insert/update sections during every publish job.

---

# 29. Scheduling policy

Scheduling should happen through one explicit P7 scheduling service.

Inputs:

```text
requested_publish_at
channel_timezone
market_timezone if relevant
minimum_lead_time
blackout_windows
publication_frequency_limit
```

Normalize all timestamps to UTC internally.

Render operator-facing timestamps in the channel's configured timezone.

Before submission verify:

```text
publish_at > now + minimum_lead_time
publish_at not in blackout window
privacy = private
```

The official API states that `publishAt` can only be set for a private video and that a past time causes immediate publication.  
Source: https://developers.google.com/youtube/v3/docs/videos

Never intentionally submit a past timestamp unless immediate publication is explicitly requested.

---

# 30. Notification policy

The API exposes `notifySubscribers` on video insertion.

The official documentation notes that the default behavior is notification and that channel owners uploading many videos may prefer false to avoid notifying subscribers for every upload.  
Source: https://developers.google.com/youtube/v3/docs/videos/insert

P7 must therefore use an explicit channel policy:

```text
notify_subscribers = true/false
```

Never choose the value ad hoc during implementation.

For this music-media business, the default should be a configurable policy rather than a hard-coded recommendation.

---

# 31. Synthetic-media disclosure

The publishing manifest must contain an explicit decision on whether the video includes relevant altered/synthetic content.

For AI-generated music, P7 must propagate the upstream policy decision and set the YouTube API field accordingly when required.

The official video resource exposes:

```text
status.containsSyntheticMedia
```

for disclosure of realistic altered/synthetic content.  
Source: https://developers.google.com/youtube/v3/docs/videos

P7 is a **transport layer** for this decision, not the authority that determines whether a particular creation is synthetic. That determination must come from the P5/P6 policy/provenance layer and current policy configuration.

---

# 32. Audience / made-for-kids handling

The publish manifest must specify the intended audience designation.

The API supports `status.selfDeclaredMadeForKids` on insert/update and exposes `madeForKids`/`selfDeclaredMadeForKids` in the video resource.  
Source: https://developers.google.com/youtube/v3/docs/videos

Do not infer audience status from music style.

If no explicit value is available from upstream configuration, block publication.

---

# 33. Privacy-state policy

Supported lifecycle values relevant to this system:

```text
private
unlisted
public
```

The official API documents these privacy states.  
Source: https://developers.google.com/youtube/v3/docs/videos

Recommended rollout states:

### Development

```text
private only
```

### Staging

```text
private or unlisted
```

### Production autonomous

```text
scheduled/private → public at approved time
```

Do not allow a local developer configuration to accidentally publicize production content.

---

# 34. Public-publish safety gate

Separate the permission to **upload** from the permission to **publish publicly**.

A configuration should look like:

```text
allow_upload = true
allow_private = true
allow_unlisted = false
allow_public = false
```

until the system passes readiness criteria.

Then:

```text
allow_public = true
```

can be enabled independently.

This prevents a newly installed environment from accidentally making content public.

---

# 35. API project verification and audit readiness

The implementation must explicitly detect whether the Google API project is subject to the unverified-upload restriction.

Current official YouTube documentation states that uploads by unverified API projects created after July 28, 2020 are restricted to private viewing and require an audit to lift the restriction.  
Source: https://developers.google.com/youtube/v3/docs/videos/insert

Expose a readiness status such as:

```text
UPLOAD_PRIVATE_READY
UPLOAD_PUBLIC_READY
API_AUDIT_REQUIRED
API_AUDIT_PENDING
API_AUDIT_FAILED
```

The public-autonomous publishing scheduler must refuse to enable itself while the required audit state is unresolved.

---

# 36. Quota management

P7 must have a provider-aware quota manager.

Track separately:

```text
quota units consumed
upload bucket usage
search/list calls
thumbnail calls
playlist calls
video update calls
```

Do not assume every operation costs the same amount.

The official current documentation lists, for example:

```text
thumbnails.set ≈ 50 units
playlists.insert = 50 units
```

while `videos.insert` has a separate upload quota behavior.  
Sources:
- https://developers.google.com/youtube/v3/docs/thumbnails/set
- https://developers.google.com/youtube/v3/docs/playlists/insert
- https://developers.google.com/youtube/v3/docs/videos/insert

Quota handling must be configuration-driven and must stop low-priority work before critical reconciliation tasks when approaching limits.

---

# 37. Request budgeting

Before each job, estimate required provider calls:

```text
upload
video reconciliation
thumbnail
playlist
playlist reconciliation
```

If projected quota exceeds the available budget:

```text
queue / defer
```

Do not partially execute an operation merely because quota is almost exhausted.

Exception: if an upload has already started, prioritize recovery/reconciliation to avoid duplicate uploads.

---

# 38. Concurrency control

Concurrency must be limited at several levels.

## Per channel

Avoid simultaneous high-volume writes against one channel unless proven safe.

## Per publish package

Only one active publish attempt for a given idempotency key.

## Per playlist

Serialize playlist modifications.

## Per OAuth credential

Protect token refresh from concurrent refresh storms.

Use distributed locks when the deployment has multiple workers.

---

# 39. OAuth architecture

Use a secure credential reference rather than embedding secrets into job payloads.

Store:

```text
oauth_credential_ref
provider
scopes
created_at
last_validated_at
status
```

Refresh-token material belongs in a secrets system.

Never log access tokens or refresh tokens.

Implement proactive validation so an expired/revoked credential can be detected before a large batch begins.

---

# 40. OAuth scope policy

Request the minimum scopes needed for the configured operations.

At minimum, the production account must be authorized for the YouTube operations actually implemented.

Do not request unrelated Google permissions just because they are available.

Store the required-scope version in configuration.

If the scope set changes, mark the credential as requiring reauthorization rather than silently failing mid-run.

---

# 41. Human exception model

The system is designed for minimal intervention, not zero observability.

Human-review events should include:

```text
OAuth reauthorization
API audit issue
policy rejection
copyright/legal rejection
unexpected metadata mutation
unknown provider error after bounded retries
account restriction
possible duplicate publication
```

Routine successful uploads should not generate human tasks.

---

# 42. Repair engine

Implement repair operations rather than requiring a developer to manually edit database state.

Examples:

```text
REPAIR_METADATA
REPAIR_THUMBNAIL
REPAIR_PLAYLIST_ATTACHMENT
RESUME_UPLOAD
RECONCILE_REMOTE_VIDEO
REBUILD_SCHEDULE
```

Every repair must create an audit event:

```text
who/what initiated
why
previous state
new state
timestamp
configuration version
```

Automated repairs must have bounded scope.

---

# 43. Reconciliation service

A periodic reconciliation process should scan for mismatches.

Example:

```text
internal: PUBLISHED
YouTube: PRIVATE
```

or:

```text
internal: playlist attached
YouTube: playlist missing
```

or:

```text
internal: thumbnail hash X
YouTube: different thumbnail state
```

The reconciler should classify the discrepancy and either:

```text
repair automatically
```

or:

```text
raise an exception
```

It must not silently overwrite the internal truth to match YouTube just because YouTube differs.

Determine whether the difference is expected, accidental, or operator-initiated.

---

# 44. Event model

Record events like:

```text
PUBLISH_PACKAGE_RECEIVED
PREFLIGHT_STARTED
PREFLIGHT_PASSED
PREFLIGHT_BLOCKED
UPLOAD_SESSION_CREATED
UPLOAD_PROGRESS
UPLOAD_COMPLETED
VIDEO_ID_CAPTURED
VIDEO_PROCESSING_STARTED
VIDEO_PROCESSED
METADATA_VERIFIED
THUMBNAIL_SET
PLAYLIST_CREATED
PLAYLIST_ITEM_INSERTED
VIDEO_SCHEDULED
VIDEO_PUBLISHED
PUBLISH_VERIFIED
PUBLISH_FAILED
REPAIR_STARTED
REPAIR_COMPLETED
```

Events should be append-only.

---

# 45. Audit record

For every publish attempt store:

```text
publish_attempt_id
publish_manifest_id
channel_id
experiment_id
content_arm_id
video_sha256
thumbnail_sha256
request_hashes
provider_request_ids where available
youtube_video_id
playlist_ids
state_transitions
errors
retry_counts
quota_consumption
created_at
completed_at
software_version
configuration_version
```

This permits complete forensic reconstruction.

---

# 46. P7 database tables

Recommended tables:

```text
youtube_channel_profiles
youtube_oauth_credentials_ref
youtube_publish_manifests
youtube_publish_attempts
youtube_upload_sessions
youtube_video_mappings
youtube_video_state_snapshots
youtube_thumbnail_attempts
youtube_playlist_mappings
youtube_playlist_item_mappings
youtube_schedule_records
youtube_reconciliation_jobs
youtube_publish_events
youtube_quota_usage
youtube_repairs
youtube_exceptions
```

The exact schema can adapt to the existing repository conventions.

---

# 47. Example publish-attempt schema

```text
id UUID PK
idempotency_key UNIQUE
manifest_id FK
channel_profile_id FK
state
youtube_video_id NULL
upload_session_id NULL
playlist_id NULL
scheduled_publish_at NULL
last_provider_status NULL
retry_count
last_error_code NULL
last_error_message NULL
quota_units_used
created_at
updated_at
completed_at NULL
```

Create indexes on:

```text
idempotency_key
youtube_video_id
manifest_id
state
scheduled_publish_at
```

---

# 48. API contract

## `POST /youtube/publish`

Creates or resumes a publish attempt.

Input:

```json
{
  "publish_package_id": "pkg_123",
  "mode": "scheduled"
}
```

Return:

```json
{
  "publish_attempt_id": "attempt_123",
  "state": "READY",
  "idempotency_key": "..."
}
```

The endpoint must be idempotent.

---

# 49. `GET /youtube/publish/{attempt_id}`

Return:

```text
state
channel
manifest
youtube_video_id
upload_progress
scheduled_time
playlist_state
thumbnail_state
provider_status
errors
last_updated
```

Do not expose OAuth secrets.

---

# 50. `POST /youtube/publish/{attempt_id}/repair`

Input:

```json
{
  "repair_type": "reconcile_remote_video"
}
```

Repair types must come from an allowlisted enum.

Do not accept arbitrary provider API operations from the public API.

---

# 51. `GET /youtube/health`

Return system readiness:

```text
OAuth healthy
API project verified for public upload
quota budget healthy
upload workers healthy
reconciliation healthy
last successful publish
last failed publish
```

---

# 52. Publication modes

## Mode 1 — dry run

No provider writes.

Validate:

```text
manifest
file
metadata
quota estimate
channel config
schedule
```

## Mode 2 — private smoke test

Actually upload but force:

```text
privacy = private
```

This is the first real-world integration mode.

## Mode 3 — staged

Uploads become private/unlisted until operator or policy gate allows publication.

## Mode 4 — autonomous

P7 follows the approved schedule and publication policy without routine human intervention.

Each mode must be explicit in configuration.

---

# 53. Autonomous mode activation criteria

Do not expose autonomous public publishing until all are true:

```text
OAuth validated
API project public-upload eligibility confirmed
at least N successful private smoke tests
resumable recovery tested
metadata reconciliation tested
thumbnail setting tested
playlist reconciliation tested
synthetic-media disclosure verified
scheduling verified
quota handling verified
duplicate protection verified
repair workflow verified
monitoring configured
exception alerts configured
```

`N` should be configurable; a default such as 5–10 is reasonable for initial deployment.

---

# 54. Duplicate-publication protection scenarios

The test suite must simulate:

### Scenario A

Upload succeeds.
Process crashes before database persistence.

Expected:

Reconciliation identifies remote video or safely recovers the upload mapping without creating another public video.

### Scenario B

Database says uploading.
Remote video already exists.

Expected:

Attach remote ID after reconciliation.

### Scenario C

Network timeout after final chunk.

Expected:

Do not assume failure; query/resume before starting another upload.

---

# 55. Scheduling race conditions

Test:

```text
scheduler sees publish_at approaching
operator pauses channel
scheduler resumes
```

Expected:

Publication respects the current channel control state.

Also test:

```text
publish_at edited upstream after P7 manifest locked
```

Expected:

Existing locked attempt remains unchanged; a new manifest/version is required for a new schedule.

---

# 56. Metadata immutability

P7 must store the exact submitted title/description/tags as a snapshot.

Later analytics jobs must never mutate that historical submission snapshot.

If YouTube normalizes the content, preserve both:

```text
submitted_value
observed_remote_value
```

This lets the reconciliation engine distinguish provider normalization from accidental mutation.

---

# 57. Content provenance linkage

P7 must retain the full upstream provenance chain:

```text
youtube_video
 ↓
publish_attempt
 ↓
publish_manifest
 ↓
P6 package
 ↓
P5 release manifest
 ↓
P4 generated assets
 ↓
P3 experiment
 ↓
P2 A&R directive
 ↓
P1 opportunity arm
```

This enables P9 to answer:

> Which market hypothesis created this video?

without reverse-engineering filenames.

---

# 58. Experiment-integrity requirements

P7 must not alter variables designated by P3 as experimental.

Examples:

If P3 tests:

```text
title_archetype = time_place_mood
```

P7 must not rewrite the title into another archetype.

If P3 tests:

```text
thumbnail_variant = B
```

P7 must not pick another thumbnail.

If P3 tests:

```text
publish_time = 20:00 KST
```

P7 must not silently shift it to 18:00 based on an unrelated heuristic.

Operational constraints may override an experiment only through an explicit policy exception, which must be logged.

---

# 59. Upload timing and channel throughput

P7 should maintain configurable throughput controls:

```text
max uploads/day
max uploads/week
minimum interval between uploads
max simultaneous uploads
max scheduled horizon
```

These are channel safety/configuration controls, not content strategy decisions.

Do not encode arbitrary limits in application logic.

---

# 60. Operational dashboards

The P7 dashboard should show:

```text
Queue depth
Uploading
Processing
Scheduled
Published today
Failed
Retrying
Blocked
Quota used
Quota remaining if available
Last successful upload
Last reconciliation
```

Per-video view:

```text
manifest
experiment
video ID
state machine
upload progress
remote state
thumbnail
playlist
schedule
errors
cost/quota
```

---

# 61. Alerts

Alert on:

```text
OAuth revoked
API audit required
quota threshold exceeded
upload failure rate spike
processing timeout spike
duplicate-publication suspicion
playlist reconciliation drift
unexpected metadata drift
channel restriction
scheduled publication missed
```

Don't alert on every successful upload.

---

# 62. Logging requirements

Structured logs must include:

```text
timestamp
service
publish_attempt_id
manifest_id
channel_id
youtube_video_id if known
state
operation
latency
error_code
retry_count
quota_units
```

Never log:

```text
OAuth access tokens
refresh tokens
client secrets
full private file paths if they expose credentials
```

---

# 63. Security

The upload worker should run with the least filesystem permissions necessary.

Validate every file path against an allowed artifact root.

Do not allow a manifest to instruct the worker to upload arbitrary filesystem paths outside the artifact store.

This prevents path traversal and accidental exfiltration.

Validate MIME type and media signature instead of trusting only the filename extension.

---

# 64. Cost accounting

P7 should track:

```text
YouTube API quota units
storage transfer
compute time for upload checksum
network transfer
```

It does not need to estimate advertising revenue; P9 does that.

The system should report cost per published video and cost per failed attempt.

---

# 65. P7 → P9 analytics handoff

Once a video is verified, publish an internal event such as:

```json
{
  "event": "youtube_video_published",
  "youtube_video_id": "abc123",
  "publish_attempt_id": "attempt_123",
  "experiment_id": "exp_123",
  "content_arm_id": "arm_456",
  "published_at": "2026-10-07T12:00:00Z",
  "metadata_version": "p7-v1"
}
```

P9 uses this as the anchor for analytics collection.

P7 must not fetch or calculate long-term performance metrics as part of publication success.

---

# 66. P7 → P8 handoff

P8 should be able to discover:

```text
video_id
track IDs / release IDs
publication timestamp
channel
playlist
experiment
content arm
```

P7 should not create Spotify release metadata.

---

# 67. Test strategy

## Unit tests

Cover:

```text
idempotency-key calculation
manifest canonicalization
metadata validation
title validation
description normalization
schedule timezone conversion
past-date rejection
privacy/schedule consistency
synthetic disclosure mapping
audience mapping
quota estimation
retry classification
backoff bounds
state transition validity
playlist operation validation
```

## Property tests

Verify:

```text
same manifest → same idempotency key
changing video checksum → different idempotency key
UTC normalization is stable
no valid schedule can result in public+publishAt inconsistency
state machine cannot skip mandatory gates
negative progress impossible
quota usage never negative
```

---

# 68. Contract tests

Mock official YouTube API responses for:

```text
videos.insert
videos.list
videos.update
thumbnails.set
playlists.insert
playlistItems.insert
playlistItems.list
channelSections.list/insert/update as implemented
```

Contract fixtures should include both success and documented error shapes.

Do not depend on live YouTube for deterministic unit tests.

---

# 69. Integration tests

Run a fully mocked end-to-end path:

```text
P6 package
 ↓
P7 manifest
 ↓
preflight
 ↓
upload session
 ↓
video ID
 ↓
processing
 ↓
thumbnail
 ↓
playlist
 ↓
schedule
 ↓
verification
 ↓
P9 event
```

Verify every state transition and database mutation.

---

# 70. Recovery tests

Explicitly kill the worker at each major step:

```text
before upload
mid-upload
after upload
before video ID persistence
after video ID persistence
before thumbnail
after thumbnail
before playlist insert
after playlist insert
before schedule verification
```

Rerun the worker and ensure no duplicate public video is created.

---

# 71. Live smoke-test sequence

After OAuth setup, use one test video and perform:

```text
1. dry-run validation
2. private upload
3. verify remote state
4. set thumbnail
5. create/attach test playlist if used
6. schedule in future or publish privately as configured
7. reconcile
8. clean up test object according to the configured policy
```

Only after the smoke test passes should autonomous publication be enabled.

---

# 72. Failure test matrix

At minimum test:

```text
401 invalid credential
403 permission denied
403 unverified project restriction
429 rate limit
quota exhausted
500
502
503
network disconnect
DNS failure
upload timeout
invalid media
invalid thumbnail
playlist missing
video processing rejected
scheduling timestamp invalid
```

Expected result for every row must be defined as one of:

```text
retry
pause
quarantine
manual review
repair
```

There must not be an ambiguous fallback of "retry forever".

---

# 73. Readiness report

Implement a command/API such as:

```text
youtube publish readiness
```

It should return:

```text
OAuth: PASS
Channel mapping: PASS
API audit/public visibility: PASS/FAIL
Quota: PASS
Private smoke test: PASS
Resumable upload recovery: PASS
Thumbnail: PASS
Playlist: PASS
Scheduling: PASS
Synthetic disclosure: PASS
Reconciliation: PASS
Alerts: PASS
```

Overall status:

```text
NOT_READY
PRIVATE_READY
STAGED_READY
AUTONOMOUS_READY
```

---

# 74. Rollout sequence

## Step P7.1 — Domain/contracts

Implement:

```text
enums
entities
publish manifest
state machine
```

No real API writes yet.

## Step P7.2 — persistence

Create tables/migrations/repositories.

## Step P7.3 — OAuth

Implement secure credential references and validation.

## Step P7.4 — client abstraction

Implement a small provider interface around official YouTube API calls.

## Step P7.5 — dry-run preflight

No external writes.

## Step P7.6 — resumable upload

Implement upload session state/retry/recovery.

## Step P7.7 — remote reconciliation

Verify upload/processing status.

## Step P7.8 — thumbnail

Implement set + reconciliation.

## Step P7.9 — playlist

Implement create/attach/reconcile.

## Step P7.10 — scheduling/publication

Implement private/scheduled/public states.

## Step P7.11 — synthetic-media/audience fields

Wire configured policy into the YouTube request.

## Step P7.12 — monitoring and repair

Implement dashboards, alerts, repair endpoints.

## Step P7.13 — live smoke tests

Run against a controlled test channel/video.

## Step P7.14 — autonomous enablement

Only after readiness criteria pass.

---

# 75. What the vibe-coding AI may decide

The implementation AI may choose:

- exact HTTP client library;
- Google API client wrapper style;
- async vs synchronous worker internals;
- ORM or query layer;
- exact database indexes beyond required ones;
- exact queue implementation if one already exists;
- exact retry library;
- exact observability stack;
- exact secret-store integration;
- internal class names;
- chunk size defaults;
- exact API route naming if existing conventions require it.

Provided that external behavior and the contracts in this document remain intact.

---

# 76. What the vibe-coding AI must not silently change

Do not silently change:

- P6 → P7 contract semantics;
- publish-manifest immutability;
- idempotency requirements;
- private-first rollout model;
- public-publish readiness gate;
- OAuth secret handling;
- synthetic-media disclosure propagation;
- explicit audience designation;
- schedule/private consistency;
- duplicate-publication protection;
- resumable-upload recovery;
- remote reconciliation;
- playlist concurrency handling;
- provenance linkage;
- experiment-integrity rules;
- quota protection;
- append-only audit events;
- human exception boundaries;
- P7 → P9/P8 handoff.

If a change affects these semantics, record it in the repository decision log and update this plan/version rather than implementing an undocumented alternative.

---

# 77. Reasonable flexibility

The plan does not dictate:

- one UI framework;
- one database ORM;
- one queue;
- one monitoring system;
- one secret manager;
- one exact chunk size;
- one exact retry coefficient;
- one exact schedule polling interval.

Those are engineering choices as long as the observable behavior remains safe and deterministic.

---

# 78. Acceptance criteria

P7 is complete only when all of the following pass.

## AC-01 — P6 ingestion

A valid P6 package can be accepted and snapshotted as an immutable P7 publish manifest.

## AC-02 — Preflight

Invalid packages are blocked before external writes.

## AC-03 — Idempotency

Repeated execution of the same publish command cannot create multiple public videos.

## AC-04 — Resumability

An interrupted large upload can resume or safely reconcile without duplicate publication.

## AC-05 — Video mapping

The resulting YouTube video ID is stored and linked to the experiment/content lineage before downstream work proceeds.

## AC-06 — Processing verification

P7 does not mark a video verified until YouTube reports a satisfactory processed state.

## AC-07 — Metadata reconciliation

Submitted metadata is verified against remote state and discrepancies are classified.

## AC-08 — Synthetic disclosure

The configured synthetic-media decision is carried into the YouTube API request.

## AC-09 — Audience designation

The made-for-kids setting is explicit and never guessed by P7.

## AC-10 — Scheduling

Scheduled publication uses private status and a valid future timestamp.

## AC-11 — Thumbnail

The expected thumbnail is uploaded and verified.

## AC-12 — Playlist

Requested playlist creation/attachment is completed and reconciled.

## AC-13 — Quota

Provider quota/rate-limit conditions result in bounded, observable behavior.

## AC-14 — Recovery

Worker crashes at each critical point can be recovered without duplicate public publication.

## AC-15 — Security

No secrets appear in logs, job payloads, audit events or API responses.

## AC-16 — Provenance

A published video can be traced through P7 → P6 → P5 → P4 → P3 → P2 → P1.

## AC-17 — Experiment integrity

P7 does not silently alter P3-designated experimental variables.

## AC-18 — P9 event

Verified publication emits a complete event consumable by P9.

## AC-19 — Readiness

The system can report whether the environment is ready for private, staged or autonomous public publishing.

---

# 79. Example complete lifecycle

```text
P6 package ready
       ↓
P7 receives package
       ↓
validate checksum
       ↓
validate target channel
       ↓
validate metadata
       ↓
validate disclosure
       ↓
validate audience
       ↓
validate schedule
       ↓
freeze publish manifest
       ↓
reserve idempotency key
       ↓
start upload
       ↓
resumable transfer
       ↓
YouTube returns video ID
       ↓
persist video mapping
       ↓
wait for processing
       ↓
verify metadata
       ↓
set thumbnail
       ↓
attach playlist
       ↓
schedule/private/public state
       ↓
verify remote state
       ↓
emit youtube_video_published
       ↓
P9 begins analytics collection
```

---

# 80. Definition of done

P7 is done when the system can take a valid P6 publish package and autonomously execute the entire YouTube publication lifecycle with durable state, explicit safety gates, bounded retries, duplicate protection, provenance, reconciliation and observability.

The end-state property is:

> **A human should be able to configure the YouTube account and publication policy once, after which routine uploads should require no manual YouTube Studio operation, while any exceptional or risky condition is surfaced as an explicit, auditable human task.**

P7 is therefore not merely an API uploader. It is the **transactional publishing control plane** that converts P6 content into a verified YouTube publication and creates the canonical platform event consumed by P9.
