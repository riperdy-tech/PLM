# CONTRACT REGISTRY — the ten frozen cross-phase seams

> **Authority.** For every cross-phase seam, this file is the first authority in the
> order `registry → owning phase plan → architecture.md → P0 master plan` (R2,
> `docs/P0_master_integration_implementation_plan.md` §0/§9). Nine independently
> written phase specs become one system only if both sides of each seam are tested
> against the *same* field list — this file. **Amend the registry, never the prose**
> (P0 §9); a wire change lands here first (as an ADR), then in the producer and the
> consumer in the same change.

**Status vocabulary** (what the seam looked like *before* its ruling):

- **compatible** — both sides agree; only gaps or loose ends remain.
- **drift** — same intent, different names or shapes; a mechanical mapping is needed.
- **broken** — the two sides cannot connect as written.
- **open** — a required direction of communication does not exist yet.
- **out of scope** — deliberately deferred to a future phase (R12).
- **missing** — the spec itself has no home (no PHASE-P0 plan file).

**Field names.** Each entry lists the frozen field list under the *producer's*
name for each field. Where the consumer uses a different internal name, the
mapping is listed. Where a name genuinely does not exist in any phase document or
the master plan, the entry says
`unspecified - must be resolved at the <phase> freeze` — never a guess.

**Seam tests.** Every entry names the test that must exist at
`tests/contract/<name>.py`. Status **pending** means it does not exist yet (most do
not; Wave 0 shipped the template `tests/contract/_TEMPLATE_test_seam.py`, whose
registry check fails for an entry this file does not define). A seam test asserts
the producer's serialised payload satisfies the consumer's expected schema, field
names first (see `tests/support/contract.py`).

## Summary

| Seam | Direction | Status before ruling | Rulings | Seam test |
|---|---|---|---|---|
| C1 | PHASE-P1 → PHASE-P2 (market opportunity) | compatible (two gaps) | R14, R15 | `test_c1_market_to_ar.py` — pending |
| C2 | PHASE-P2 → PHASE-P3 (production directive) | broken (6 of 12 fields mismatch) | R4 | `test_c2_ar_to_experiments.py` — pending |
| C3 | PHASE-P3 → PHASE-P4 (track spec) | drift (names and missing keys) | R5 | `test_c3_experiments_to_music.py` — pending |
| C4 | PHASE-P4 → PHASE-P5 (candidate ready) | broken (event OK, retrieval missing) | R6 | `test_c4_music_to_qa.py` — pending |
| C5 | PHASE-P5 → PHASE-P6 (release manifest) | broken (different shapes entirely) | R7, R8 | `test_c5_qa_to_media.py` — pending |
| C6 | PHASE-P6 → PHASE-P7 → PHASE-P8 (publish/distribution identity) | drift (linkage incomplete) | R9 | `test_c6_media_to_publishing.py` — pending |
| C7 | PHASE-P8 → PHASE-P9 → PHASE-P2 (feedback loop) | open (loop does not close) | R10, R11 | `test_c7_distribution_to_analytics.py` — pending |
| C8 | PHASE-P9 → PHASE-P10 (deferred feature seam) | out of scope (P10 deferred) | R12 | `test_c8_analytics_to_p10.py` — pending |
| C9 | Cross-phase storage layout | drift (three divergent prefixes) | R13 | `test_c9_storage_namespace.py` — pending |
| C10 | Missing engineering prelude (PHASE-P0) | missing (no P0 plan file) | R16 | `test_c10_engineering_prelude.py` — pending |

---

## C1 · PHASE-P1 → PHASE-P2 (market opportunity)

**Producer:** PHASE-P1 (`docs/P1_market_intelligence_implementation_plan.md`) §20
(opportunity object), §25 (API contract for P2), §14.2 (taxonomy dimensions).
**Consumer:** PHASE-P2 (`docs/P2_autonomous_ar_engine_implementation_plan.md`) §4
(dependency model), §46 (`arm_feature_snapshot`).

