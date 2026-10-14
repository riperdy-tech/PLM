# PLM — Review Findings & Remediation Execution Plan (rev 2)

## Context

**Goal reference:** `docs/architecture.md` and `docs/P0_master_integration_implementation_plan.md` §1: a P1 market observation flows through every phase to a published video + distributed release, measured revenue/cost is attributed back to the arm, and **that outcome updates the allocation policy that chose the arm**, with no human step in the middle.

**Why:** git history and plan checkboxes claim waves W0–W6 complete and "100% tests green". Audits of all 9 services + platform + tests (critical findings independently re-verified by the user against `main`) show domain logic is substantial (P1 scoring, P2 Normal–Normal Thompson bandit, P9 calculator are real) but the code doesn't yet prove the pipeline works: stubs hide in fallback values, contract/e2e tests check hand-built payloads, nothing deploys or orchestrates the chain, the reward never reaches P2, and the dashboard is scripted.

**Approval scope (user decision):** this plan authorizes **R0 only**. Each later stage is approved separately against its own evidence gate. Remaining uncertainty is resolved at those gates, not up front.

**Note on `AGENTS.md`:** the inherited `C:\Users\riper\Downloads\AGENTS.md` is for an equity-screening system. Its principle 1 (fix the systemic root cause, never patch the example) applies here; the rest does not. R0 adds a PLM-specific `AGENTS.md`.

---

## Part 1 — Systemic root causes

| ID | Root cause | Violates |
|---|---|---|
| RC1 | Silent fallback defaults at seams: missing data becomes a passing value | P0 §2 no silent substitution; unknown ≠ zero ≠ not-ready |
| RC2 | Tests check copies, not integration: contract schemas defined in tests; e2e hand-builds 6 of 9 hand-offs | P0 §3 Review Focus 3 |
| RC3 | No runtime: only P1 worker deployed; no pipeline workflow; P3 has no transport; P9 never posts to P2 | PART XIV, P0 §1 |
| RC4 | Provider choice and side-effect permission not separated or enforced; `DRY_RUN` stored, never checked | P0 §2 provider neutrality, staged rollout |
| RC5 | Measurement/economics semantics undefined: windows summed, no currency, float money, proxy reward treated as value, no decision log | PARTS VII, XVI, XXI |
| RC6 | Status reporting outruns reality: P0 says "no code exists", checkboxes say done, dashboard shows literal KPIs as live | P0 §7 |

---

## Part 2 — Findings (C=critical, H=high, M=medium)

### Cross-phase chain
- **C** `services/analytics/api/routes.py:183`: the feedback route never supplies `http_client`/`ar_results_url`, so the reward is never sent. `evaluation/feedback.py:329` logs a failed POST and records the reward as posted anyway. The loop is open. (RC3)
- **C** `tests/e2e/test_end_to_end_chain.py`: runs in-process on SQLite. It hand-builds the P1 opportunity (:118), the P2 directive (:164), the C4 event and P5 manifest (:243), the P7 event (:323) and the P8 release (:342), and uses literal P9 revenue (:390). It writes the reward directly into P2 and never runs a second allocation cycle (:464). (RC2)
- **H** `tests/contract/test_c*.py`: consumer schemas are copied into the tests. There is no C7-leg-2 test (P9 → `/ar/results`). (RC2)
- **H** There is no Temporal pipeline workflow. P4–P9 run synchronously inside the request, and only the P1 worker is in `docker-compose.yml:91`. P3 never consumes `GET /ar/directives`. (RC3)

### P1 Market intelligence
- **C** `connectors/youtube.py:242` `fetch_channels` is never called, and `normalization/youtube.py:90` leaves subscribers unknown. Demand (25% weight) is therefore always unknown.
- **H** Spotify/Trends connectors have no callers, so cross-platform (10%) is always unknown. `cli.py:362` imports `tests.support.http`.
- **M** `features/growth.py:111`: the "prev90d" window is days 90–180 back, and there is no 180d window. `features/arm_features.py:81` computes HHI keyed on video id, not channel. There is no 24h/72h velocity, and weights are not in `MarketSettings`.

### P2 Autonomous A&R
- **H** `modeling/update.py:232`: each observation window of the same content is applied as a separate full-weight update. (RC5)
- **H** The reward is P9's number used as-is (`compose_proxy` is unused). Budget is split equally per slot (`policy/constraints.py:79`).
- **M** `domain/policy.py:288` allows fractions summing below 1. Decay is baked in at update time with `now` = newest observation (`routes.py:166`).

