# Phase P8 Execution Plan: DSP / Music Distribution Engine

**Objective:** Implement Phase P8 (DSP Distribution Engine) end-to-end according to `docs/P8_dsp_distribution_engine_implementation_plan.md` and Rulings R9, R10, R11 in `docs/contracts/CONTRACT_REGISTRY.md`.

---

## Tasks Overview

- [x] **P8.1 Domain Layer**: Enums (ReleaseType, ReleaseStatus, DeliveryStatus, StorePlatform, ISRCSource), domain models (`CanonicalReleasePackage`, `DistributionRelease`, `DistributionTrack`, `DeliveryTarget`, `ArtistProfile`, `DistributionReportReference`, `DistributionRecordPayload`), deterministic IDs & fingerprints.
- [x] **P8.2 Persistence & Migrations**: PostgreSQL `distribution` schema migration `0014_distribution_initial`, ORM models, and repositories.
- [x] **P8.3 Ingestion & Release Compiler**: Ingest P5 release clearance (`ReleaseManifest`) and P6 media package (`MediaPackage`), compile immutable `CanonicalReleasePackage` with source manifest references (`p5_manifest_id`, `p6_manifest_id`), deterministic `submission_fingerprint`.
- [x] **P8.4 Artist Identity & ISRC Management**: Artist validation, virtual artist profile management, store artist mapping, deterministic ISRC assignment/validation without silent re-generation on metadata corrections.
- [x] **P8.5 Distribution Package Preflight & Validation**: Metadata sanity checks (Unicode titles, explicit flags, language codes, track-number gaps), rights/audio/artwork preflight.
- [x] **P8.6 Distributor Protocol & Mock Adapter**: Provider-neutral interface (`DistributorClientProtocol`), capabilities model, deterministic mock adapter for testing without live distributor APIs.
- [x] **P8.7 Distribution Orchestrator & State Machine**: Durable state machine (`DRAFT` -> `VALIDATING` -> `SUBMITTED` -> `PROCESSING` -> `DELIVERED`/`PARTIALLY_DELIVERED`/`REJECTED`), partial store delivery tracking, retry and idempotency guard preventing duplicate submissions.
- [x] **P8.8 Raw Provider Report Ingestion & Versioning**: Ingest raw provider statements/reports (`raw_report`, `provider_period`, `received_at`, `report_checksum`), immutable report versioning; verify P8 never computes revenue (Ruling R10).
- [x] **P8.9 Seam C7 Contract Gate**: Seam C7 Leg 1 contract test `tests/contract/test_c7_distribution_to_analytics.py` verifying `DistributionRecordPayload` with all R9 identity fields (`experiment_id`, `experiment_family_id`, `content_arm_id`, `content_genome_id`, `production_directive_id`, `media_package_id`), update `CONTRACT_REGISTRY.md` to `**exists**`.
- [x] **P8.10 API Surface & Service Token Auth**: FastAPI endpoints (`POST /distribution/releases`, `GET /distribution/releases/{id}`, `GET /distribution/reports`, `GET /distribution/health`).
- [x] **P8.11 Observability & CLI Integration**: Metrics, structured logging, `plm distribution` subcommands (`submit`, `release`, `report`, `serve`, `smoke`) in `packages/plm_platform/cli.py`, update `test_cli.py`.
- [x] **P8.12 Acceptance Scenarios & Invariant Tests**: Comprehensive unit tests covering state machine transitions, ISRC stability, artist collisions, partial delivery, report versioning, and failure scenarios (F01–F12).
- [x] **P8.13 Controlled Smoke Test & Phase Verification**: End-to-end dry-run smoke test, `ruff` & `mypy` strict clean across codebase.
