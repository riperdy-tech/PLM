# PLM capability status

**This file is the source of truth for what the system actually does.** Plan checkboxes,
commit messages and the dashboard record intent or progress; this file records verified
capability. Update it in the same change that alters a capability.

- Last verified: 2026-10-13, branch `review/r0-truth-baseline` (R0 of the remediation
  plan).
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
- The W6 end-to-end test runs the phases in process on SQLite, and builds most hand-offs
  inside the test.
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
| P1 intake over HTTP | real | `ingestion/p1_adapter.py` |
| `POST /ar/results` storage, idempotent | real | Only stores; posteriors change on `POST /ar/update` |
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
| Directive intake from P2 | absent | No HTTP client; `GET /ar/directives` is never consumed (R3) |
| `budget_limit` from P2 | stub | Ignored; defaults to 100.0 (R2) |

### P4 Music factory
| Capability | Status | Where / gap |
|---|---|---|
| Generation engine, planner, candidate bank | partial | At most 3 variants; no funnel or ranking; plan marked complete when short |
| Mock provider | mock | Sine-wave audio capped at 30 s |
| Suno / ElevenLabs adapters | stub | Always return FAILED (R6) |
| Seed determinism | stub | `abs(hash(...))` varies per process (R2) |
| Artifact storage via API | stub | In-memory; stored URIs point at nothing (R4) |
| Rights snapshot | stub | Hardcoded PERMITTED / `pro_tier` (R1) |
| Cost tracking / budget enforcement | stub | `credits*0.05` is invented; `budget_limit` is not enforced |
| Protected-artist prompt blocking | absent | Exists only in P5 (R4) |

### P5 Quality gate
| Capability | Status | Where / gap |
|---|---|---|
| WAV integrity, sample rate, channels, duration, all-zero silence, clipping | partial | `evaluators/technical.py`. WAV only; clipping never blocks; no edge-silence check |
| Loudness (LUFS), true peak | stub | RMS−3 dB approximation; no true peak (R4) |
| Audio / lyric similarity, voice cloning | absent | `similarity_score` is never computed (R4) |
| Rights / provenance (P28) | partial | 4 of 12 fields checked; missing fields don't block; P4 vocabulary mismatch quarantines everything (R1, R4) |
| Hard-stop vs reviewable hold | absent | Overrides have no authorization check (R3) |

### P6 Media factory
| Capability | Status | Where / gap |
|---|---|---|
| Playlist manifest assembly with clearance check | partial | `playlist/assembler.py`. `rendering/assembler.py` defaults a missing disposition to `PASSED`, which bypasses the gate (R1) |
| Sequencing (BPM/key/energy) | stub | Input order, labelled "optimal_energy_flow" (R4) |
| Video render, thumbnails | stub | URIs and checksums are hashes of seed strings; FFmpeg is never called (R4) |
| Metadata, chapters, tags | partial | Tags hardcoded; chapter rules not validated |
| Shorts | stub | Clip descriptors only |

### P7 YouTube publishing
| Capability | Status | Where / gap |
|---|---|---|
| Publish state machine, idempotency | real | `engine.py` |
| YouTube client | mock | `client/mock.py` only (R6) |
| External-write guard, dry-run enforcement | real | A real client under dry_run/shadow ends `INTENT_RECORDED`; live writes need an authorization |
| Synthetic-media disclosure field | real | Sent in `status.containsSyntheticMedia`, so far only to the mock |
| Media bytes | stub | Falls back to `MOCK_*_BINARY` bytes (R1) |
| Channel profile / OAuth | stub | The route invents a profile, so Gate A always passes (R1) |

### P8 Distribution
| Capability | Status | Where / gap |
|---|---|---|
| Release state machine, fingerprint idempotency | real | `engine.py` |
| Distributor client | mock | `client/mock.py` only (R6) |
| External-write guard | real | Submit/takedown are refused before anything persists (HTTP 409) |
| ISRC allocation | stub | Built from the track index, so a second release violates the unique constraint; `identifiers/isrc.py` is unused (R1) |
| UPC, cross-platform catalog (P20) | absent | R6 |
| Raw report references (R10) | real | `reports/ingest.py` |

### P9 Analytics / economics
| Capability | Status | Where / gap |
|---|---|---|
| Unit economics calculator | real | `economics/calculator.py` |
| Economics endpoint aggregation | stub | Sums overlapping window snapshots (R2) |
| YouTube Analytics ingestion | stub | Stores caller-supplied numbers; no API client (R6) |
| Distributor statement ingestion | partial | Invents an ISRC when it is missing (R1) |
| Currency, decimal money | absent | All `*_usd` floats (R2) |
| Reward computation | partial | A bounded engagement/contribution proxy, not economic value (R2, economics gate) |
| Reward delivery to P2 | absent | The route never sends it; a failed POST is still recorded as posted (R3) |

### Architecture parts with no code
PART X (weekly multi-agent review / LLM router), XII (Shorts engine), XIII (community),
XXIII (multi-brand beyond a `brand_id` column), and P10 recursive allocation. These are
deferred and tracked here; they are not scheduled before the economics gate.

## Test baseline (2026-10-13)

| Tier | Command | Result |
|---|---|---|
| Unit, contract, e2e, simulation | `pytest -m "not integration"` | 948 passed, 1 skipped, ~40 s |
| Integration, compose **down** | `pytest -m integration` | 3 passed, 27 skipped, ~2.5 min |
| Integration, compose **up** | `pytest -m integration` | Not run in this session (no containers running) |
| Lint | `ruff check .` | clean |
| Types | `mypy packages services` | clean (349 files) |

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

**What the green suite does not prove.** The e2e test calls phase functions in process on
SQLite and builds the P1 opportunity, P2 directive, C4 event, P5 manifest, P7 event and
P8 release by hand. It never runs a second allocation cycle. The contract tests validate
schemas copied into the test files rather than the real consumer models. R1 and R5
replace both.

## Detector limitations (for when R4 lands)

Audio fingerprints and lyric similarity are risk signals, not proof of originality. They
miss derivative works and voice imitation that fall outside the reference corpus. A
detector that is unavailable or inconclusive must return `UNKNOWN` and route the
candidate to quarantine; it must never be treated as clearance.