### P3 Experiments
- **C** `experiments/engine.py:141`: any variable except bpm/visual_world gets the literal `variant_{idx}`, so title/thumbnail experiments don't exist. (RC1)
- **H** `engine.py:49` hardcodes two variables that change together, with no isolation. `seed` is discarded (:35) and `datetime.now` is read (:34). `genome/engine.py:159` only has an rnb template. P2 `budget_limit` is ignored (`api/schemas.py:49` default 100).
- **M** Status is set to `VALIDATED` before validation (:184). Treatments 1 and 3 are identical (:120). `ingestion/p2_adapter.py:95` invents version strings.

### P4 Music factory
- **C** `generation/engine.py:121` seeds from `abs(hash(...))`, which Python randomizes per process.
- **C** `api/routes.py:138` uses `InMemoryArtifactStorage`, so stored URIs point at nothing.
- **C** `engine.py:236` hardcodes the rights snapshot as PERMITTED/`pro_tier`. (RC1)
- **H** Live adapters always fail, and the protocol lacks extend/edit/stems/download/license. There is no funnel or ranking. Cost is `credits*0.05`, invented. `budget_limit` is unenforced. There is no artist-name blocking.
- **H** The mock caps audio at 30s, but P5 blocks any duration gap above 1s, so a real P4→P5 run rejects everything.
- **M** `attempts[0]` is always used (`candidates/bank.py:78`, `routes.py:330`). `content_arm_id="arm_placeholder"` (`routes.py:344`).

### P5 Quality gate
- **C** There is no similarity, lyric-similarity, voice or fingerprint detection, and half the P29 hard-stops are absent.
- **H** "LUFS" is RMS−3dB (`evaluators/musical.py:91`). There is no true-peak check, clipping never blocks, and silence is only detected when all-zero.
- **H** Vocabulary mismatch: P4 emits `permitted`, but `rights.py:148` expects `ELIGIBLE`. The caller supplies `audio_path`/`rights_snapshot` (`api/schemas.py:50`). Only 4 of 12 P28 fields are checked, and missing fields don't block. The banned-lyric list is a placeholder.
- **M** Overrides have no authorization check, and there is no hard-stop vs reviewable distinction.

### P6 Media factory
- **C** `rendering/renderer.py:179` renders no video. Checksums are hashes of seed strings, and the QA numbers are hardcoded.
- **C** `rendering/assembler.py:87` treats a missing disposition as `"PASSED"` and falls back on duration and artifact location, so the gate can be bypassed. (RC1)
- **H** Sequencing keeps input order, and the required duration is not enforced. Thumbnails are hashed URIs.
- **M** Tags are hardcoded. Chapter rules are not checked.

### P7 YouTube publishing
- **C** Mock client only, with `MOCK_*_BINARY` byte fallbacks.
- **H** `engine.py:84`: `operating_mode` is never checked. (RC4)
- **M** The route invents a channel profile/OAuth ref, so Gate A always passes. A tz-naive `publish_at` raises a TypeError (`preflight/gates.py:106`).

### P8 Distribution
- **C** `compiler/release_compiler.py:170` builds the ISRC from the track index under a unique constraint, so a second release crashes. `identifiers/isrc.py:38` exists but is unused.
- **C** Mock only. There is no UPC persistence and no P20 catalog. Fallbacks: duration 180s, checksum = sha256(URI).

### P9 Analytics / economics
- **C** `ingestion/youtube.py:15` stores whatever numbers the caller passes. There is no Analytics API client.
- **H** `api/routes.py:143` sums overlapping window snapshots. There is no currency handling, and money is `float`. There is no decision log. P30 is partial.
- **M** `ingestion/distributor.py:64` invents an ISRC when it's missing. The 72h window is missing. Maturity defaults to "mature".

