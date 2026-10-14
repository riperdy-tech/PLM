# PLM capability status

**This file is the source of truth for what the system actually does.** Plan checkboxes,
commit messages and the dashboard record intent or progress; this file records verified
capability. Update it in the same change that alters a capability.

- Last verified: 2026-10-14, branch `review/r2-correctness-determinism` (R2 of the
  remediation plan, in progress; R1 on `review/r1-fail-closed-seams`, R0 on
  `review/r0-truth-baseline`).
- Evidence: the review in
  [`reviews/2026-10-13-system-review-and-remediation-plan.md`](reviews/2026-10-13-system-review-and-remediation-plan.md),
  which has file:line references for every finding.

## Program status in one paragraph

The P0 §1 success sentence is **not yet true** of the running system. Each phase has real
domain logic, persistence and unit tests, but the phases do not yet run together.

- Only the P1 worker is deployed.
- No workflow drives the pipeline.
- P3 has no transport.
- P9 computes a reward but never delivers it to P2.
- The P4, P7 and P8 providers exist only as mocks, and P6 renders nothing.
- The W6 end-to-end test runs the phases in process on SQLite. Since R1 it uses the
  real P2 → P8 producers and consumers, but the P5 manifest and the P9 revenue figures
  are still built inside the test.
- The dashboard serves demo data.

Closing these gaps is stages R1–R6 of the remediation plan. Each stage is approved
separately against its own evidence gate.

## Legend

| Mark | Meaning |
|---|---|
| **real** | Implemented and exercised against the thing it claims to handle. |
| **mock** | Works only against an in-process simulation. |
| **stub** | Code exists but returns placeholder, hardcoded or invented values, or is never called. |
| **absent** | No implementation. |
| **partial** | Real for part of the stated scope; the gap is named. |

## Platform (`packages/`)

| Capability | Status | Where / gap |
|---|---|---|
| Layered config, redacting secrets | real | `plm_platform/config.py`, `secrets.py` |
| Service-token auth (R15), fail-closed | real | One implementation in `plm_platform/auth.py`; every service and the dashboard use it |
| Postgres engine, schema-per-phase, migrations | real | `plm_platform/db.py` (5 s connect timeout), `migrations/` |
| MinIO content-addressed storage | real | `plm_platform/storage.py`; integration tests need compose |
| Temporal helpers (retry policy, ids, idempotency key) | real | `plm_platform/temporal.py` |
| Provider mode × execution policy | real | `plm_contracts/execution.py`, `plm_platform/execution.py`; defaults are mock + dry_run |
| External-write guard | real | P7 upload/thumbnail/playlist and P8 submit/takedown pass through it |
| Write authorizations | partial | Guard checks them, but they are only supplied in code. No persisted store or operator path yet (R3). |
| Budget ceiling | stub | Default `NoBudgetLedger` refuses every live write. `CostTracker` (`observability.py`) is in-memory and used only by the P1 CLI (R3). |
| Pipeline workflow (PART XIV lifecycle) | absent | R3 |
| Outbox / cross-service delivery | absent | R3 |
| Exception queue with hold classes (PART XVIII) | absent | R3 |
| Decision log (PART XXI) | absent | R3 |
| Seam payloads C1–C8 | real | One shared, versioned model per seam in `plm_contracts/seams/` (ADR-0025). Producers serialise and consumers parse with it; missing, unknown, placeholder and future-major payloads are refused with a reason code. |
| Experiment lineage | real | `ExperimentLineage` (ADR-0026) travels from P3 through C3–C7 to P9's ISRC links and P2's reward. Placeholder ids are refused. |
| Injected time and derived seeds | real | `plm_platform/clock.py`: `Clock`/`SystemClock`/`FixedClock`, `stable_seed` (sha256). P3–P9 domain and engine code take `now` or a `Clock`; only API routes, CLIs, smoke scripts and connectors read the wall clock. The external-write guard takes `now` per decision. Enforced by `tests/unit/test_no_ambient_nondeterminism.py` (static) and `tests/unit/test_cross_process_determinism.py` (two interpreters with different `PYTHONHASHSEED` produce identical P2 draws, P3 experiments/track specs, and P4 plans, seeds, attempt/candidate ids and artifact digests). |
| Fail-open regression guard | real | `tests/contract/test_no_fail_open_defaults.py`, a narrow static scan that supplements the behavioural seam tests |
| Dashboard | demo | `plm_platform/dashboard/`: literal data and a scripted playback, labelled `data_source: "demo"` with a page banner. The six PART XVII views are absent (R5). |

