# Wave 6 Implementation Plan: Loop Closure & End-to-End Acceptance

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Wave 6 of the Master Integration Plan (`docs/P0_master_integration_implementation_plan.md` §6), establishing the uncredentialed, hermetic, end-to-end autonomous loop P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8 → P9 → P2 (Ruling R11), verifying positive and negative integration chains, and finalizing all seam contracts (C1–C10).

**Architecture:** A mock-first, in-memory SQLite test harness spanning all nine domain engines. All cross-phase contracts adhere strictly to Rulings R1–R18 and `docs/contracts/CONTRACT_REGISTRY.md`. No real external network or credential dependencies.

**Tech Stack:** Python 3.12, FastAPI, SQLite (in-memory test engine), SQLAlchemy 2.0, Pydantic v2, pytest.

**Spec:** `docs/P0_master_integration_implementation_plan.md` §1 (Success criteria), §3 (Review focus), §5 (The ten seams), §6 (Wave 6), and Rulings R1–R18 in `docs/contracts/CONTRACT_REGISTRY.md`.

## Global Constraints

- **Hermetic & Offline-Safe**: Must not connect to external network, live YouTube, live Spotify, live distributor, or live PostgreSQL.
- **R9 Lineage Preservation**: All 6 identity fields (`experiment_id`, `experiment_family_id`, `content_arm_id`, `content_genome_id`, `production_directive_id`, `media_package_id`) must pass unbroken across P6 → P7 → P8 → P9.
- **R10 Boundary**: P8 versions raw distributor statements and references; P9 parses facts and calculates unit economics.
- **R11 Loop Closure**: P9 computes `normalized_reward` and `attribution_confidence` and posts `NormalizedRewardPayload` to P2 (`POST /ar/results`). P2 owns posterior updates and updates bandit allocation.
- **R13 Storage Layout**: All artifact URIs must use the 7 canonical prefixes: `market/`, `experiments/`, `music/`, `qa/`, `media/`, `reports/`, `analytics/`.
- **Quality Gates**: `pytest -m "not integration"` 100% green, `ruff check .` clean, `mypy packages services` strict clean.

---

### Task 1: Complete Contract Seams C8, C9, C10 & Contract Registry

**Files:**
- Create: `tests/contract/test_c8_analytics_to_p10.py`
- Create: `tests/contract/test_c9_storage_namespace.py`
- Create: `tests/contract/test_c10_engineering_prelude.py`
- Modify: `docs/contracts/CONTRACT_REGISTRY.md:35-47,595-671`

**Interfaces:**
- Consumes: `services/analytics/domain/models.py:P10FeatureView`, `packages/plm_platform/storage.py`, `packages/plm_contracts/`
- Produces: 100% frozen seam test coverage for all 10 seams (C1 through C10) in `tests/contract/`

- [ ] **Step 1: Write test_c8_analytics_to_p10.py**
Assert `GET /analytics/p10/features` wire payload conforms to Seam C8 frozen schema in `CONTRACT_REGISTRY.md` (`unit_id`, `experiment_id`, `arm_id`, `content_genome_id`, `observation_window`, `maturity`, `primary_outcome`, `secondary_outcomes`, `quality`).

- [ ] **Step 2: Write test_c9_storage_namespace.py**
Assert `StorageClient` and prefix conventions enforce the 7 frozen prefixes (`market/`, `experiments/`, `music/`, `qa/`, `media/`, `reports/`, `analytics/`), content addressing, and checksum verification on read.

- [ ] **Step 3: Write test_c10_engineering_prelude.py**
Assert platform prelude imports cleanly, verifies `plm_contracts`, `plm_platform` configuration, observability, error shapes, and tri-state enums.

- [ ] **Step 4: Update CONTRACT_REGISTRY.md**
Update summary table and sections for C8, C9, C10 from `**pending**` to `**exists**`.

- [ ] **Step 5: Run contract suite**
Run: `pytest tests/contract/ -v`
Expected: 100% PASS across all seam tests.

- [ ] **Step 6: Commit**
```bash
git add tests/contract/ docs/contracts/CONTRACT_REGISTRY.md
git commit -m "test(contract): complete seam tests for C8, C9, C10 and finalize registry"
```

---

### Task 2: Implement Positive End-to-End Acceptance Chain (W6.1)