### Platform / dashboard / engineering
- **C** The dashboard is scripted: `dashboard/orchestrator.py:63` and literal data in `dashboard/service.py:29`. (RC6)
- **H** No budget ceiling (`CostTracker` is in-memory). No exception queue. Dashboard CORS is `*` with credentials, and there is no auth on mutating routes.
- **H** `httpx` is dev-only but imported by runtime code. CI runs `mypy packages/` only (`.github/workflows/ci.yml:58`). `P0` §13 status says "No code exists".
- **M** Compose binds all interfaces. Locally, the full test suite showed 1 failure in the first ~50 tests and did not finish within 15 min.
- **Absent:** PART X agent review/LLM router, XII Shorts, XIII community, XXIII multi-brand.

---

## Part 3 — Execution stages

Each stage has its own gate and is approved separately. R3 and R4 may share parallel tasks where interfaces are frozen, but their gates stay separate.

| Stage | Objective | Gate evidence |
|---|---|---|
| **R0** | Truthful status, provider/execution policy, hygiene | below |
| R1 | Shared versioned contracts, fail-closed seams | every seam: real producer → real consumer; missing/invalid/unknown blocks |
| R2 | Engine correctness, determinism, measurement semantics | cross-process determinism; one effective reward per declared observation; reproducible money |
| R3 | Runtime: HTTP transport, Postgres, Temporal, transactional outbox, exception queue, cost ceiling | controlled mock activities; worker kill/resume; duplicate delivery deduped; budget stop; hard-stop not resumable |
| R4 | Audio QA, rights detectors, FFmpeg rendering, artifact storage | golden fixtures; ffprobe-validated media; checksums of produced bytes; UNKNOWN detector ⇒ quarantine |
| R5 | Full-chain Compose e2e + DB-backed dashboard | full-chain gate below |
| R6 | Live adapter conformance and staged rollout | recorded-response tests → read-only/shadow → authorized writes |
| Later | Economic-value estimation, EEV-weighted capital allocation (P10), deferred PARTS X/XII/XIII/XXIII | separate economics gate |

### R0 — Truth baseline (AUTHORIZED SCOPE)

Branch `review/r0-truth-baseline`, one commit per item, stop at Gate R0.

0. **Save write-up**: add `docs/reviews/2026-10-13-system-review-and-remediation-plan.md` (this document).
1. **Test baseline**: run `pytest -m "not integration"` with per-test timeout (`--timeout` via `pytest-timeout` dev dep) and record pass/fail/duration. Identify the failing test(s) and any hang, and record them in `STATUS.md`. Fix only if trivially in scope. Otherwise list them as known failures with owners/stage.
2. **Capability inventory**: add `docs/STATUS.md`, with each capability per phase marked `real | mock | stub | absent` plus a file reference, seeded from Part 2. Correct `P0` §13 "Status" and the §6 checkboxes so they point at `STATUS.md` instead of claiming done. Add `docs/STATUS.md` to the README docs list.
3. **Dashboard labeling**: show a persistent "DEMO DATA — not connected to pipeline" banner, and return `data_source: "demo"` on every API payload in `packages/plm_platform/dashboard/`. Rename the orchestrator to `DemoOrchestrator`. Correct the dashboard design spec's claim that it "executes the 10-step chain".
4. **Provider mode × execution policy** (two concepts, in `packages/plm_contracts`):
   - `ProviderMode {MOCK, REAL}`, set per integration. It selects which implementation serves the call.
   - `ExecutionPolicy {READ_ONLY, DRY_RUN, SHADOW, LIVE}`, set per side-effecting capability. It decides whether an external write may happen. `SHADOW` means live reads and decisions are evaluated and recorded but don't control production.
   - **External-write guard** in `plm_platform`: a single `authorize_external_write(capability, idempotency_key, cost_estimate)` choke point. It requires `ExecutionPolicy.LIVE` for that capability, an explicit authorization record, a budget check (a stub interface now, a ledger in R3) and an idempotency key. No adapter may infer permission from `ProviderMode.REAL`.
   - **Boot validation**: a service fails at startup only when a capability it is configured to *require* has no available implementation for its configured mode. Optional integrations that are absent only degrade with a logged capability warning.
   - Wire the guard into P7 `engine.py` (upload, thumbnail, playlist) and P8 engine (submit, takedown). `DRY_RUN` records intent only. The default is `MOCK` + `DRY_RUN` everywhere.
   - Tests: a `REAL` provider with `DRY_RUN` makes no write; `LIVE` without an authorization record is refused; a missing optional integration doesn't block boot; a missing required one does.