**Endpoint / event:** `GET /market/opportunities` and
`GET /market/opportunities/{arm_id}` (P1 §25), under base path `/market/v1` with a
service token from `plm_platform` (R15). Response envelope:
`items`, `next_cursor`, `as_of`, `score_version` (P1 §25).

**Producer fields (frozen names, P1 §20):**

```text
arm_id
market_opportunity_score
confidence
as_of
taxonomy            {genre, subgenre, mood, use_case, language, vocal_mode,
                     duration_bucket, visual_world, title_archetype}
components          {demand, growth, competitor_velocity, competition,
                     audience_quality, cross_platform, production_fit}
evidence            {observation_count, source_count, top_observation_ids}
score_version
```

**Consumer names.** P2 §46 persists `p1_score`, `p1_confidence`, `demand_score`,
`growth_score`, `competition_score`, `cross_platform_score`,
`production_fit_score`, `freshness_seconds`, `feature_json`, `feature_version`.
Mapping (P2 adapter, P2 §114 P2.2): `market_opportunity_score → p1_score`,
`confidence → p1_confidence`, `components.* → *_score`. P2 §4 also requires
"freshness": there is **no `freshness` field on the P1 wire** — P1 §21 derives
freshness buckets from `as_of`; `freshness_seconds` is P2-internal (P2 §46).

**Status before ruling:** compatible — two gaps: no base path or auth stated
(P1 §25); P1's taxonomy dimension set disagreed with its own AC-05.

**Ruling:** R14, R15.

**Required amendments (must not be missed):**

- P1 AC-05 is corrected from its 10 dimensions to the 13 of §14.2 —
  `VocalGender`, `BPMRange` and `AudienceIntent` are reinstated (R14).
- P1 mints and owns `arm_id` (identity, taxonomy, market score); P2 owns arm
  *state* (P2 §45), posteriors (P2 §47) and exposure (P2 §54) (R15).
- Base path `/market/v1`; auth via service token from `plm_platform` (R15).

**Seam test:** `tests/contract/test_c1_market_to_ar.py` — **pending** (named by the
P1 gate in the master plan; does not exist yet).

---

## C2 · PHASE-P2 → PHASE-P3 (production directive)

**Producer:** PHASE-P2 `docs/P2_autonomous_ar_engine_implementation_plan.md` §32
(production directive), §52 (`production_directive` table), §120 (P2 output
expected by P3).
**Consumer:** PHASE-P3 `docs/P3_content_genome_experiment_framework_implementation_plan.md`
§10 (P2 input contract), §11 (P2-to-P3 translation).

**Endpoint / event:** the directive handoff; queued directives are served by
`GET /ar/directives` (P2 §71).

**Producer fields (frozen names, P2 §32, as amended by R4):**

```text
directive_id
decision_id
arm_id
allocation_type
priority
quantity
brief               {genre, mood, use_case, language, vocal_mode,
                     duration_bucket, visual_world}
expected_value
uncertainty
budget_limit
market_context
selection_reason
directive_version
policy_version
expires_at
```

P2 §52 (`production_directive` table) additionally persists: `id`, `status`,
`brief_json`, `created_at`, `consumed_at` — persistence only, not wire fields.
P2 §120 rounds the same payload out as: arm_id, allocation_type, priority,
quantity, creative search region, expected value, uncertainty, decision_id,
policy version, expiration.

**Consumer names (P3 §10):** `directive_id`, `policy_version`, `decision_id`,
`arm_id`, `selection_reason`, `market_context`, `priority`,
`requested_experiment_count`, `required_constraints`, `recommended_attributes`,
`exploration_level`, `budget_limit`, `production_deadline`. P3 must reject a
directive with no `arm_id` or no valid policy/decision identity (P3 §10).

