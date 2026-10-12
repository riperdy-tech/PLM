# Phase P7 Execution Plan: Autonomous YouTube Publishing Engine

**Objective:** Implement Phase P7 (Autonomous YouTube Publishing Engine) end-to-end according to `docs/P7_youtube_publishing_engine_implementation_plan.md` and Rulings R9 in `docs/contracts/CONTRACT_REGISTRY.md`.

---

## Tasks Overview

- [x] **P7.1 Domain Layer**: Enums (including `PROCESSING_TIMEOUT`), domain models, channel profile, publish manifest, publish attempt, video publication records, Seam C6 event structures.
- [x] **P7.2 Persistence & Migrations**: PostgreSQL `youtube` schema migration `0013_youtube_initial`, ORM models, and repositories.
- [x] **P7.3 Seam C6 Ingestion & Package Preflight**: Ingest P6 MediaPackage / Manifest, run Gates A, B, C (channel configuration, artifact checksum/format, metadata bounds & synthetic disclosure), create immutable `PublishManifest` with canonical idempotency key.
- [x] **P7.4 YouTube API Client Protocol & Mock Connector**: Protocol for YouTube Data API v3 (`videos.insert`, `videos.list`, `thumbnails.set`, `playlists.insert`, `playlistItems.insert`), deterministic mock adapter for testing without live credentials, auth error handling.
- [x] **P7.5 Resumable Upload & Session State Machine**: Resumable upload orchestrator, session persistence, upload retry policy with exponential backoff, state transitions (`UPLOAD_SESSION_CREATING` -> `UPLOADING` -> `UPLOADED`).
- [x] **P7.6 Video Metadata, Disclosure & Thumbnail Engine**: Apply title, description, tags, category (`10`), synthetic media disclosure (`status.containsSyntheticMedia`), `made_for_kids=False`, custom thumbnail upload via `thumbnails.set`.
- [x] **P7.7 Playlist Placement & Scheduling Engine**: Playlist resolution/creation, `playlistItems.insert`, privacy management (`private` during staging/scheduled -> `publishAt` for scheduled release).
- [x] **P7.8 Reconciliation, Health Check & Recovery**: Remote video status polling (`processingDetails`, `uploadStatus`), timeout handling (`PROCESSING_TIMEOUT`), idempotency recovery (lookup existing video ID before duplicate upload), human exception queue.
- [x] **P7.9 Seam C6 Event Producer & Handoff**: Emit `youtube_video_published` event carrying all R9 identity fields (`experiment_id`, `experiment_family_id`, `content_arm_id`, `content_genome_id`, `production_directive_id`, `media_package_id`, `youtube_video_id`, `publish_attempt_id`, `published_at`, `metadata_version`). Contract test Seam C6 Hop 2.
- [x] **P7.10 API Surface & Service Token Auth**: FastAPI endpoints (`POST /youtube/publish`, `GET /youtube/attempts/{id}`, `POST /youtube/reconcile/{id}`, `GET /youtube/health`).
- [x] **P7.11 Observability & CLI Integration**: Metrics, structured logging, `plm youtube` subcommands (`publish`, `attempt`, `reconcile`, `serve`, `smoke`) in `packages/plm_platform/cli.py`, update `test_cli.py`.
- [x] **P7.12 Acceptance Scenarios & Invariant Tests**: Comprehensive unit tests covering state machine transitions, idempotency, preflight rejections, mock upload and reconciliation.
- [x] **P7.13 Controlled Smoke Test & Phase Verification**: End-to-end dry-run smoke test, `ruff` & `mypy` strict clean across codebase.