5. **Engineering hygiene**:
   - Move `httpx` to runtime deps.
   - Change CI and `make.ps1` to `mypy packages services`, and fix the resulting errors. If the error volume is large, land a baseline file and burn it down, recorded in `STATUS.md`.
   - Bind compose ports to `127.0.0.1:`.
   - Restrict dashboard CORS to localhost origins without credentials.
   - Lift `analytics/api/service_token.py` into `plm_platform` as fail-closed service-token auth on all mutating dashboard routes.
   - Remove the `tests.*` import from `market_intelligence/cli.py:362` by moving the mock transport into `plm_platform.testing` or a mock connector.
6. **PLM `AGENTS.md`** at the repo root covering: the mission (P0 §1 sentence); the no-band-aid principle; "a missing required fact can never become a passing decision"; "a mock pass is not evidence of production behavior"; and keeping `STATUS.md` updated whenever capability changes.

**Gate R0:**
- CI green, with mypy over `packages` and `services`, or with a recorded baseline.
- The test baseline is recorded and the full non-integration suite completes under a time limit.
- `STATUS.md` and the corrected P0 status have been reviewed.
- The dashboard is visibly demo-labeled.
- The mode/policy tests pass.
- The external-write guard is the only path to a P7/P8 external call, checked by a test that patches the mock clients to assert they're unreachable without authorization.

