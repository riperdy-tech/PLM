# Phase P5 Execution Plan: Quality, Rights, Provenance & Release-Gate Engine

**Objective:** Implement Phase P5 (Quality Gate) end-to-end according to `docs/P5_quality_rights_provenance_release_gate_implementation_plan.md` and rulings R6, R7, R8 in `docs/contracts/CONTRACT_REGISTRY.md`.

---

## Tasks Overview

- [x] **P5.1 Domain Layer**: Enums, domain models, value objects, evaluation snapshots, check structures, gate decisions.
- [x] **P5.2 Persistence & Migrations**: PostgreSQL `qa` schema migration `0011_qa_initial`, ORM models, and repositories.
- [x] **P5.3 Seam C4 Ingestion Adapter**: Consume `CandidateReadyForP5Event` and assemble immutable `EvaluationSnapshot`.
- [x] **P5.4 Technical Audio Validator**: Audio format, WAV header, duration tolerance, clipping, silence, SHA-256 integrity.
- [x] **P5.5 Musical & Structural Checker**: Dynamic range, integrated LUFS estimation, true peak, structural coherence.
- [x] **P5.6 Lyric & Vocal Checker**: Vocal presence vs track spec mode, profanity, text structure.
- [x] **P5.7 Provenance & Rights Validator**: Ruling R6 compliance (`plan_or_tier`, `account_identity_ref`, `terms_version`, `downloaded_at`), commercial-use eligibility, unknown license routing.
- [x] **P5.8 Policy & Similarity Risk Screener**: Artist name protection, forbidden keywords, YouTube synthetic disclosure requirement flag.
- [x] **P5.9 Gate Policy Engine**: Rule engine evaluating hard gates vs soft signals, rendering `GateDecision` (`PASSED`, `PASSED_WITH_WARNINGS`, `QUARANTINED`, `REJECTED`).
- [x] **P5.10 Release Manifest Generator**: Generate immutable `ReleaseManifest` with `p5_manifest_id` (R7, R8) for downstream P6 consumption.
- [x] **P5.11 Review Queue & Quarantine Resolution**: Human-in-the-loop review workflow, audit log, `CLEARED_BY_OVERRIDE`.
- [x] **P5.12 Seam C5 Contract & Registry Gate**: Contract test `tests/contract/test_c5_qa_to_media.py`, update `CONTRACT_REGISTRY.md` to `**exists**`.
- [x] **P5.13 API Surface & Service Token Auth**: FastAPI endpoints (`GET /qa/release-manifests/{candidate_id}`, `/evaluations`, `/quarantine`, `/health`).
- [x] **P5.14 Observability & CLI Integration**: Metrics, structured logging, `plm qa` subcommands in `packages/plm_platform/cli.py`, update `test_cli.py`.
- [x] **P5.15 Acceptance Scenarios & Invariant Tests**: Comprehensive unit tests covering all evaluation paths and invariants.
- [x] **P5.16 Controlled Smoke Test & Phase Verification**: End-to-end verification, `ruff` & `mypy` strict clean across codebase.
