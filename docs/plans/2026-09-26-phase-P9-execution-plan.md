# Phase P9 Execution Plan: Analytics & Economics Feedback Engine

**Objective:** Implement Phase P9 (Analytics & Economics Engine) end-to-end according to `docs/P9_analytics_economics_engine_implementation_plan.md` and Rulings R10, R11, R12 in `docs/contracts/CONTRACT_REGISTRY.md`.

---

## Tasks Overview

- [x] **P9.1 Domain Layer**: Enums (`MetricSource`, `MetricStatus`, `ObservationWindow`, `RevenueStatus`, `PaybackStatus`, `DataFreshness`), domain models (`VideoMetricObservation`, `TrackMetricObservation`, `RevenueStatement`, `CostItem`, `UnitEconomics`, `ExperimentOutcomeRecord`, `NormalizedRewardPayload`, `P10FeatureView`), deterministic IDs.
- [x] **P9.2 Persistence & Migrations**: PostgreSQL `analytics` schema migration `0015_analytics_initial` chained to `0014_distribution_initial`, ORM models, and repositories.
- [x] **P9.3 Ingestion & Reconciliation Engine**: Ingest YouTube performance metrics, ingest & parse raw distributor statements (Ruling R10), ingest cost items, deduplicate statements, and version metric observations.
- [x] **P9.4 Economics & Unit-Economics Calculations**: Canonical financial arithmetic (gross revenue, allocated cost, net contribution, RPM, revenue per stream, ROI, payback period), zero/negative handling.
- [x] **P9.5 Experiment Evaluation & Feedback Engine (Ruling R11, Seam C7 Leg 2)**: Calculate windowed performance outcomes, compute `normalized_reward` and `attribution_confidence`, and post to P2 `POST /ar/results`.
- [x] **P9.6 P10 Feature Projection (Seam C8)**: Generate P10-ready feature view conforming to Seam C8 contract.
- [x] **P9.7 API Surface & Service Token Auth**: FastAPI router and app (`POST /analytics/ingest/*`, `GET /analytics/metrics/*`, `GET /analytics/economics/*`, `GET /analytics/p10/features`, `GET /analytics/health`).
- [x] **P9.8 Observability & CLI Integration**: Metrics, structured logging, `plm analytics` subcommands (`ingest`, `report`, `rewards`, `serve`, `smoke`) in `packages/plm_platform/cli.py`, remove `analytics` from `test_cli.py` `STUB_CASES`.
- [x] **P9.9 Acceptance Scenarios & Invariant Tests**: Hermetic in-memory SQLite harness, financial arithmetic tests, reconciliation tests, R11 feedback post tests, C8 feature projection tests, API & CLI tests.
- [x] **P9.10 Controlled Smoke Test & Phase Verification**: End-to-end dry-run smoke test (exit code 3 when uncredentialed), `ruff` clean, `mypy` strict clean, `pytest -m "not integration"` 100% passing across codebase.