### R1 — Contracts and fail-closed seams (requires separate approval)
- **Canonical seam models** live in `packages/plm_contracts/seams/cN.py`. Each has an explicit `schema_version` and a documented acceptance policy (consumer accepts N and N−1; unknown future major versions are rejected with a reason code). Use `extra="forbid"` where appropriate, required facts have no defaults, and unknown is a legal `Tri` state where the registry allows it.
- **Shared vocabularies**: `Disposition` (P5's four values + `CLEARED_BY_OVERRIDE`), `CommercialUseStatus`, `PlanTier` and `HoldClass`. This fixes the P4↔P5 mismatch at its root.
- **Three defenses**, with behavioral tests as the primary acceptance:
  1. Contract enforcement: the models above.
  2. Behavioral tests per seam: the real producer is called and the real consumer ingests, plus missing / malformed / unknown / out-of-range variants. Every prohibited substitution must provably block the handoff. Add C7-leg-2.
  3. A narrow static guard targeting known-dangerous patterns only: a missing gate decision defaulting to `PASSED`, invented artifact or release identities (ISRC/UPC/artifact ids), and placeholder ids (`arm_placeholder`, `variant_`). This is a supplement, not the gate.
- **Symptom fixes**:
  - P6 `assembler.py:87` defaults and duration/location fallbacks.
  - P4 rights snapshot is built from the adapter's declared terms, `UNKNOWN` when unknown.
  - P4 `arm_placeholder` and `attempts[0]`.
  - P8 uses `preserve_or_allocate_isrc` with a persisted sequence, and missing source data blocks.
  - P9 missing ISRC goes to an `unattributed` bucket.
  - P3 adapter requires P2 versions/EV.
  - Identity propagation (`experiment_id`, `content_arm_id`, `content_genome_id`, `production_directive_id`, origin observation ref) is asserted on every seam.

### R2 — Correctness, determinism, measurement semantics (separate approval)
- **Determinism**: P4 seeds from `hashlib.sha256`, reusing the existing deterministic-id helpers. P3 honours `seed` and gets an injected clock. Add a two-subprocess identical-output test.
- **Observation model in `plm_contracts`**. Every metric record declares one of these kinds:
  - `INCREMENTAL` (additive over a period)
  - `CUMULATIVE_SNAPSHOT` / `ROLLING_SNAPSHOT` (never summed across records)
  - `EXPERIMENT_OUTCOME` (predeclared horizon + maturity rule)
  - `REVISION` (supersedes a prior financial fact, linked by id)

  Aggregation functions accept only kinds valid for them. The P9 economics endpoint and P2 reward intake both go through this model.
- **Experiment reward**: P3 predeclares the observation horizon and maturity rule per experiment. P2 accepts exactly one effective outcome per (experiment, content) at that horizon; earlier immature outcomes are recorded but don't update the posterior. Fractions must sum to 1, and decay is computed at draw time from `as_of`.
- **Money**: `Decimal` with a declared precision policy for every amount, an explicit currency, and an FX table (rate, source, as_of) applied reproducibly at report time. Reporting currency defaults to KRW per P9 §23.
- **Reward ≠ EEV**: rename and version the current P9 bounded reward as `engagement_contribution_proxy_vN`. Specify the treatment of immature / estimated / missing / low-confidence outcomes, and keep observed revenue/cost provenance separate from any estimated subscriber or catalog value, which is never booked as realized revenue. Add sensitivity tests for reward vs net contribution, engagement, attribution confidence and maturity.
- **Other engine fixes**:
  - P3: treatment generation driven by the ontology via contracts, one-factor-at-a-time by default, distinct treatments asserted, `VALIDATED` only after the validator runs, data-driven genome templates, `budget_limit` honoured.
  - P1: window fixes, 24h/72h velocity, channel-keyed concentration (add `channel_id` + migration), `fetch_channels` wired in, weights moved into settings.
  - P4/P5 duration seam resolved per the C3 registry (P5 checks against the spec's duration range).

### R3 — Runtime (separate approval)
- One API container per service (or a gateway) plus workers in compose, with health checks reported by `plm doctor`.
- **Transactional outbox** in `plm_platform`:
  - The outcome record and outgoing event are committed in the same DB transaction.
  - The dispatcher uses a stable idempotency key with backoff.
  - The receiver dedupes. P2 `/ar/results` already has deterministic ids and an update-log check (`modeling/update.py:235,283`), so extend it to dedupe on the key.
  - An event is marked `DELIVERED` only on acknowledgment. A timeout goes to `UNCERTAIN` and is reconciled by querying the receiver, never assumed.
  - This replaces `feedback.py:329`.
- **Pipeline workflow** (PART XIV states) on Temporal, reusing `plm_platform/temporal.py` (:185, :195, :213). Activities call services over HTTP. Each state persists retries, errors, costs and provider ids. P3 pulls `GET /ar/directives`.
- **Cost ledger and ceiling**: persisted costs. The `authorize_external_write` budget stub from R0 becomes reservation against the ceiling, and the system fails closed when the ceiling is exhausted.
- **Exception queue with hold classes**: rows record reason code, severity, `hold_class ∈ {REVIEWABLE_QUARANTINE, HARD_STOP}`, `override_permitted`, `required_evidence`, an authorized reviewer role, status, actor and timestamps.
  - Hard stops cannot be overridden: copyright disputes, platform-policy disputes, KYC, account security, payment changes, and the P29 list as classified in policy data. They resolve only when the underlying external process records resolution evidence.
  - The resume handler checks policy and reviewer authorization before signaling Temporal.
  - Unknown rights stay quarantined.
- **Decision log** (PART XXI): the P2 allocation and overrides write the decision; P9 back-fills the realized outcome.
- **Gate (runtime only)**: uses controlled mock activities, not R4 media. It covers transport, persistence, worker-kill recovery, duplicate-delivery dedupe, the `UNCERTAIN` reconciliation path, budget stop, a reviewable quarantine resuming only for an authorized reviewer, and a hard stop refusing a generic approve.

### R4 — Media and quality (separate approval; may share tasks with R3)
- **Dependencies**: `numpy`, `soundfile`, `pyloudnorm`, `Pillow`, plus chromaprint/`fpcalc` in the worker image. FFmpeg is already there.
- **P5 technical checks**: BS.1770 integrated LUFS, true peak (4× oversampling), blocking clipping threshold, leading/trailing silence, stereo correlation, and FFmpeg decode for non-WAV inputs.
- **P5 rights and policy**: fingerprint match against the internal catalog plus a reference set, and shingled lyric similarity against a reference corpus. The protected-artist policy file is shared by the P4 prompt builder (blocks before generation) and P5. All 12 P28 fields are required, and every P29 category maps to a reason code and hold class.
  - **Limitations documented in `STATUS.md`**: these are risk signals, not proof of originality. They have reference-corpus blind spots and don't reliably detect derivative works or voice imitation.
  - A detector that is unavailable or inconclusive returns `UNKNOWN`, which routes to quarantine. It is never treated as clearance.
- **P6**: real FFmpeg render (audio concat + loudness normalization, procedural visualizer) and thumbnails A/B/C via Pillow. Checksums are taken from the produced bytes, and post-render QA runs through ffprobe. Sequencing is a beam search over Camelot key, BPM, energy curve and vocal density, with a deterministic tie-break and enforcement of the required duration. Chapters are validated against YouTube rules.
- **P4**: uses the MinIO artifact store, and P5 verifies checksums on read.
- **Gate tests**:
  - known-matching audio fixture
  - known lyric overlap
  - missing or incomplete provenance
  - detector fails to run
  - technical pass but rights/policy fail
  - quarantined item attempted via an alternate publish path (P6 direct, P7 direct, P8 direct): every one is blocked.

### R5 — Full chain (separate approval)
- **Compose-based e2e** marked `integration` and running in the CI compose job, with no hand-built hand-offs.
- **Happy path**: one arm goes from a P1 observation to LEARNING. A **second allocation cycle runs and consumes the updated posterior**, asserting that the allocation shifts toward the rewarded arm.
- **Negative paths**:
  - A REJECTED candidate never reaches P6.
  - A preflight failure never uploads.
  - A duplicate webhook or reward is not double-counted.
  - A missing seam field blocks.
  - Budget exhaustion stops the run.
  - A hard stop is not overridable.
  - A worker restart mid-run recovers.
- **Traceability**: every downstream artifact (candidate, manifest, media package, video record, release, metric, reward, posterior update) must trace to the originating market observation and arm via **stored identifiers**, not names or fixtures, checked by a lineage query test.
- **Dashboard**: reads the DB and APIs and implements the six PART XVII views. The Exceptions view drives the R3 queue with hold-class rules. Demo mode is available only behind `--demo`.
- **Scope of the claim**: this gate proves the infrastructure and posterior-update path work. It does **not** prove the policy is economically optimal; that needs the separate economics gate.

### R6 — Live adapters (separate approval)
Covers P1 Spotify/Trends, the P4 provider, P7 YouTube upload/OAuth, the P8 distributor (plus UPC and the P20 catalog) and P9 YouTube Analytics/Reporting and statement parsing.

1. Build the interfaces and conformance tests against recorded responses and controlled mocks. No credentials are needed in CI.
2. Activate with real accounts (P0 §10 human tasks) under `READ_ONLY`/`SHADOW` first.
3. Enable `LIVE` writes only per capability, through the R0 external-write guard, with explicit human authorization.

### Later — Economics gate
Versioned estimation of subscriber and catalog value with visible uncertainty, EEV composition, validation of the proxy reward against realized economics, and then P10 EEV-weighted capital allocation. The deferred PARTS X, XII, XIII and XXIII are tracked in `STATUS.md`.

---

## Critical files (R0)
- `docs/STATUS.md` (new), `docs/reviews/…` (new), `docs/P0_master_integration_implementation_plan.md` §13/§6, `README.md`, `docs/superpowers/specs/2026-10-13-plm-visual-dashboard-design.md`, `AGENTS.md` (new)
- `packages/plm_contracts/` (new `ProviderMode`, `ExecutionPolicy`), `packages/plm_platform/` (new external-write guard, `testing/` mock transport, lifted service-token auth), `packages/plm_platform/dashboard/{app.py,service.py,orchestrator.py,static/}`
- `services/youtube_publishing/engine.py` and `api/routes.py`, `services/distribution/` engine, `services/market_intelligence/cli.py`
- `pyproject.toml`, `.github/workflows/ci.yml`, `make.ps1`, `Makefile`, `docker-compose.yml`

## Reuse
- `analytics/api/service_token.py` (fail-closed auth), `plm_platform/secrets.py` `Secret`
- The existing enum and envelope style in `plm_contracts`, and `plm_platform/config.py` layered settings for the mode/policy config
- P7 idempotency key handling (`youtube_publishing/engine.py:90,123`) as the idempotency input to the guard

## Verification (R0)
- `ruff check .`, `mypy packages services` (or the recorded baseline), and `pytest -m "not integration" --timeout=120`, which must complete with results recorded in `STATUS.md`.
- New tests:
  - `tests/unit/platform/test_execution_policy.py`: DRY_RUN makes no write; LIVE without authorization is refused; required vs optional boot behaviour.
  - P7/P8 guard tests: the mock client asserts it is unreachable unless authorized.
  - A dashboard test that every payload carries `data_source: "demo"`.
  - A mutating dashboard route without a token returns 401/403.
- Manual: `./run_dashboard.ps1` shows the demo banner; `docker compose config` shows `127.0.0.1` bindings.