## Phases (`services/`)

### P1 Market intelligence
| Capability | Status | Where / gap |
|---|---|---|
| YouTube Data API search + video details | real | `connectors/youtube.py` |
| Channel statistics (subscribers) | stub | `fetch_channels` is never called, so Demand (25% weight) is always unknown (R2) |
| Spotify, Trends connectors | stub | Real clients with no callers, so cross-platform (10%) is always unknown (R6) |
| Browser connector | stub | Always raises |
| Opportunity score, 7 weights summing to 1 | real | `scoring/calibration.py`. Weights are not in settings and not learnable (R2). |
| Growth windows | partial | "prev90d" is days 90–180, there is no 180d window, and it uses lifetime views/day (R2) |
| Velocity 24h / 72h | absent | Only 7/30/90-day medians exist (R2) |
| Competition concentration | partial | HHI is keyed on video id, not channel (R2) |
| 13-dimension taxonomy | partial | Keyword classifier only; the LLM path is absent |
| Daily/weekly/backfill Temporal jobs | real | `jobs/`; the only deployed worker |
| `ENV=ci` fixture connector | mock | `cli.py`. It reads fixtures from `tests/fixtures`, so it needs a source checkout. |

### P2 Autonomous A&R
| Capability | Status | Where / gap |
|---|---|---|
| Normal–Normal posterior, Thompson sampling | real | `modeling/reward_model.py` |
| Prior from P1 score/confidence/staleness | real | `modeling/prior.py` |
| P1 intake over HTTP | real | `ingestion/p1_adapter.py`; parses the shared C1 page, and a malformed page is `MarketUnavailable` |
| `POST /ar/results` storage, idempotent | real | Parses the shared C7 leg-2 model. Only stores; posteriors change on `POST /ar/update` |
| Posterior update | partial | Every observation window of one content is a full-weight update, so it is counted several times (R2) |
| Exploration split 70/20/10 | partial | Configurable, but fractions may sum below 1 and under-fill slots (R2) |
| Budget allocation (PART XI) | stub | Equal split per slot (later stage) |
| Reward as expected economic value | absent | Uses P9's proxy as-is (economics gate) |

### P3 Experiments
| Capability | Status | Where / gap |
|---|---|---|
| Content genome (21 dimensions) | partial | Defaults fill unknowns. Only an rnb template exists; other genres reuse lofi (R2). |
| Treatment generation | stub | Variables other than bpm/visual_world get literal `variant_{idx}`; no variable isolation; seed ignored (R2) |
| Validation | partial | Status is set to `VALIDATED` before validation runs (R2) |
| Directive parsing (C2) | real | `ingestion/p2_adapter.py` parses the shared model with no defaults (R1) |
| Directive transport from P2 | absent | No HTTP client; `GET /ar/directives` is never consumed (R3) |
| `budget_limit` from P2 | stub | Parsed, but materialization still uses a request `budget_envelope` that defaults to 100.0 (R2) |
| Lineage origination | real | `experiment_family_id` (P3 §12) and `content_genome_id` are minted and carried on every track spec (ADR-0026) |
| Track spec derivation | real | BPM and duration are the genome target or range midpoint (R5 rule), not invented defaults. The provider allow-list is still a fixed list (R2). |