**Status before ruling:** **broken** — field-level mismatch on six of twelve
fields (P2 §32/§120 vs P3 §10).

**Ruling:** R4.

**Required amendments (must not be missed):**

- P2's directive is the wire format. P2 extends it with `budget_limit`,
  `market_context`, `selection_reason` and a versioned `directive_version`
  (`expected_value` and `uncertainty` are already in P2 §32; P3 §4.5 wants them).
- P3's adapter maps (R4, P3 §11): `requested_experiment_count ← quantity`;
  `exploration_level ← allocation_type` (enum map); `production_deadline ←
  expires_at`; `required_constraints` / `recommended_attributes ← arm hard
  constraints + creative search region`.
- Frozen before either P2.1 or P3.4 starts (P0 §6, pre-step W2.0).

**Seam test:** `tests/contract/test_c2_ar_to_experiments.py` — **pending** (this is
the template's worked copy target).

---

## C3 · PHASE-P3 → PHASE-P4 (track spec)

**Producer:** PHASE-P3 `docs/P3_content_genome_experiment_framework_implementation_plan.md`
§20 (track-level specification), §44 (content package model), §45 (downstream
interface P4), §58 (`track_specs`).
**Consumer:** PHASE-P4 `docs/P4_music_generation_factory_implementation_plan.md` §7
(inputs from P3), §8 (request boundary).

**Endpoint / event:** `GET /content-packages/{package_id}` (P3 §45); the
`music_specs[]` array of the content package (P3 §44).

**Frozen field list (the union, canonical names = P4 §7; P3 emits every key — R5):**

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

**P3-side names to map (the drift R5 resolves):** `role` (P3 §20) →
`track_role`; `duration_range` (P3 §20) → `requested_duration`;
`vocal_profile` → `vocal_spec`; `instrument_profile` → `instrumentation_spec`
(the `*_profile` / `*_spec` drift). P3 §20 also carries `genome_reference`,
`lyrical_theme`, `hook_profile`, `energy_target`, `negative_constraints`; P3 §58
adds `sequence_position`, `thematic_brief`, `creative_constraints`, `status`.
These ride inside the constraints/creative-latitude keys above.

**Consumer rejects (P4 §7):** missing `track_spec_id`; missing `experiment_id`;
invalid hard constraints; no permitted provider; negative budget;
`candidate_target < 1`.

**Status before ruling:** drift — `role`/`track_role`,
`duration_range`/`requested_duration`, `*_profile`/`*_spec`; and P4 needs
`candidate_target`, `priority`, `budget_limit`, `provider_policy`, which P3 does
not emit.

**Ruling:** R5.

**Required amendments (must not be missed):**

- P3 derives (documented rules): `candidate_target`, `priority`, `budget_limit`
  from the budget envelope (P3 §34/§44); `requested_duration` from
  `duration_range`; `provider_policy` from the experiment's provider constraints.
- P4 must reject only what P4 §7 says it rejects — no extra strictness.

**Seam test:** `tests/contract/test_c3_experiments_to_music.py` — **pending**.

---

## C4 · PHASE-P4 → PHASE-P5 (candidate ready)

**Producer:** PHASE-P4 `docs/P4_music_generation_factory_implementation_plan.md`
§67 (API endpoints), §68 (P5 handoff contract), §65 (`rights_snapshots` table).
**Consumer:** PHASE-P5 `docs/P5_quality_rights_provenance_release_gate_implementation_plan.md`
§7 (P4 input contract), §33 (provider license snapshot), §117 (unknown
commercial rights — the scenario that quarantines when the snapshot is thin).

**Endpoint / event:** event `music_candidate_ready_for_p5`, `event_version` `"1"`
(P4 §68).

**Producer fields (frozen names, P4 §68):**

```text
event_type                  = "music_candidate_ready_for_p5"
event_version               = "1"
candidate_id
artifact_id
track_spec_id
experiment_id
content_arm_id
provider_id
model_id
measured_audio              {duration_seconds, sample_rate, channels}
rights_status
provenance_complete
```

**Consumer requirement beyond the event (P5 §7):** P5 must be able to retrieve,
through internal APIs: the full candidate record, artifact manifest, provenance
chain, prompt metadata, generation attempts and license snapshot — without
reconstructing generation history from raw provider logs.

**Status before ruling:** **broken** — event compatible; retrieval missing.

**Ruling:** R6.

**Required amendments (must not be missed) — the P4.1/P4.2 amendment:**

- P4 adds read endpoints: `GET /music/candidates/{id}/attempts`,
  `GET /music/candidates/{id}/license`,
  `GET /music/candidates/{id}/artifacts`,
  `GET /music/candidates/{id}/provenance`.
- P4 extends `rights_snapshots` (P4 §65 currently: `id`, `attempt_id`,
  `provider_id`, `commercial_use_status`, `basis`, `license_reference`,
  `policy_version`, `checked_at`) with: `plan_or_tier`,
  `account_identity_ref`, `terms_version`, `created_at`, `downloaded_at`.
- Mapping to P5 §33's prose requirements: plan/tier → `plan_or_tier`; account
  identity reference → `account_identity_ref`; commercial-use terms version →
  `terms_version`; creation timestamp → `created_at`; download timestamp →
  `downloaded_at`.
- Must land before P5.3 starts; without these, P5 §117 defaults every candidate
  to quarantine.

**Seam test:** `tests/contract/test_c4_music_to_qa.py` — **pending**.

---

## C5 · PHASE-P5 → PHASE-P6 (release manifest)

**Producer:** PHASE-P5 `docs/P5_quality_rights_provenance_release_gate_implementation_plan.md`
§77 (release manifest), §78 (downstream gating contract), §94 (API surface),
§138 (P5-to-P6 interface), §51 (candidate disposition).
**Consumer:** PHASE-P6 `docs/P6_playlist_visual_video_factory_implementation_plan.md`
§4 (P6 input contract) — **amended by R7: P6 assembles the playlist-level
manifest** rather than receiving one.

**Endpoint / event:** per-track `GET /qa/release-manifests/{candidate_id}` (P5
§94) — P6 calls it for each track of the playlist it assembles.

**Producer fields (frozen names, P5 §77; `p5_manifest_id` added by R7):**

```text
p5_manifest_id
release_manifest_version
candidate_id
canonical_artifact_id
experiment_id
track_spec_id
disposition
technical_status
commercial_use_status
provenance_status
ai_disclosure_required
rights_risk
policy_status
evaluation_id
evaluation_policy_version
warning_codes
evidence_bundle_id
```

P5 §138's minimum P6 view: `candidate_id`, `artifact_id`, `track_spec_id`,
`experiment_id`, `content_arm_id`, `disposition`, `warnings`,
`ai_disclosure_required`, `rights_status`, `provenance_status`,
`evidence_bundle_id`.

**Consumer fields (P6 §4, as amended):** playlist-level manifest —
`release_manifest_id`, `experiment_id`, `content_arm_id`, `brand_id`,
`tracks[]` (`song_id`, `artifact_id`, `title`, `duration_seconds`, `audio_uri`,
`audio_checksum`, `provenance_status`, `release_constraints`),
`required_duration_minutes`, `presentation_profile`, `visual_profile`,
`thumbnail_profile`, `metadata_profile`,
`policy_flags` (`ai_disclosure_required`).

**Status before ruling:** **broken** — different shapes entirely; no phase owned
assembling the playlist-level manifest; vocabulary drifted (`cleared` vs `PASSED`).

**Ruling:** R7, R8.

**Required amendments (must not be missed):**

- P5 stays per-candidate and gains a stable `p5_manifest_id` (R7).
- **P6 assembles** the playlist-level manifest: it reads P3's `playlist_spec`
  and `brand_profiles` (P3 §19/§26 — P3 references the brand as
  `brand_profile_id`); `required_duration_minutes` comes from the playlist spec.
- The four `*_profile` versions come from P3's brand/spec tables, **not** from
  P5. P6 §4 is amended accordingly.
- Dispositions: P6 accepts only `PASSED` / `PASSED_WITH_WARNINGS`; it rejects
  `QUARANTINED` / `REJECTED` track(s) except through the audited override
  mechanism (P5 §79; `CLEARED_BY_OVERRIDE`).
- `cleared` is not a state (R8). Canonical verdict vocabulary is P5 §51's four
  dispositions plus `CLEARED_BY_OVERRIDE`; the word "cleared" in P6/P8 prose
  means "`PASSED`, or `PASSED_WITH_WARNINGS` where that phase's own policy
  permits warnings".

**Seam test:** `tests/contract/test_c5_qa_to_media.py` — **pending**.

---

## C6 · PHASE-P6 → PHASE-P7 → PHASE-P8 (publish and distribution identity)

**Producer:** PHASE-P6 `docs/P6_playlist_visual_video_factory_implementation_plan.md`
§5 (output contract / media package), §58 (architectural handoff to P7); then
PHASE-P7 `docs/P7_youtube_publishing_engine_implementation_plan.md` §65
(`youtube_video_published` event), §66 (P7 → P8 handoff).
**Consumer:** PHASE-P7 §9 (publish package contract from P6), §10 (publish
manifest); then PHASE-P8 `docs/P8_dsp_distribution_engine_implementation_plan.md`
§11 (release compiler / `CanonicalReleasePackage`), §39 (release provenance).

**Endpoint / event:** hop 1 — the media package contract (P6 §5); hop 2 — event
`youtube_video_published` (P7 §65).

**Hop 1 producer fields (P6 §5):**

```text
media_package_id
release_manifest_id
experiment_id
brand_id
video                {master_uri, checksum, duration_seconds, render_profile}
audio                {master_uri, checksum}
thumbnails[]         {id, uri}
tracklist
chapters
metadata_candidates
short_source_assets
render_report
status
```

P7 §9 expects the semantics, and its own names may differ by design:
`publish_package_id`, `content_bundle_id`, `experiment_id`, `content_arm_id`,
`video_master_asset_id`, `video_sha256`, `thumbnail_asset_id`,
`thumbnail_sha256`, `primary_title`, `title_variants`, `final_description`,
`keywords/tags if used`, `language`, `category_id`, `playlist_intent`,
`playlist_title_hint`, `chapter_map`, `tracklist`, `brand_profile_id`,
`contains_synthetic_media`, `made_for_kids`, `scheduled_publish_at`,
`publish_timezone`, `license_intent`, `provenance_manifest_ref`,
`p6_manifest_version`, `created_at`.

**Hop 2 producer fields (P7 §65, as amended by R9):**

```text
event                       = "youtube_video_published"
youtube_video_id
publish_attempt_id
experiment_id
content_arm_id
published_at
metadata_version
experiment_family_id        (added by R9)
content_genome_id           (added by R9)
production_directive_id     (added by R9)
media_package_id            (added by R9)
```

P7 §66's P8-visible set: video_id, track IDs / release IDs, publication
timestamp, channel, playlist, experiment, content arm.

**Status before ruling:** drift — P6→P7 workable; experiment linkage incomplete
(P7 §9 explicitly tolerates field-name drift if semantics match; neither
producer sent the identity fields P9 §38 requires).

**Ruling:** R9.

**Required amendments (must not be missed):**

- Both the P7 §65 event **and** P8's P8 → P9 record (P8 §71, see C7) must carry:
  `experiment_id`, `experiment_family_id`, `content_arm_id`, `content_genome_id`,
  `production_directive_id`, `media_package_id`. Without them the learning loop
  silently breaks at the last hop (P9 §38 marks missing linkage
  `experiment_context = UNKNOWN`).
- P7's §12 state list gains `PROCESSING_TIMEOUT` (a state the machine needs but
  §12 never listed).
