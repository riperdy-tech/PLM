# PLM Visual Control Deck & Monitoring Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a zero-dependency, rich visual web dashboard and monitoring control deck served directly via `python -m plm_platform.cli dashboard`, enabling one-click autonomous pipeline execution, real-time stage progression streaming, in-browser audio candidate playback, and bandit intelligence visualization.

**Architecture:** A FastAPI dashboard backend (`packages/plm_platform/dashboard/`) embedded into the platform CLI. It exposes REST + Server-Sent Events (SSE) for pipeline orchestration and telemetry, and serves a modern, zero-dependency HTML5/Vanilla CSS/ES6 single-page web app with an obsidian glassmorphic theme.

**Tech Stack:** Python 3.12, FastAPI, Starlette SSE, SQLite (in-memory & local models), Vanilla HTML5/CSS3/JavaScript (ES Modules, Web Audio API), Typer, pytest.

**Spec:** `docs/superpowers/specs/2026-10-13-plm-visual-dashboard-design.md`

## Global Constraints

- Zero external node/npm build dependencies (must run out of the box in pure Python).
- Hermetic & offline-safe: operates cleanly with SQLite and in-memory mock synthesizers.
- Consistent with R1–R18 rulings: respects Seam contracts C1–C10, preserving R9 lineage fields (`experiment_id`, `experiment_family_id`, `content_arm_id`, `content_genome_id`, `production_directive_id`, `media_package_id`) and R11 reward loop.
- Quality gates: `pytest -m "not integration"` 100% green, `ruff check .` clean, `mypy packages services` strict clean.

---

### Task 1: Dashboard Data Service & Audio Synthesizer

**Files:**
- Create: `packages/plm_platform/dashboard/__init__.py`
- Create: `packages/plm_platform/dashboard/service.py`
- Create: `tests/unit/test_dashboard_service.py`

**Interfaces:**
- Consumes: `packages/plm_platform/config.py`, domain models from `services/autonomous_ar`, `services/music_factory`, `services/quality_gate`, `services/distribution`, `services/analytics`.
- Produces: `DashboardService.get_stats() -> dict`, `DashboardService.get_arms() -> list[dict]`, `DashboardService.get_candidates() -> list[dict]`, `DashboardService.get_releases() -> list[dict]`, `DashboardService.generate_audio_wav(candidate_id: str) -> bytes`, `DashboardService.resolve_qa_case(...) -> dict`.

- [ ] **Step 1: Write the failing test for DashboardService**

```python
# tests/unit/test_dashboard_service.py
from packages.plm_platform.dashboard.service import DashboardService

def test_dashboard_service_stats_and_arms():
    service = DashboardService()
    stats = service.get_stats()
    assert "total_cycles" in stats
    assert "active_arms" in stats
    assert stats["total_cycles"] >= 0

    arms = service.get_arms()
    assert isinstance(arms, list)
    assert len(arms) > 0
    assert "arm_id" in arms[0]
    assert "posterior_mean" in arms[0]

def test_dashboard_service_synthesizes_wav():
    service = DashboardService()
    wav_bytes = service.generate_audio_wav("cand_test_01")
    assert wav_bytes.startswith(b"RIFF")
    assert b"WAVE" in wav_bytes[:16]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\pytest.exe tests/unit/test_dashboard_service.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Implement DashboardService**

Create `packages/plm_platform/dashboard/__init__.py` and `packages/plm_platform/dashboard/service.py` with:
- `get_stats()` returning platform metrics.
- `get_arms()` returning bandit arm identities, posteriors, and pull counts.
- `get_candidates()` returning candidate metadata and QA status.
- `generate_audio_wav()` synthesizing an harmonic audio buffer using standard Python `wave` + `math` modules (16-bit PCM WAV at 44.1kHz).
- `get_releases()` returning published YouTube and DSP releases.
- `resolve_qa_case()` updating quarantine cases.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\pytest.exe tests/unit/test_dashboard_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/plm_platform/dashboard/__init__.py packages/plm_platform/dashboard/service.py tests/unit/test_dashboard_service.py
git commit -m "feat(dashboard): implement dashboard data service and audio synthesizer"
```

---

### Task 2: In-Process Pipeline Orchestrator & Event Broadcaster

**Files:**
- Create: `packages/plm_platform/dashboard/orchestrator.py`
- Create: `tests/unit/test_dashboard_orchestrator.py`