**Files:**
- Create: `tests/e2e/__init__.py`
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_end_to_end_chain.py`

**Interfaces:**
- Consumes: All 9 domain engines (`market_intelligence`, `autonomous_ar`, `experiments`, `music_factory`, `quality_gate`, `media_factory`, `youtube_publishing`, `distribution`, `analytics`)
- Produces: Deterministic end-to-end positive verification of the complete PLM lifecycle with loop closure into P2.

- [ ] **Step 1: Create test harness in tests/e2e/conftest.py**
Setup in-memory SQLite session fixtures, mock HTTP client for inter-phase dispatch, and mock storage directory for artifacts.

- [ ] **Step 2: Implement test_end_to_end_chain.py**
Implement the test `test_autonomous_music_engine_end_to_end_loop_closure`:
1. **P1 (Market)**: Ingests observation, scores opportunity for arm (`OpportunityObject`).
2. **P2 (A&R)**: Allocates opportunity into `ProductionDirective`.
3. **P3 (Genome/Experiments)**: Materializes `Experiment` and track specs (`music_specs[]`).
4. **P4 (Music Factory)**: Generates candidate audio with commercial rights snapshot (`music_candidate_ready_for_p5`).
5. **P5 (Quality Gate)**: Inspects audio, rights, and provenance, emitting `ReleaseManifest` (`PASSED`).
6. **P6 (Media Factory)**: Assembles playlist manifest, produces `MediaPackage` with video, thumbnails, and audio checksums.
7. **P7 (YouTube)**: Preflights, uploads via mock client, emits `YouTubeVideoPublishedEvent` with all 6 R9 fields.
8. **P8 (Distribution)**: Compiles release, preflights, submits via mock distributor, creates Seam C7 record with all 6 R9 fields.
9. **P9 (Analytics)**: Ingests YouTube metrics and DSP statement, calculates unit economics, computes `NormalizedRewardPayload` (Ruling R11), and posts to P2 `POST /ar/results`.
10. **P2 Loop Closure**: P2 processes result, runs `apply_pending_updates`, and updates Bayesian arm posterior. Verifies that the posterior mean increases and the subsequent allocation cycle prioritizes the rewarded arm.

- [ ] **Step 3: Run the positive end-to-end test**
Run: `pytest tests/e2e/test_end_to_end_chain.py -v`
Expected: PASS with full 10-step chain assertions green.

- [ ] **Step 4: Commit**
```bash
git add tests/e2e/
git commit -m "feat(e2e): implement positive end-to-end acceptance chain (W6.1)"
```

---

### Task 3: Implement Negative Chain & Hard-Stop Scenarios (W6.2)

**Files:**
- Create: `tests/e2e/test_negative_chain.py`

**Interfaces:**
- Consumes: P5 gate rejection, P6 assembler, P7 preflight validator, P8 distribution engine, P9 ingestion deduplicator.
- Produces: Verified enforcement of Review Focus 3 & 4 (quarantined/rejected candidates cannot reach publication, preflight failures block, duplicate submissions & statements are strictly idempotent).

- [ ] **Step 1: Implement test_negative_chain.py**
Include 4 hard-stop scenarios:
1. `test_p5_rejected_candidate_cannot_be_assembled_into_media_package`: P5 `REJECTED` candidate is refused by P6 assembler and cannot pass P8 preflight.
2. `test_p7_preflight_failure_blocks_youtube_publishing`: Missing synthetic media disclosure or missing master asset blocks YouTube publish intent (`BLOCKED_PREFLIGHT`).
3. `test_p8_duplicate_submission_is_strictly_idempotent`: Submitting the same release fingerprint twice returns existing distribution record without invoking distributor client twice.
4. `test_p9_duplicate_statement_ingestion_prevents_double_counting`: Re-submitting the same raw statement deduplicates records and preserves canonical ledger amounts without duplicate revenue.

- [ ] **Step 2: Run the negative chain tests**
Run: `pytest tests/e2e/test_negative_chain.py -v`
Expected: PASS for all 4 negative scenarios.

- [ ] **Step 3: Commit**
```bash
git add tests/e2e/test_negative_chain.py
git commit -m "feat(e2e): implement negative chain and hard-stop scenarios (W6.2)"
```

---

### Task 4: Whole-Program Verification & Master Plan Finalization (W6.3)

**Files:**
- Modify: `docs/P0_master_integration_implementation_plan.md:235-240`

- [ ] **Step 1: Run full non-integration test suite**
Run: `pytest -m "not integration"`
Expected: 100% green across all unit, contract, and e2e tests.

- [ ] **Step 2: Run linter and formatting checks**
Run: `ruff check .`
Expected: All checks passed.

- [ ] **Step 3: Run strict type checking**
Run: `mypy packages services`
Expected: Success: no issues found.

- [ ] **Step 4: Check CLI smoke commands**
Run: `python -m plm_platform.cli --help` and verify all phase subcommands exist.

- [ ] **Step 5: Update Master Plan checklist**
Mark Wave 6 tasks W6.1, W6.2, W6.3 as completed `[x]` in `docs/P0_master_integration_implementation_plan.md`.

- [ ] **Step 6: Commit**
```bash
git add docs/P0_master_integration_implementation_plan.md
git commit -m "chore: complete Wave 6 loop closure and finalize master implementation plan"
```