- P8 consumes P5 clearance plus the P6 manifest and P7's publication record;
  P8's release provenance (P8 §39) references `p5_manifest_id` and
  `p6_manifest_id`; `source_manifest_ids` in P8 §11 is the compiled snapshot.

**Seam test:** `tests/contract/test_c6_media_to_publishing.py` — **pending**.

---

## C7 · PHASE-P8 → PHASE-P9 → PHASE-P2 (the feedback loop)

**Producer:** PHASE-P8 `docs/P8_dsp_distribution_engine_implementation_plan.md`
§71 (P8 → P9 contract), §72 (metric separation), §51 (report ingestion); then
PHASE-P9 `docs/P9_analytics_economics_engine_implementation_plan.md` §38/§39
(experiment linkage, outcome record), §153 (experiment outcome schema), §155.
**Consumer:** PHASE-P9 §38 (experiment linkage is *required*); then PHASE-P2
`docs/P2_autonomous_ar_engine_implementation_plan.md` §71 (`POST /ar/results`),
§72 (result ingestion contract), §73/§74 (idempotency).

**Endpoint / event:** P8 → P9 stable distribution record (P8 §71); P9 →
`POST /ar/results` (P2 §71/§72).

**Leg 1 producer fields (P8 §71, as amended by R9):**