**Interfaces:**
- Consumes: All 9 phase domain engines (`market_intelligence`, `autonomous_ar`, `experiments`, `music_factory`, `quality_gate`, `media_factory`, `youtube_publishing`, `distribution`, `analytics`).
- Produces: `PipelineOrchestrator.run_pipeline() -> str` (run_id), `PipelineOrchestrator.subscribe_events() -> AsyncGenerator[str, None]`.

- [ ] **Step 1: Write the failing test for PipelineOrchestrator**

```python
# tests/unit/test_dashboard_orchestrator.py
import pytest
import asyncio
from packages.plm_platform.dashboard.orchestrator import PipelineOrchestrator

@pytest.mark.asyncio
async def test_pipeline_orchestrator_execution():
    orchestrator = PipelineOrchestrator()
    events = []

    async def listener():
        async for event in orchestrator.subscribe():
            events.append(event)
            if event.get("type") in ("pipeline_complete", "pipeline_error"):
                break

    task = asyncio.create_task(listener())
    run_id = await orchestrator.start_run()
    assert run_id.startswith("run_")
    await asyncio.wait_for(task, timeout=15.0)

    event_types = [e.get("type") for e in events]
    assert "stage_start" in event_types
    assert "stage_complete" in event_types
    assert "pipeline_complete" in event_types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\pytest.exe tests/unit/test_dashboard_orchestrator.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Implement PipelineOrchestrator**

Create `packages/plm_platform/dashboard/orchestrator.py` with:
- Async event broadcasting queue (`asyncio.Queue`).
- 10-step execution runner:
  1. `p1_market`: Market observation & opportunity scoring.
  2. `p2_ar`: Bandit directive allocation.
  3. `p3_genome`: Experiment materialization & track specs.
  4. `p4_music`: Audio candidate generation.
  5. `p5_qa`: Quality gate & release manifest clearance.
  6. `p6_media`: Media package assembly.
  7. `p7_youtube`: YouTube publishing intent.
  8. `p8_distribution`: DSP submission & ISRC assignment.
  9. `p9_analytics`: Unit economics & normalized reward calculation.
  10. `p2_loop`: Bayesian posterior update & allocation shift.
- Emits detailed SSE formatted payloads for each step.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\pytest.exe tests/unit/test_dashboard_orchestrator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/plm_platform/dashboard/orchestrator.py tests/unit/test_dashboard_orchestrator.py
git commit -m "feat(dashboard): implement in-process pipeline orchestrator and SSE broadcaster"
```

---

### Task 3: FastAPI Dashboard API & Endpoints

**Files:**
- Create: `packages/plm_platform/dashboard/app.py`
- Create: `tests/unit/test_dashboard_api.py`

**Interfaces:**
- Consumes: `DashboardService`, `PipelineOrchestrator`, FastAPI, Starlette `EventSourceResponse` / SSE.
- Produces: `create_app() -> FastAPI` mounting all REST endpoints and static files.

- [ ] **Step 1: Write the failing test for Dashboard API**

```python
# tests/unit/test_dashboard_api.py
from fastapi.testclient import TestClient
from packages.plm_platform.dashboard.app import create_app

def test_dashboard_api_endpoints():
    app = create_app()
    client = TestClient(app)

    # Health & stats
    r_stats = client.get("/api/stats")
    assert r_stats.status_code == 200
    assert "active_arms" in r_stats.json()

    # Arms
    r_arms = client.get("/api/arms")
    assert r_arms.status_code == 200
    assert len(r_arms.json()) > 0

    # Candidates
    r_cand = client.get("/api/candidates")
    assert r_cand.status_code == 200

    # Audio stream
    r_audio = client.get("/api/audio/cand_mock_01")
    assert r_audio.status_code == 200
    assert r_audio.headers["content-type"] == "audio/wav"

    # Pipeline run trigger
    r_run = client.post("/api/pipeline/run")
    assert r_run.status_code == 200
    assert "run_id" in r_run.json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\pytest.exe tests/unit/test_dashboard_api.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Implement Dashboard FastAPI App**

Create `packages/plm_platform/dashboard/app.py`:
- Initialize FastAPI app with title `"PLM Autonomous Music Engine"`.
- Implement endpoints:
  - `GET /api/stats`
  - `GET /api/arms`
  - `GET /api/candidates`
  - `GET /api/audio/{candidate_id}`
  - `GET /api/releases`
  - `POST /api/pipeline/run`
  - `GET /api/pipeline/events` (SSE streaming)
  - `POST /api/qa/resolve`
- Mount static files directory `packages/plm_platform/dashboard/static` at `/static` and root route `/` returning `index.html`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\pytest.exe tests/unit/test_dashboard_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/plm_platform/dashboard/app.py tests/unit/test_dashboard_api.py
git commit -m "feat(dashboard): implement FastAPI dashboard REST and SSE endpoints"
```

