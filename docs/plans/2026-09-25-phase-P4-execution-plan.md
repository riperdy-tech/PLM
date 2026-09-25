# PHASE-P4 Music Generation Factory — Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the P4 Music Generation Factory (`services/music_factory/`) — the autonomous audio production engine that converts P3 `TrackSpec` specifications into generated music candidates, versioned audio artifacts in object storage, complete provenance records, and rights snapshots, exposing the C4 producer seam for P5.

**Architecture:** Service package `services/music_factory/` writing to the `music` PostgreSQL schema and storing audio artifacts under prefix `music/` in MinIO. P4 consumes P3 track specifications via frozen seam C3 (`tests/contract/test_c3_experiments_to_music.py`) and produces `music_candidate_ready_for_p5` events and read endpoints via frozen seam C4 (`tests/contract/test_c4_music_to_qa.py`, Ruling R6).

**Tech Stack:** Python 3.12 · pydantic 2 · SQLAlchemy 2 · Alembic (`music_0010_initial_tables` down from `0009_experiments_initial`) · FastAPI + uvicorn · `plm_contracts` · `plm_platform` (`config`, `db`, `storage`, `observability`, `correlation`) · pytest 9 · ruff · mypy strict.

---

## Tasks & Implementation Sequence

- [x] ### Task P4.1: Domain Contracts & Enums
  - Define `services/music_factory/domain/enums.py` and `models.py`.
  - Implement `ProviderCapabilities`, `GenerationRequest`, `GenerationResponse`, `GenerationPlan`, `GenerationAttempt`, `GeneratedCandidate`, `AudioArtifact`, `RightsSnapshot`, `CostRecord`, `MeasuredAudio`.
  - Implement `storage/ids.py` for deterministic uuid5 ID generation.
  - Unit tests: `tests/unit/music/test_domain_models.py`.

- [x] ### Task P4.2: Persistence & Repositories (`music` schema)
  - Create Alembic migration `migrations/versions/music/music_0010_initial_tables.py` (`down_revision="0009_experiments_initial"`).
  - Implement `storage/models.py` across all 10 tables in schema `music` (including R6 fields on `rights_snapshots`).
  - Implement `storage/repositories.py` with scoped session support.
  - SQLite harness: `tests/support/music_db.py`.
  - Unit tests: `tests/unit/music/test_repositories.py`.

- [x] ### Task P4.3: Provider Registry & Capability Discovery
  - Implement `providers/registry.py`: provider/model catalog, capability filtering matching `TrackSpec`.
  - Unit tests: `tests/unit/music/test_provider_registry.py`.

- [x] ### Task P4.4: Mock Provider (Deterministic Audio Synthesis)
  - Implement `providers/mock_provider.py`: generates valid WAV audio headers and deterministic waveforms.
  - Unit tests: `tests/unit/music/test_mock_provider.py`.

- [x] ### Task P4.5: P3 Integration (C3 Consumer Seam)
  - Implement `ingestion/p3_adapter.py`: ingest P3 `TrackSpec`, validate constraints, derive candidate target and budget limit.
  - Extend `tests/contract/test_c3_experiments_to_music.py` with consumer assertions.

- [x] ### Task P4.6: Prompt Builder & Provenance
  - Implement `generation/prompts.py`: translates `TrackSpec` into provider prompts, style tags, negative tags, and variant deltas with SHA256 prompt hashing.
  - Unit tests: `tests/unit/music/test_prompt_builder.py`.

- [x] ### Task P4.7: Generation Planner & Batch Control
  - Implement `generation/planner.py`: builds `GenerationPlan` with creative variants, retry limits, budget enforcement, and stopping rules.
  - Unit tests: `tests/unit/music/test_generation_planner.py`.

- [x] ### Task P4.8: Provider Protocol & Adapters
  - Implement `providers/protocol.py`, `providers/adapters.py` (Mock, Suno, ElevenLabs).
  - Unit tests: `tests/unit/music/test_provider_adapters.py`.

- [x] ### Task P4.9: Durable Generation Engine
  - Implement `generation/engine.py`: executes generation plan, orchestrates provider attempts, tracks cost and credits.
  - Unit tests: `tests/unit/music/test_generation_engine.py`.

- [x] ### Task P4.10: Artifact Intake & Storage
  - Implement `storage/artifacts.py`: downloads/intakes audio bytes, writes to MinIO under `music/`, calculates SHA256 checksum, extracts duration and channel count.
  - Unit tests: `tests/unit/music/test_artifact_storage.py`.

- [x] ### Task P4.11: Candidate Bank
  - Implement `candidates/bank.py`: candidate lifecycle management, state transitions, retrieval by track spec or experiment.
  - Unit tests: `tests/unit/music/test_candidate_bank.py`.

- [x] ### Task P4.12: P5 Event Contract & Seam C4 Producer Gate
  - Implement `api/events.py` for `music_candidate_ready_for_p5` event.
  - Implement C4 read endpoints: attempts, license, artifacts, provenance (Ruling R6).
  - Create `tests/contract/test_c4_music_to_qa.py` and register seam as `**exists**` in `docs/contracts/CONTRACT_REGISTRY.md`.

- [x] ### Task P4.13: API Surface, Observability, Metrics & CLI
  - Implement `api/app.py`, `api/routes.py`, `api/service_token.py`.
  - Implement `observability/metrics.py`, `observability/logging.py`.
  - Implement `cli.py` and register `plm music ...` in `packages/plm_platform/cli.py`.
  - Update `tests/unit/test_cli.py` removing `music` from stubs.
  - Unit tests: `tests/unit/music/test_cli.py`.

- [x] ### Task P4.14: Acceptance Scenarios & Invariant Tests
  - Implement `tests/unit/music/test_acceptance_scenarios.py` verifying P4 §111 ACs.

- [x] ### Task P4.15: Controlled Production Dry-Run Smoke Test
  - Implement `acceptance/smoke.py` and `plm music smoke`.
  - Unit tests: `tests/unit/music/test_smoke.py`.

- [x] ### Task P4.16: Autonomous-Mode Gate & Full Verification
  - Verification: `pytest`, `ruff check .`, `mypy packages services`.