```text
release_id
artist_id
release_version
isrcs[]
provider
deliveries[]                 {store, external_release_id, status,
                              first_delivered_at}
source_package_hash
status
+ R9 identity fields (see C6): experiment_id, experiment_family_id,
  content_arm_id, content_genome_id, production_directive_id, media_package_id
```

**Leg 2 consumer schema (P2 §72, names P2 expects):**

```text
experiment_id
arm_id
content_id
observation_window
status
observed_at
raw_metrics                  {views, watch_time_minutes,
                              average_percentage_viewed, subscriber_gain,
                              estimated_revenue}
normalized_reward
reward_version
attribution_confidence
```

**Status before ruling:** **open** — the loop does not close. P8→P9 is fine; P9
defines no P2-facing contract though P2 §72 expects results; raw-statement
ownership overlapped (P8 §51 vs P9 §53–§54).

**Ruling:** R10, R11.

**Required amendments (must not be missed):**

- R10 — P8 stores and versions raw provider reports (P8 §51: `raw_report`,
  `provider_period`, `received_at`, `report_checksum`) and exposes references;
  P9 owns parsing into canonical facts (P9 §53/§54) and the immutable archive of
  what it ingested; **P8 must not compute revenue** (P8 §51/§72).
- R11 — **P9 posts normalized rewards to P2's `POST /ar/results`.** P9 owns the
  reward arithmetic: `normalized_reward`, `reward_version`, window, maturity,
  attribution confidence, quality status. P2 owns posterior updates (P2 §74/§75).
  Mapping onto P2 §72's names: window → `observation_window`; maturity →
  `status`; attribution confidence → `attribution_confidence`.