---

### Task 4: Interactive Frontend Single-Page App (HTML, CSS, JS)

**Files:**
- Create: `packages/plm_platform/dashboard/static/index.html`
- Create: `packages/plm_platform/dashboard/static/app.css`
- Create: `packages/plm_platform/dashboard/static/app.js`

**Interfaces:**
- Consumes: REST endpoints and SSE stream from `/api/*`.
- Produces: Fully interactive web app with:
  1. **Control Deck Tab**: KPI metric cards, "Run Autonomous Pipeline" CTA with spinning/pulse animation, 10-node interactive pipeline stepper, real-time telemetry console.
  2. **Music Vault Tab**: Grid of generated audio candidates, in-browser HTML5 Audio player with interactive waveform scrub, rights compliance pill, manifest inspection modal.
  3. **Bandit Intelligence Tab**: Visual cards of bandit arms, Bayesian posterior win rates, exploration vs exploitation indicators.
  4. **QA & Clearance Tab**: Review queue of cleared vs quarantined cases, one-click override action.

- [ ] **Step 1: Create `static/index.html`**
HTML5 structure with header branding, KPI overview banner, navigation tabs, view sections, modal inspector, and bottom fixed audio player.

- [ ] **Step 2: Create `static/app.css`**
Obsidian cyber-dark palette (`#070a11`), glassmorphism (`backdrop-filter: blur(16px)`), CSS grid layouts, glowing pulse animations for the pipeline stepper, clean typography using Google Fonts (Outfit, Inter, JetBrains Mono).

- [ ] **Step 3: Create `static/app.js`**
ES6 script providing:
- Tab navigation.
- Data fetching for stats, arms, candidates, releases.
- Audio player controls with HTML5 audio playback of `/api/audio/{candidate_id}` and dynamic waveform canvas.
- EventSource subscription to `/api/pipeline/events` updating stage stepper nodes and printing colorized logs to the telemetry console.
- Pipeline execution trigger.

- [ ] **Step 4: Verify static assets served via TestClient**
Test GET `/` and GET `/static/app.css` return 200 OK with proper content types.

- [ ] **Step 5: Commit**

```bash
git add packages/plm_platform/dashboard/static/
git commit -m "feat(dashboard): build interactive glassmorphic web dashboard frontend"
```

---

### Task 5: Platform CLI Integration (`plm dashboard`)

**Files:**
- Modify: `packages/plm_platform/cli.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Consumes: `packages.plm_platform.dashboard.app:create_app`, `uvicorn`.
- Produces: Subcommand `python -m plm_platform.cli dashboard [--host 127.0.0.1] [--port 8000]`.

- [ ] **Step 1: Write test for dashboard CLI command**

Add `test_cli_dashboard_help()` to `tests/unit/test_cli.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\pytest.exe tests/unit/test_cli.py::test_cli_dashboard_help -v`
Expected: FAIL.

- [ ] **Step 3: Add `dashboard` command to `cli.py`**

Define `dashboard_app = typer.Typer(help="Visual control deck & monitoring dashboard.")` and add `serve` / root command running `uvicorn.run(...)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\pytest.exe tests/unit/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/plm_platform/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): add plm dashboard command group for visual web control deck"
```

---

### Task 6: Whole-Program Verification

**Files:**
- None (verification suite)

- [ ] **Step 1: Run full non-integration test suite**
Run: `pytest -m "not integration"`
Expected: 100% green.

- [ ] **Step 2: Run linter and formatting checks**
Run: `ruff check .`
Expected: All checks passed!

- [ ] **Step 3: Run strict type checking**
Run: `mypy packages services`
Expected: Success: no issues found.

- [ ] **Step 4: Run dashboard smoke check**
Run: `python -m plm_platform.cli dashboard --help`
Expected: Exits 0 and prints dashboard options.

- [ ] **Step 5: Commit**
Final chore commit if any cleanup needed.
