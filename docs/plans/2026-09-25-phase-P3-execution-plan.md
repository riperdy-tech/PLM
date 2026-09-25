# PHASE-P3 Content Genome & Experiment Framework — Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the P3 Content Genome and Experiment Framework (`services/experiments/`) — the causal bookkeeping layer that transforms P2 allocation directives into structured, reproducible content experiments, maintains machine-readable content genomes across 21 canonical dimensions with explicit hard/soft/free constraints, generates downstream content packages (track, playlist, visual, thumbnail, and title specifications for P4–P8), enforces budget ceilings and duplicate/contamination prevention, and closes the C2 consumer seam and C3 producer seam.

**Architecture:** One service package `services/experiments/` writing to the `experiments` PostgreSQL schema and reading/writing artifacts under the `experiments/` object-storage prefix (R13/C9). P3 consumes P2 directives via the frozen C2 contract (`tests/contract/test_c2_ar_to_experiments.py`) and emits the frozen C3 track specifications to P4 (`tests/contract/test_c3_experiments_to_music.py`). Every experiment, genome, and package is deterministic from stored inputs plus version strings: IDs are uuid5-derived (`storage/ids.py`), randomness is explicitly seeded, and experiments become strictly immutable once materialization or execution begins.

**Tech Stack:** Python 3.12 · pydantic 2 (wire/domain validation) · SQLAlchemy 2 Core · Alembic (single chain; this phase adds `experiments_0009_initial_tables`) · FastAPI + uvicorn · `plm_contracts` (`AllocationType`, `ChannelActivationState`, `QualityStatus`, `Tri`, `ErrorShape`, `BrandId`, `format_versioned_id`) · `plm_platform` (`config`, `db`, `storage`, `observability`, `temporal`) · `tests/support` harness (frozen clock, seed) · pytest 9 · ruff · mypy.

**Spec:** `docs/P3_content_genome_experiment_framework_implementation_plan.md` (PHASE-P3; ~2,789 lines). Frozen seam authorities: `docs/contracts/CONTRACT_REGISTRY.md` entries **C2** (FROZEN 2026-09-20, ADR-0023) and **C3** (FROZEN, Ruling R5). Base commit `75e8235` on branch `build/wave0-foundation`.

---

## Global Constraints & Seam Rulings