- One field name is absent from both sources:
  **`quality status` (R11) — unspecified - must be resolved at the PHASE-P2
  freeze** (P2 §72 has no such field; P9 §153's `measurement_status` is the
  experiment-outcome record, not the reward post).
- This is the loop-closing contract; tested end to end in Wave 6.

**Seam test:** `tests/contract/test_c7_distribution_to_analytics.py` — **pending**.

---

## C8 · PHASE-P9 → PHASE-P10 (deferred seam)

**Producer:** PHASE-P9 `docs/P9_analytics_economics_engine_implementation_plan.md`
§86 (P10 feature view), §88 (analytics API), §155 (P9 → P10 contract), §156
(semantics).
**Consumer:** PHASE-P10 — deferred, not cancelled (R12; architecture PART XXVI's
P10 is out of this build, and P2 owns allocation here).

**Endpoint / event:** `GET /analytics/p10/features` (P9 §88).

**Producer fields (P9 §155 minimum contract, frozen now for the future consumer):**

```text
unit_id
experiment_id
arm_id
content_genome_id
observation_window
maturity
primary_outcome              {metric, value, status}
secondary_outcomes           {views, watch_time_hours, subscribers_gained,
                              spotify_streams}
quality                      {data_completeness, fidelity, contamination,
                              attribution_confidence}
```

