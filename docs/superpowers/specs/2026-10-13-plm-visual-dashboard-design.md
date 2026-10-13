# PLM Visual Control Deck & Monitoring Dashboard Design Spec

## 1. Executive Summary

The **PLM Visual Control Deck** provides a zero-setup, zero-external-dependency web dashboard for monitoring and orchestrating the autonomous music-media platform. Operating as a native subcommand of the platform CLI (`python -m plm_platform.cli dashboard --port 8000`), it bridges the 9 backend engines with an interactive, glassmorphic dark-mode web interface. Operators can trigger autonomous cycles with one click, watch stage execution live across a 10-step visual node stepper, listen to generated candidate audio directly in the browser, inspect multi-armed bandit posteriors, and review quality gate clearances.

---

## 2. Architecture & Components

```
┌──────────────────────────────────────────────────────────────────┐
│                   Browser Client (HTML5 / Vanilla CSS / ES6)     │
│  - Control Deck & Live Stepper   - Music & Audio Player          │
│  - Bandit Intelligence           - QA Clearance Center           │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ HTTP REST & Server-Sent Events (SSE)
┌─────────────────────────────────▼────────────────────────────────┐
│      FastAPI Dashboard Server (`packages/plm_platform/dashboard`)│
│  - Static Asset Provider (`/static/*`, `/`)                      │
│  - Orchestrator Engine (`/api/pipeline/run`, `/api/pipeline/events`)
│  - Analytics & Arms Query Service (`/api/stats`, `/api/arms`)    │
│  - Audio Streaming Service (`/api/audio/{candidate_id}`)         │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ Python in-process / SQLite
┌─────────────────────────────────▼────────────────────────────────┐
│      PLM Domain Engines (P1 -> P2 -> P3 -> P4 -> P5 -> P6 -> P7 -> P8 -> P9) │
└──────────────────────────────────────────────────────────────────┘
```

### 2.1 Backend Structure (`packages/plm_platform/dashboard/`)
- `__init__.py`: Package export.
- `app.py`: FastAPI app definition, static file mounts, REST routers, and CORS configuration.
- `orchestrator.py`: In-process pipeline runner that executes the 10-step chain (P1 Market -> P2 A&R -> P3 Genome -> P4 Music -> P5 QA -> P6 Media -> P7 YouTube -> P8 Distribution -> P9 Analytics -> P2 Posterior update) and broadcasts execution events to SSE subscribers.
- `service.py`: Data access service querying SQLite tables or in-memory fixtures for dashboard metrics, bandit arm parameters, candidate catalogs, and releases.
- `static/`: Static web assets:
  - `index.html`: Modern semantic HTML structure with tab navigation, modal inspectors, and player controls.
  - `app.css`: Custom vanilla CSS design system with CSS variables, obsidian glassmorphism, responsive grid layouts, and glow animations.
  - `app.js`: ES6 client managing state, SSE event listening, Web Audio playback, and DOM updates.

### 2.2 CLI Integration (`packages/plm_platform/cli.py`)
Add `dashboard` command group to Typer:
```powershell
python -m plm_platform.cli dashboard [--host 127.0.0.1] [--port 8000] [--reload]
```

---

## 3. Detailed Interfaces & API Endpoints

### 3.1 Pipeline Control & Real-Time Telemetry
- **`POST /api/pipeline/run`**:
  - Request body: `{"mode": "full" | "step", "mock_audio": true}`
  - Response: `{"run_id": "run_xxx", "status": "started"}`
  - Spawns background task executing the lifecycle.
- **`GET /api/pipeline/events`**:
  - `text/event-stream` (SSE).
  - Emits events:
    - `stage_start`: `{stage: "p1_market", run_id: "...", title: "Market Trend Discovery"}`
    - `stage_log`: `{stage: "p1_market", message: "Found high-velocity niche 'synthwave_ambient'", level: "info"}`
    - `stage_complete`: `{stage: "p1_market", duration_ms: 120, output_summary: {...}}`
    - `pipeline_complete`: `{run_id: "...", final_reward: 0.88, arm_updated: "arm_synthwave"}`
    - `pipeline_error`: `{stage: "p5_qa", error: "Candidate rejected: duration mismatch"}`

### 3.2 Analytics & Operations
- **`GET /api/stats`**:
  - Returns global platform statistics:
    ```json
    {
      "total_cycles": 14,
      "active_arms": 5,
      "candidates_generated": 42,
      "cleared_releases": 38,
      "total_revenue_usd": 1240.50,
      "avg_normalized_reward": 0.72
    }
    ```
- **`GET /api/arms`**:
  - Returns bandit arms list with `arm_id`, `name`, `status`, `allocation_type`, `observation_count`, `posterior_mean`, and `confidence_interval`.
- **`GET /api/candidates`**:
  - Returns list of music candidates with IDs, track titles, genre/tempo, duration, rights status, and QA disposition.
- **`GET /api/audio/{candidate_id}`**:
  - Streams WAV audio. If the physical file is not on disk, synthesizes a melodious ambient chord sequence on the fly so audio playback works offline.
- **`GET /api/releases`**:
  - Returns completed media packages, mock YouTube video IDs, and DSP ISRC distribution records.
- **`POST /api/qa/resolve`**:
  - Request body: `{"case_id": "case_xxx", "resolution": "CLEARED_BY_OVERRIDE", "notes": "..."}`
  - Unblocks quarantined candidate and returns updated status.

---

## 4. Frontend Design & Interaction Specs

### 4.1 Aesthetic Tokens
- **Background**: Deep void obsidian (`#070a11` gradient to `#0d1322`).
- **Cards & Panels**: Glass acrylic with `background: rgba(18, 24, 38, 0.75)`, `border: 1px solid rgba(255, 255, 255, 0.08)`, `backdrop-filter: blur(16px)`.
- **Typography**: `Outfit` for headers/branding, `Inter` for interface elements, `JetBrains Mono` for IDs, metrics, and logs.
- **Color Coding**:
  - Accent Electric Violet: `#8b5cf6` (A&R / Bandit / Primary CTA)
  - Cyber Cyan: `#06b6d4` (Market / Discovery)
  - Warm Amber: `#f59e0b` (Music Factory / Synthesis)
  - Emerald Green: `#10b981` (QA Cleared / Live Published)
  - Crimson Rose: `#ef4444` (Quarantine / Hard Stops)

### 4.2 Tabbed Views
1. **Control Deck (Main)**:
   - Top banner: Real-time stat KPI cards.
   - Central control: "Run Autonomous Pipeline" CTA button with execution spinner.
   - Stepper: Horizontal 10-node stepper representing P1 through P9 + P2 Loop Closure.
     - Idle: dim icon.
     - Running: pulsing neon halo with spinning indicator.
     - Success: emerald glow with elapsed duration badge.
     - Failed: crimson alert badge with stop reason.
   - Real-time Console: Embedded terminal at bottom showing colored log lines and JSON payloads streamed from SSE.
2. **Music & Audio Vault**:
   - Audio card grid with title, genre, BPM, mood, and rights clearance pill.
   - Interactive Audio Player at bottom:
     - Play/Pause, timeline scrub bar, current/total time display.
     - Real-time HTML5 Canvas waveform animation.
     - One-click "Inspect Manifest" opening a modal with Seam C5 release manifest JSON.
3. **Bandit Intelligence**:
   - Visual distribution comparison for bandit arms.
   - Visual gauges for posterior mean win rate and exploration ratios.
4. **Quality & Rights Gate (QA)**:
   - Split view of Cleared vs Quarantined cases.
   - One-click override button with reason modal to resolve quarantined candidates.

---

## 5. Error Handling & Resilience
- **Offline & Standalone**: Operates with zero network or database dependencies by using SQLite and synthetic audio fixtures.
- **SSE Connection Resilience**: Client automatically attempts reconnection with exponential backoff if the server is restarted.
- **Hard-Stop Visualization**: If an intentional hard-stop occurs (e.g. P5 rejection or P7 preflight failure), the pipeline stepper accurately halts at that node, marks it in red, and logs the specific contract refusal reason in the telemetry console.

---

## 6. Verification & Quality Gates
- `tests/unit/test_dashboard_api.py`: 100% green tests covering all dashboard REST endpoints and audio streaming.
- `tests/unit/test_dashboard_orchestrator.py`: Verification of orchestrator step execution and event broadcast.
- CLI smoke check: `python -m plm_platform.cli dashboard --help` exits cleanly with zero errors.
- Linter & Type Safety: `ruff check .` clean and `mypy packages services` strict clean.
