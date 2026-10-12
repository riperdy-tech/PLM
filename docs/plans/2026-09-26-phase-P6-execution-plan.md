# Phase P6 Execution Plan: Playlist, Visual & Video Factory (Media Factory)

**Objective:** Implement Phase P6 (Media Factory) end-to-end according to `docs/P6_playlist_visual_video_factory_implementation_plan.md` and Rulings R7, R8, R9 in `docs/contracts/CONTRACT_REGISTRY.md`.

---

## Tasks Overview

- [x] **P6.1 Domain Layer**: Enums, domain models, value objects, playlist track structures, visual session plans, render jobs, media packages.
- [x] **P6.2 Persistence & Migrations**: PostgreSQL `media` schema migration `0012_media_initial`, ORM models, and repositories.
- [x] **P6.3 Seam C5 Ingestion & Playlist Assembler**: Consume per-track P5 release manifests, load P3 brand/playlist spec, sequence tracks, validate clearance.
- [x] **P6.4 Visual & Concept Planner**: Scene planning, theme selection, camera/motion rules, safe-area layout.
- [x] **P6.5 Artwork & Thumbnail Engine**: Thumbnail variant generation (A/B testing candidates), cover art compilation.
- [x] **P6.6 Metadata, Chapter & Tracklist Compiler**: Timestamped chapter generation, YouTube metadata description, tags, synthetic media disclosure.
- [x] **P6.7 Deterministic Media Renderer**: Audio muxing, visual timeline composition, container validation, preflight & post-render QA.
- [x] **P6.8 Media Package Assembler**: Assemble Seam C6 publishable package with video/audio master URIs and checksums.
- [x] **P6.9 Seam C6 Contract & Registry Gate**: Contract test `tests/contract/test_c6_media_to_publishing.py`, update `CONTRACT_REGISTRY.md` to `**exists**`.
- [x] **P6.10 API Surface & Service Token Auth**: FastAPI endpoints (`POST /media/packages`, `GET /media/packages/{id}`, `GET /media/packages/{id}/manifest`, `GET /media/health`).
- [x] **P6.11 Observability & CLI Integration**: Metrics, structured logging, `plm media` subcommands in `packages/plm_platform/cli.py`, update `test_cli.py`.
- [x] **P6.12 Acceptance Scenarios & Invariant Tests**: Comprehensive unit tests covering playlist composition, rendering, packaging, and invariant checks.
- [x] **P6.13 Controlled Smoke Test & Phase Verification**: End-to-end dry-run smoke test, `ruff` & `mypy` strict clean across codebase.