### P4 Music factory
| Capability | Status | Where / gap |
|---|---|---|
| Generation engine, planner, candidate bank | partial | At most 3 variants; no funnel or ranking; plan marked complete when short |
| Mock provider | mock | Sine-wave audio capped at 30 s |
| Suno / ElevenLabs adapters | stub | Always return FAILED (R6) |
| Seed determinism | real | `stable_seed("p4.generation", track_spec_id, variant_code)`; identical across processes (R2) |
| Artifact storage via API | stub | In-memory; stored URIs point at nothing (R4) |
| Rights snapshot | real | Records the terms the provider adapter declares (`license_terms()`, ADR-0027). The mock declares PLM-owned synthesis; Suno and ElevenLabs declare nothing, which means unknown. |
| C4 event | real | Built only from stored facts (plan spec, the candidate's own attempt, its single full-mix artifact); refused with 409 when a fact is missing |
| Provider selection | partial | The engine ignores the spec's `allowed_providers` and uses its default (mock). That is the intended mock-mode behaviour, but it is implicit; real provider selection is R6. |
| Cost tracking / budget enforcement | stub | `credits*0.05` is invented; `budget_limit` is not enforced |
| Protected-artist prompt blocking | absent | Exists only in P5 (R4) |

### P5 Quality gate
| Capability | Status | Where / gap |
|---|---|---|
| WAV integrity, sample rate, channels, duration, all-zero silence, clipping | partial | `evaluators/technical.py`. WAV only; clipping never blocks; no edge-silence check |
| Loudness (LUFS), true peak | stub | RMS−3 dB approximation; no true peak (R4) |
| Audio / lyric similarity, voice cloning | absent | `similarity_score` is never computed (R4) |
| Rights / provenance (P28) | partial | Judges the declared C4 snapshot (R6 fields) in the shared vocabulary. No snapshot means quarantine; a tier label can no longer upgrade unknown to eligible (R1). The other P28 fields (generator, model version, human contribution, …) are R4. |
| Intake | real | `POST /qa/evaluations` takes the C4 event; no defaulted vocal mode or rights (R1). The audio is read from a caller-given path; fetching it by URI and checksum is R4. |
| Override | partial | Recorded separately from the automated disposition (ADR-0028). Who may override, and hard-stop classes, are R3. |

### P6 Media factory
| Capability | Status | Where / gap |
|---|---|---|
| Playlist manifest assembly with clearance check | real | Assembles only from C5 manifests. It refuses an uncleared track, mixed lineage, an empty playlist or a missing fact (ADR-0028). Manifests are passed by the caller, not fetched from P5 (R3). |
| Sequencing (BPM/key/energy) | stub | Input order, labelled "optimal_energy_flow" (R4) |
| Video render, thumbnails | stub | URIs and checksums are hashes of seed strings; FFmpeg is never called (R4) |
| Metadata, chapters, tags | partial | Tags hardcoded; chapter rules not validated |
| Shorts | stub | Clip descriptors only |

### P7 YouTube publishing
| Capability | Status | Where / gap |
|---|---|---|
| Publish state machine, idempotency | real | `engine.py`; timestamps come from the injected clock and are read back as UTC (stored timestamps used to come back naive) |
| YouTube client | mock | `client/mock.py` only (R6) |
| External-write guard, dry-run enforcement | real | A real client under dry_run/shadow ends `INTENT_RECORDED`; live writes need an authorization |
| Synthetic-media disclosure field | real | Comes from P5's finding carried on C6 (no hardcoded `True`); so far sent only to the mock |
| Publish manifest | real | Derived only from the C6 package and the configured channel (ADR-0029); the hop-2 event carries the lineage and is returned |
| Media bytes | partial | Placeholder bytes exist only on the mock client; a real client with no bytes fails before sending. Fetching real media is R4. |
| Channel profile / OAuth | partial | Must be configured by an operator (409 otherwise). Real OAuth is R6. |

### P8 Distribution
| Capability | Status | Where / gap |
|---|---|---|
| Release state machine, fingerprint idempotency | real | `engine.py` |
| Distributor client | mock | `client/mock.py` only (R6) |
| External-write guard | real | Submit/takedown are refused before anything persists (HTTP 409) |
| ISRC allocation | real | Once per recording from a persisted sequence (`identifiers/allocator.py`, ADR-0029); the registrant code is development configuration (P0 §10) |
| Release compilation | real | Only from C5 manifests + the C6 package (+ a matching C6 event); release metadata comes from the caller; lineage is stored and read back |
| One recording on several releases | absent | `tracks.isrc` is unique, so a recording cannot yet appear on two releases (R6) |
| UPC, cross-platform catalog (P20) | absent | R6 |
| Raw report references (R10) | real | `reports/ingest.py` |

### P9 Analytics / economics
| Capability | Status | Where / gap |
|---|---|---|
| Unit economics calculator | real | `economics/calculator.py` |
| Economics endpoint aggregation | stub | Sums overlapping window snapshots (R2) |
| YouTube Analytics ingestion | stub | Stores caller-supplied numbers; no API client (R6). `observed_at` and cost `incurred_at` are required facts of the measurement (HTTP 422 when missing), never stamped at receipt. |
| Analytics CLI ingest | stub | `cli_ingest` still fills missing facts (`period`, `provider`, `views` = 0), which breaks the missing-fact rule (R2 measurement item) |
| Distributor statement ingestion | real | No invented ids. Rows without an ISRC are counted as unattributed revenue; an ISRC's release comes from its C7 link (ADR-0030). |
| C7 leg-1 release links | real | `ingestion/releases.py` links each (release, ISRC) to its lineage, idempotently |
| Outcome measurement inputs | partial | Window, maturity, attribution and quality are explicit inputs with the shared vocabulary; deriving maturity from publish time is R2 |
| C8 feature view | real | Unmeasured values served as null (no zeros, `1.0`, `false` or hardcoded `REPORTED`) |
| Currency, decimal money | absent | All `*_usd` floats (R2) |
| Reward computation | partial | A bounded engagement/contribution proxy, not economic value (R2, economics gate) |
| Reward delivery to P2 | absent | The route never sends it; a failed POST is still recorded as posted (R3) |

### Architecture parts with no code
PART X (weekly multi-agent review / LLM router), XII (Shorts engine), XIII (community),
XXIII (multi-brand beyond a `brand_id` column), and P10 recursive allocation. These are
deferred and tracked here; they are not scheduled before the economics gate.

## Test baseline (2026-10-14, after R1)

| Tier | Command | Result |
|---|---|---|
| Unit, contract, e2e, simulation | `pytest -m "not integration"` | 1216 passed, 1 skipped, ~35 s |
| Integration, compose **down** | `pytest -m integration` | 3 passed, 27 skipped, ~2.5 min (collection and imports verified) |
| Integration, compose **up** | `pytest -m integration` | Not run (no containers). The migrations `0016`–`0020` and the schema-matches-models tests have therefore **not** been exercised against Postgres. |
| Lint | `ruff check .` | clean |
| Types | `mypy packages services` | clean (365 files) |

Before R0:

- **The W6.1 loop-closure test failed.** It passed only before 12:00 UTC on the day it
  was written: P9 stamped `observed_at` with the wall clock, and P2 ignores observations
  later than its pinned cycle clock. R0 fixed it by injecting the clock. Removing
  wall-clock reads across services is R2.
- **The integration tier hung indefinitely when compose was down**, because postgres
  connects had no timeout. R0 added a 5 s connect timeout and a 120 s per-test timeout
  (`pytest-timeout`).
- **The known skip** is `tests/unit/test_cli.py:57`. Its parameter list `STUB_CASES` is
  empty because no stub CLI commands remain, so the test has nothing to check.

**What the green suite does and does not prove (after R1).** Every seam contract test
now runs the real producer into the real consumer, and refuses every missing,
placeholder, unknown-field and future-major payload. That proves the hand-offs, not
the pipeline.

The e2e test still has these gaps:
- It runs in process on SQLite, with no HTTP, Temporal or Postgres.
- Its P1 opportunity and P2 directive are hand-built.
- Its P5 manifest is hand-built from the real C4 event's facts. A real P5 run rejects
  the mock's 30 s audio against the 150 s spec until R2 resolves the duration seam.
- Its P9 revenue figures are literals.
- It never runs a second allocation cycle.

R5 replaces it.

## Detector limitations (for when R4 lands)

Audio fingerprints and lyric similarity are risk signals, not proof of originality. They
miss derivative works and voice imitation that fall outside the reference corpus. A
detector that is unavailable or inconclusive must return `UNKNOWN` and route the
candidate to quarantine; it must never be treated as clearance.