1. **Shared packages only for cross-cutting concerns:** `plm_contracts` for vocabulary and envelopes; `plm_platform.storage` under prefix `experiments/` (content-addressed, checksummed); `plm_platform.db` (`scoped_session("experiments")`); `plm_platform.config` for settings — never `os.environ` directly; `plm_platform.observability` for structured logs and metrics; `plm_platform.temporal` on queue `TaskQueue.EXPERIMENTS` (`ph3.experiments`).
2. **C2 Consumer Seam (ADR-0023 & R4):** P3 consumes the frozen C2 directive payload. Adapter maps:
   - `requested_experiment_count ← quantity`
   - `exploration_level ← allocation_type` (`exploit` / `adjacent_explore` / `radical_explore`)
   - `production_deadline ← expires_at`
   - `required_constraints` / `recommended_attributes ← brief` + creative search region.
   - P3 rejects directives lacking `arm_id`, `directive_id`, or `decision_id`.
   - **Budget authority (ADR-0023 #1):** Each directive carries its share in `budget_limit`. P3 rejects any package whose envelope exceeds the directive's share ceiling.
3. **C3 Producer Seam (Ruling R5):** P3 emits track specifications matching the frozen C3 contract (`tests/contract/test_c3_experiments_to_music.py`):
   - `track_spec_id`, `playlist_spec_id`, `experiment_id`, `content_arm_id`, `brand_profile_id`, `hard_constraints`, `bounded_preferences`, `creative_latitude`, `lyric_spec`, `vocal_spec`, `instrumentation_spec`, `production_style`, `track_role`, `requested_duration`, `candidate_target`, `priority`, `budget_limit`, `provider_policy`.
   - Mappings: `role` → `track_role`; `duration_range` → `requested_duration`; `vocal_profile` → `vocal_spec`; `instrument_profile` → `instrumentation_spec`.
   - Derives `candidate_target`, `priority`, `budget_limit` from budget envelope and directive priority.
4. **21 Canonical Genome Dimensions (§6, §7):**
   `genre`, `subgenre`, `mood`, `use_case`, `language`, `vocal_mode`, `vocal_gender`, `energy`, `bpm`, `key_profile`, `lyric_density`, `song_structure`, `hook_profile`, `instrumentation_profile`, `production_style`, `track_duration`, `playlist_duration`, `visual_world`, `visual_motion_profile`, `thumbnail_family`, `title_archetype`.
5. **Constraint Hierarchy (§9):**
   - **Hard constraint:** Non-negotiable boundary; violation fails validation.
   - **Soft preference:** Optimization target; deviation recorded as creative delta.
   - **Creative freedom:** Explicitly unconstrained; downstream generators may explore.
6. **Immutability & Determinism (§4.4, §4.6, §73):**
   - Experiments and genomes are immutable once execution begins.
   - All IDs are deterministic uuid5 hashes over business identity components (`storage/ids.py`).
   - Re-running materialization with identical directive and seed produces identical packages.
7. **Database Migrations:** Single Alembic chain. Revision `0009_experiments_initial` in `migrations/versions/experiments/` with `down_revision = "0008_ar_initial"`. Table names conform strictly to §50–§63 in schema `experiments`. Enum columns stored as text.
8. **Mock-First & Strict Typing:** No live external provider calls required. Tests pass offline. Ruff clean. Mypy strict clean across `packages` and `services`.

---

## File Structure — `services/experiments/`

| File | Responsibility |
|---|---|
| `__init__.py` | Package docstring & public API export. |
| `config.py` | `ExperimentSettings`, `load_experiment_settings()`. |
| `domain/enums.py` | P3 enums: `ExperimentType`, `VariableClass`, `ConstraintType`, `ExperimentStatus`, `PackageStatus`, `TrackRole`, `VocalGender`, `EnergyLevel`. |
| `domain/genome.py` | `ContentGenome`, dimension dataclasses for all 21 dimensions, `ConstraintEnvelope`. |
| `domain/experiment.py` | `Experiment`, `ExperimentVariable`, `Treatment`, `Hypothesis`. |
| `domain/package.py` | `ContentPackage`, `TrackSpec`, `PlaylistSpec`, `VisualSpec`, `ThumbnailSpec`, `TitleSpec`. |
| `domain/measurement.py` | `MeasurementPlan`, `MetricDefinition`, `AttributionWindow`. |
| `ingestion/p2_adapter.py` | P2 `ProductionDirective` ingest, validation, field mapping (C2 consumer). |
| `genome/engine.py` | Normalization, inheritance, constraint resolution, creative latitude bounds, fingerprinting. |
| `experiments/engine.py` | Experiment design, variable assignment, treatment generation, lifecycle state machine. |
| `materialization/builder.py` | Materializes `ContentPackage` and all 5 specification types from treatments & genome. |
| `validation/engine.py` | Contradiction detection, duplicate prevention, contamination check, budget ceiling check. |
| `storage/ids.py` | Deterministic uuid5 generator for P3 entity IDs. |
| `storage/models.py` | SQLAlchemy metadata for all 13 `experiments.*` tables. |
| `storage/repositories.py` | Repositories for experiments, genomes, packages, specs, and event audit log. |
| `api/schemas.py` | Pydantic request/response schemas for `/experiments/v1`. |
| `api/service_token.py` | Token authentication dependency. |
| `api/routes.py` | Endpoints: create, validate, materialize, get package/treatments, lifecycle actions. |
| `api/app.py` | FastAPI application factory. |
| `observability/metrics.py` | Prometheus metrics on platform registry. |
| `observability/events.py` | Structured audit event logger (`experiment_events`). |
| `jobs/workflows.py` | Temporal workflow definitions (`ph3.experiments`). |
| `cli.py` | Implementations for `plm experiments ...`. |
| `acceptance/smoke.py` | §83 AC-01..AC-15 validation & §85 P3.13 production dry-run smoke. |

---

## Tasks & Implementation Sequence

### Task P3.1: Domain Contracts & 21-Dimension Content Genome
- Define `domain/enums.py` and `domain/genome.py`.
- Implement canonical 21 dimensions with types, validation, and constraint types (`HARD`, `SOFT`, `FREE`).
- Unit tests: `tests/unit/experiments/test_domain_genome.py`.

### Task P3.2: Schema, Fingerprinting & Immutability Layer
- Define `domain/experiment.py`, `domain/package.py`, `domain/measurement.py`.
- Implement SHA256 genome fingerprinting and experiment variable hashing.
- Unit tests: `tests/unit/experiments/test_domain_experiment.py`.

### Task P3.3: Database Migration & Repositories (`experiments` schema)
- Create `migrations/versions/experiments/experiments_0009_initial_tables.py` (`down_revision="0008_ar_initial"`).
- Implement `storage/models.py` for all 13 tables in schema `experiments`.
- Implement `storage/repositories.py` with scoped session support.
- Unit tests: `tests/unit/experiments/test_repositories.py`.

### Task P3.4: P2 Directive Adapter & Seam C2 Consumer Gate
- Implement `ingestion/p2_adapter.py`.
- Ingest P2 `ProductionDirective` wire payload, validate C2 schema, map fields, enforce budget share ceiling.
- Extend `tests/contract/test_c2_ar_to_experiments.py` with consumer-side assertions.

### Task P3.5: Genome Engine & Creative Latitude Envelopes
- Implement `genome/engine.py`: template inheritance, constraint resolution, normalization, and creative latitude bounds.
- Unit tests: `tests/unit/experiments/test_genome_engine.py`.

### Task P3.6: Experiment Engine, Variables & Treatment Assignment
- Implement `experiments/engine.py`: experiment creation, variable classification (`fixed`, `controlled`, `experimental`, `incidental`), treatment generation, and lifecycle state machine.
- Unit tests: `tests/unit/experiments/test_experiment_engine.py`.

### Task P3.7: Materializer & Downstream Specifications (Seam C3 Producer)
- Implement `materialization/builder.py`: materialize `ContentPackage`, `TrackSpec`, `PlaylistSpec`, `VisualSpec`, `ThumbnailSpec`, `TitleSpec`.
- Create seam contract test `tests/contract/test_c3_experiments_to_music.py` pinning the frozen C3 wire format for P4.
- Unit tests: `tests/unit/experiments/test_materializer.py`.

### Task P3.8: Validation Engine (Contradiction, Duplicate & Budget Check)
- Implement `validation/engine.py`: detect conflicting constraints, prevent duplicate experiment fingerprints, flag cross-treatment contamination, enforce budget limits.
- Unit tests: `tests/unit/experiments/test_validation_engine.py`.

### Task P3.9: Measurement Contract & Metric Registry
- Implement `domain/measurement.py`: attribution windows, primary/secondary success metrics, cohort definitions.
- Unit tests: `tests/unit/experiments/test_measurement_contract.py`.

### Task P3.10: API Surface & Service Token Auth (`/experiments/v1`)
- Implement `api/routes.py`, `api/schemas.py`, `api/service_token.py`, `api/app.py`.
- Endpoints: `POST /experiments/v1/experiments`, `POST /experiments/v1/experiments/validate`, `POST /experiments/v1/experiments/{id}/materialize`, `GET /experiments/v1/packages/{id}`, `GET /experiments/v1/experiments/{id}`.
- Unit tests: `tests/unit/experiments/test_api_endpoints.py`.

### Task P3.11: Observability, Metrics & CLI Integration
- Implement `observability/metrics.py`, `observability/events.py`, and `cli.py`.
- Replace stub in `packages/plm_platform/cli.py` for `plm experiments ...`.
- Unit tests: `tests/unit/experiments/test_cli.py`.

### Task P3.12: Acceptance Scenarios AC-01–AC-15 & Invariant Tests
- Implement `tests/unit/experiments/test_acceptance_scenarios.py` verifying all 15 scenarios from §83.
- Invariants: deterministic materialization, budget ceiling violations blocked, duplicate prevention, replication lineage.

### Task P3.13: Controlled Production Dry-Run Smoke Test
- Implement `acceptance/smoke.py` and `plm experiments smoke`.
- Run synthetic end-to-end cycle: P2 directive → P3 experiment → validate → materialize → package retrieval.
- Gate verification: full test suite, ruff clean, mypy strict clean.