P9 §86's feature record variant additionally carries `metrics` (`views`,
`watch_time_hours`, `subscriber_conversion`, `revenue`, `net_contribution`) and
`uncertainty` (`revenue_status`, `confidence`); P9 §86 says the exact field shape
may evolve, but the semantics must remain explicit.

**Status before ruling:** out of scope — deferred seam, named for its future
consumer.

**Ruling:** R12.

**Required amendments:** none for this build. P9 §86/§88 must still exist as
frozen surfaces; P10 consumes them when it is scheduled.

**Seam test:** `tests/contract/test_c8_analytics_to_p10.py` — **pending** (kept
so the seam does not rot before P10 starts).

---

## C9 · Cross-phase storage layout

**Producers:** every phase that writes artifacts. The three divergent layouts that
collided: PHASE-P4 `docs/P4_music_generation_factory_implementation_plan.md` §29
(`music/{brand_id}/{experiment_id}/…`); PHASE-P5 §193
(`artifacts/candidates/…`, `qa/evaluations/…`); PHASE-P6
`docs/P6_playlist_visual_video_factory_implementation_plan.md` §42
(`media/{brand}/{experiment}/{package_id}/…`).
**Consumers:** every phase that reads artifacts, always by the URI recorded in a
database row.

**Endpoint / event:** none — this is the storage namespace, implemented by
W0.6 `packages/plm_platform/storage.py`.

**Frozen prefixes (R13):**

```text
market/  experiments/  music/  qa/  media/  reports/  analytics/
```

One namespace; producers write immutable content-addressed artifacts; consumers
read only by the URI recorded in a database row; checksums are mandatory on every
artifact and verified on read.

**Status before ruling:** drift — three divergent prefixes.

**Ruling:** R13.

**Required amendments (must not be missed):**

- P4 §29's `music/{brand_id}/{experiment_id}/{track_spec_id}/{candidate_id}/{artifact_id}/…`
  is the `music/` subtree of the single namespace.
- P6 §42's `media/{brand}/{experiment}/{package_id}/…` is the `media/` subtree.
- The mapping of P5 §193's `artifacts/candidates/…` subtree onto an R13 prefix is
  **unspecified - must be resolved at the PHASE-P5 freeze** (its `qa/` subtree is
  already conformant).

**Seam test:** `tests/contract/test_c9_storage_namespace.py` — **pending**.

---

## C10 · Missing engineering prelude (PHASE-P0)

**Producer:** none — there is no PHASE-P0 plan file (P0 §5 C10; seven phase briefs
note infrastructure that is assumed but specified nowhere).
**Consumer:** every phase.

**Endpoint / event:** none.

**Frozen fields:** none — R16 is a scope ruling, not a payload. The assumed
infrastructure: OAuth applications, secret store, durable workflow engine,
FFmpeg, audit states.

**Status before ruling:** missing.

**Ruling:** R16.

**Required amendments (must not be missed):**

- Wave 0 (W0.1–W0.12, `docs/P0_master_integration_implementation_plan.md` §6) *is*
  the engineering half of P0.
- The account/OAuth/audit half is the human task list in P0 §10 — it blocks only
  live-mode enablement, never the mock-first build.

**Seam test:** `tests/contract/test_c10_engineering_prelude.py` — **pending**
(asserts the Wave 0 platform surfaces the phases assume import and self-describe).
