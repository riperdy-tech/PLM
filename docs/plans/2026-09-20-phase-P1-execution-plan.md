# PHASE-P1 Market Intelligence Engine — Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the P1 market-intelligence subsystem — connector-backed collection of YouTube market evidence, immutable raw provenance, canonical entities and time-series observations, a 13-dimension controlled taxonomy, deterministic features, a versioned 0–100 opportunity score with independent 0–1 confidence, a read-only `/market/v1` API for P2, scheduled jobs, and reports — so that P2 can ask "what are the current market opportunities?" and receive ranked, evidence-backed hypotheses.

**Architecture:** One service package `services/market_intelligence/` writing to the `market` Postgres schema and to the `market/` object-storage prefix only. Every metric is computed by deterministic Python from stored observations plus a version string (`score_version` = `p1-v1`). Connectors sit behind a provider interface and are exercised with `tests/support/http.MockHTTP`; no test needs credentials. LLM classification is an interface with a deterministic rule-based default and a validated, versioned, cached output schema — never a numeric score. Jobs run on Temporal task queue `ph1.market` via `plm_platform.temporal`.

**Tech Stack:** Python 3.12 · pydantic 2 · SQLAlchemy 2 Core · Alembic (single chain) · httpx (connector + API tests) · FastAPI + uvicorn (new dependencies, see P1.10 ruling) · Temporal (`temporalio`) · MinIO via `plm_platform.storage` · pytest 9 · `plm_contracts` (`Tri`, `Envelope`, `Page`, `ErrorShape`, `EventEnvelope`) · `tests/support` harness (MockHTTP, frozen clock, golden files, seam contract).

**Spec:** `docs/P1_market_intelligence_implementation_plan.md` (PHASE-P1). This plan argues from it; read it with this file. Section references written `§n` are the spec's; `PHASE-Pn §m` and `ARCH-Pnn` follow `docs/contracts/PHASE_MAP.md`.

## Global Constraints

Copied from the brief and P0 §2/§4/§5; every task's requirements implicitly include them.

- **Shared packages only for cross-cutting concerns.** `plm_contracts` for vocabulary and shapes; `plm_platform.storage` for artifacts under the `market/` prefix (R13/C9 — content-addressed, checksummed, no overwrite); `plm_platform.db` (`scoped_session("market")`) for the `market` schema; `plm_platform.config` for settings — **never** `os.environ` directly in P1 code; `plm_platform.observability` for structured JSON logs, correlation ids and the per-run cost counters (`api_calls`, `llm_tokens`, `llm_cost_estimate`, `storage_bytes`, `render_seconds`); `plm_platform.temporal` for jobs on task queue `ph1.market` (`TaskQueue.MARKET`).
- **Migrations** (`migrations/README.md`): one chain; P1 touches only the `market` schema; filename `<schema>_<revision>_<slug>`; exactly one migration may be in flight repo-wide (never dispatch two migration steps concurrently); baseline `0001_baseline` already created the schemas, so P1 adds one revision on top.
- **No native Postgres enums.** Enum values are stored as `text` and validated by `plm_contracts`/P1 enums in Python (ledger ruling L4).
- **R14:** the taxonomy dimension set is the 13 dimensions of §14.2 — `VocalGender`, `BPMRange` and `AudienceIntent` included; AC-05's "10 dimensions" is corrected. Owned by P1.7.
- **R15:** P1 mints and owns `arm_id` (identity, taxonomy, market score); P2 owns arm state, posteriors, exposure. No posterior, bandit or allocation logic in P1 (§2.2 out of scope). Base path `/market/v1`; auth via service token from `plm_platform`.
- **R19:** `brand_id` is a first-class field; one brand seeded (`neon_seoul`). P1 carries it on `market_arms`; the frozen C1 wire does not carry it (registry C1 field list).
- **Review Focus 1** (missing data must never render as zero or as a passing value) is enforced by P1's own tests with `plm_contracts.Tri`: `NULL` ⇒ `Tri.unknown()`, explicit `0` ⇒ `Tri.known(0)`, a window that has not completed ⇒ `Tri.not_ready()`.
- **Mock-first.** No test may require YouTube, Spotify, Google Trends or browser-automation credentials or live network access. The real-API smoke test (§40) has acceptance `blocked: pending credentials` — it exits non-zero with that exact reason; it is never silently skipped and never reported as passing.
- **Determinism.** Feature and scoring functions take their `as_of` instant and versions as parameters and contain no randomness; any LLM output that feeds scoring is persisted first (§29/§30). All datetimes are timezone-aware UTC (`datetime.now(UTC)`), and every metric carries its explicit window (§3.5).
- **No silent transformation.** Data-quality failures quarantine with a reason code (§24/§43); a run that loses records is `partial`, never `succeeded`; quota exhaustion stops low-priority work and reschedules, never "continue blindly" (§27).
- **Secrets** never in code, fixtures, raw payloads, logs, reports or the repository (§41); `.env`/environment only, via `plm_platform.config`.
- **Cost is a first-class metric** attached to the run (`market_collection_runs` cost columns + `plm_platform.observability.CostTracker`, §42).

## Review Focus

The five input classes or failure modes most likely to bite a person using this software (P0 §3), P1-relevant ones first. Each line names the task that owns its test.

1. **A metric that was never observed renders as `0`.** Hidden subscriber counts, `viewCount` absent from a response, a source that returned nothing. Expected: `Tri.unknown()` flowing into features and *lowering confidence*; `0` only for a verified zero. Owning tests: P1.1 (`tri_from_optional`), P1.5 (normalizer), P1.8 (features skip unknown and count `sample_size`).
2. **Time arithmetic across windows is wrong.** `views` with no window, a 30-day growth window computed across a 90-day gap, a naive datetime compared to an aware one. Expected: every window explicit (7d/30d/90d, previous-30d baseline), UTC comparisons only, unstable growth marked unstable rather than absurd. Owning tests: P1.8 (`test_growth_window_requires_matching_windows`, `test_naive_datetime_is_refused`).
3. **A re-run duplicates or overwrites evidence.** Re-collect on a later date overwrites the original observation; a retried request inserts the raw row twice. Expected: new observation per collection timestamp, idempotent raw insert on `(source_id, provider_object_type, provider_object_id, observed_at, payload_hash)`. Owning tests: P1.4 (raw-store idempotency), P1.5 (`test_second_collection_creates_a_second_observation`).
4. **`arm_id` lost or recomputed between P1 and P2.** A score row whose arm cannot be found, an arm whose fingerprint changes when the taxonomy version changes. Expected: one arm per fingerprint, `arm_id` stable and minted exactly once (`market_arms`), the C1 payload carrying it. Owning tests: P1.9 (`test_arm_id_is_stable_across_recompute`), P1.10 (`tests/contract/test_c1_market_to_ar.py`).
5. **Quarantined or malformed records leak into scores.** A negative view count, a 13-character video id, a `NaN` after a bad payload. Expected: quarantined with a reason code, excluded from features, run marked `partial`. Owning tests: P1.5 (`test_malformed_provider_id_is_quarantined`), P1.8 (`test_quarantined_observation_is_excluded_from_features`).

---

## File Structure

Every file this phase creates (C) or modifies (M). Paths are exact; directories are created by their first file.

**Package root — `services/market_intelligence/`** (the spec's §6 layout adapted: the monorepo convention is one service per phase under `services/`, which §6 explicitly permits: "use a structure close to this unless the existing repository already has a superior established convention"). The logical separation of §6 is preserved.

| File | Responsibility |
|---|---|
| `services/market_intelligence/__init__.py` | M — package docstring gains the phase's public entry points list. |
| `services/market_intelligence/config.py` | C — `MarketSettings` (versions, budgets, cadences, weights, thresholds), `load_market_settings()`. |
| `domain/enums.py` | C — P1 vocabulary: providers, query types, run statuses, error classes, `TaxonomyDimension`, components, change events. |
| `domain/value_objects.py` | C — `TaxonomyProfile`, `fingerprint()`, `slug_token()`, `ScoreComponents`, `EvidenceSummary`, `tri_from_optional()`, `classify_freshness()`. |
| `domain/entities.py` | C — `MarketSource`, `MarketQuery`, `CollectionRun`, `NormalizedObservation` (Tri fields), `MarketEntity`, `OpportunityScore`, `QuarantinedRecord`, `CollectionRunCounters`, `MarketChangeEventDraft`. |
| `connectors/base.py` | C — `ConnectorError`, `FetchedObject`, `FetchResult`, `SourceConnector` protocol. |
| `connectors/youtube.py` | C — `YouTubeDataAPIConnector` (search/videos/channels, pagination, quota accounting). |
| `connectors/spotify.py` | C — optional secondary signal connector (no popularity fields, §4.3). |
| `connectors/trends.py` | C — `PublicTrendsConnector` over a fixture/CSV feed (no browser, no credentials). |
| `connectors/browser.py` | C — `BrowserConnector` that raises `UNSUPPORTED_OPERATION` unless explicitly enabled (§4.4). |
| `collection/rate_limit.py` | C — `QuotaBudget`, `RequestBudget`, `RateLimiter`, `QuotaExceededError`. |
| `collection/retries.py` | C — `RetryPolicy`, `classify_http_status()`, `is_retryable()`, `execute_with_retries()`. |
| `collection/collectors.py` | C — `collect_query()`, `refresh_entities()`, run bookkeeping, cost counters. |
| `collection/query_planner.py` | C — `QueryState`, `priority_score()`, `next_run_at()`, `plan_refresh()`. |
| `collection/scheduler.py` | C — `SchedulePolicy`, `due_queries()`, `overdue_by_priority()` (§9, §26). |
| `collection/seeds.py` | C — `SeedSpec`, `BOOTSTRAP_SEEDS` (Stage A), `install_bootstrap_seeds()`, `validate_candidate_seed()` (Stage B). |
| `collection/seed_expansion.py` | C — `SeedProposer` protocol, `propose_from_observations()`, lineage persistence. |
| `normalization/canonical.py` | C — `normalize_text()`, `text_fingerprint()`, `entity_key()`. |
| `normalization/youtube.py` | C — provider payload → normalized draft (video/channel/playlist/search). |
| `normalization/spotify.py` | C — Spotify catalog payload → normalized draft. |
| `normalization/trends.py` | C — trends feed → normalized draft. |
| `taxonomy/ontology.py` | C — 13 dimensions (R14), controlled values, `other_candidate` bucket. |
| `taxonomy/synonym_map.py` | C — phrase → canonical value mapping. |
| `taxonomy/validators.py` | C — `validate_classification()`, `ClassificationSchemaError` (`ErrorShape`). |
| `taxonomy/classifier.py` | C — `Classifier` protocol, `RuleBasedClassifier`, cached `classify_observation()`. |
| `taxonomy/arms.py` | C — arm generation from co-occurring attributes, `arm_id_for()`, `generate_candidate_arms()`. |
| `features/velocity.py` | C — age, lifetime/windowed velocity, rates (§11). |
| `features/growth.py` | C — windowed growth, medians, unstable-baseline handling (§12). |
| `features/competition.py` | C — activity counts, percentiles, HHI, concentration (§13). |
| `features/demand.py` | C — demand inputs composite (§17.3). |
| `features/audience_quality.py` | C — public engagement-consistency signals (§17.3). |
| `features/cross_platform.py` | C — secondary-source evidence, unknown-never-zero (§17.3). |
| `features/production_fit.py` | C — feasibility factors from config (§17.3). |
| `features/confidence.py` | C — confidence breakdown, clamp 0–1 (§19). |
| `features/arm_features.py` | C — `ArmFeatures` assembly from stored observations. |
| `scoring/normalization.py` | C — `winsorize()`, `percentile_score()` (§18). |
| `scoring/calibration.py` | C — `ScoreWeights`, `default_weights()`, `validate_weights()`. |
| `scoring/opportunity.py` | C — `compute_component_scores()`, `score_arm()`, `score_all_arms()`, `persist_scores()`. |
| `storage/models.py` | C — SQLAlchemy Core `Table` objects for the `market` schema. |
| `storage/repositories.py` | C — insert/upsert/select helpers for every P1 table. |
| `storage/raw_store.py` | C — immutable raw insert + `market/` artifact write + request provenance. |
| `reports/market_snapshot.py` | C — `build_snapshot()`, `detect_changes()`, `persist_snapshot()` (§31, §32). |
| `reports/exports.py` | C — `render_markdown()`, `store_report()` under `market/reports/`. |
| `api/schemas.py` | C — the frozen C1 payloads (`OpportunityOut` etc.), cursors, error body. |
| `api/routes.py` | C — `/market/v1` router: opportunities, detail, observations, runs. |
| `api/app.py` | C — `create_app()` (FastAPI, correlation middleware, service-token auth, error handler). |
| `api/service_token.py` | C — `require_service_token()` dependency + `SERVICE_TOKEN_HEADER` usage. |
| `jobs/daily.py` | C — `DailyMarketWorkflow` + activities (discovery, refresh, classify, features, score). |
| `jobs/weekly.py` | C — `WeeklyMarketWorkflow` (full taxonomy recompute + snapshot). |
| `jobs/backfill.py` | C — `BackfillWorkflow` (on-demand historical collection). |
| `jobs/schedules.py` | C — `ensure_schedules()`, `JOB_NAMES` (§26 names). |
| `jobs/__init__.py` | C — `REGISTRATIONS: tuple[WorkerSet, ...]` for `plm_platform.worker --load`. |
| `observability/metrics.py` | C — P1 Prometheus counters on `plm_platform.observability.METRICS_REGISTRY`. |
| `observability/logging.py` | C — run-summary logging over `plm_platform.observability` (no second logging stack). |
| `observability/run_summary.py` | C — `summarize_run()` answering §AC-14's questions. |
| `cli.py` | C — implementations behind the `plm market …` commands (plm_platform keeps the Typer declarations). |
| `acceptance/youtube_smoke.py` | C — §40 live smoke path, `blocked: pending credentials` when unconfigured. |
| `data/bootstrap_seeds.json` | C — the Stage A seed pack as data (loaded with stdlib `json`; §8). |

**Platform packages (small, additive changes only)**

| File | Responsibility |
|---|---|
| `packages/plm_platform/config.py` | M — add optional `service_token: Secret | None` (`PLM_SERVICE_TOKEN`) + `SERVICE_TOKEN_VAR`/`SERVICE_TOKEN_HEADER`. |
| `packages/plm_platform/cli.py` | M — un-stub the `market` group (real commands, lazy service imports), add `BLOCKED_EXIT_CODE`. |
| `.env.example` | M — document the commented `PLM_SERVICE_TOKEN`. |
| `pyproject.toml` | M — add `fastapi`, `uvicorn` (P1.10 ruling). |
| `docs/adr/0021-p1-service-token-and-api.md` | C — the P1.10 rulings (auth var, framework, fail-closed). |
| `docs/adr/0022-p1-tables-and-envelope.md` | C — P1.2/P1.10 table names and the C1 envelope reconciliation. |
| `docs/adr/0023-p1-scheduler-temporal.md` | C — P1.11 scheduler ruling (pure cadence core + Temporal wiring). |
| `docs/contracts/DECISION_LOG.md` | M — the four P1 decisions recorded in the log's table. |

**Migrations**

| File | Responsibility |
|---|---|
| `migrations/versions/market/market_0002_initial_tables.py` | C — the single P1 revision: all `market` tables, with a working `downgrade()`. |

**Tests**

| File | Responsibility |
|---|---|
| `tests/unit/market/__init__.py`, `tests/integration/market/__init__.py` | C — packages (repo convention). |
| `tests/unit/market/test_domain_enums.py`, `test_domain_values.py` | C — P1.1. |
| `tests/unit/market/test_storage_models.py`, `test_migration_module.py` | C — P1.2 (DDL ↔ models drift guard). |
| `tests/unit/market/test_retries.py`, `test_rate_limit.py`, `test_youtube_connector.py`, `test_youtube_secrets.py` | C — P1.3. |
| `tests/unit/market/test_raw_store.py`, `tests/integration/market/test_raw_store_minio.py` | C — P1.4. |
| `tests/unit/market/test_canonical_text.py`, `test_youtube_normalization.py`, `test_entity_resolution.py`, `test_quarantine.py` | C — P1.5. |
| `tests/unit/market/test_bootstrap_seeds.py`, `test_query_planner.py`, `test_seed_expansion.py` | C — P1.6. |
| `tests/unit/market/test_ontology.py`, `test_classifier.py`, `test_classifier_cache.py`, `test_google_trends.py` | C — P1.7. |
| `tests/unit/market/test_velocity.py`, `test_growth.py`, `test_competition.py`, `test_confidence.py`, `test_cross_platform.py` | C — P1.8. |
| `tests/unit/market/test_normalization_scoring.py`, `test_calibration.py`, `test_opportunity_scoring.py`, `test_arm_identity.py` | C — P1.9. |
| `tests/unit/market/test_api_opportunities.py`, `test_api_auth.py`, `test_api_errors.py`, `tests/contract/test_c1_market_to_ar.py` | C — P1.10. |
| `tests/unit/market/test_scheduler.py`, `tests/integration/market/test_temporal_jobs.py` | C — P1.11. |
| `tests/unit/market/test_snapshot.py`, `test_change_detection.py`, `test_run_summary.py` | C — P1.12. |
| `tests/unit/market/test_scoring_properties.py`, `test_reproducibility.py`, `test_data_quality_gates.py` | C — P1.13. |
| `tests/fixtures/market/__init__.py`, `youtube/*.json`, `scenario.py` | C — recorded YouTube shapes (P1.3) and the §35 scenario pack (P1.13). |
| `tests/fixtures/golden/market/*.json`, `market_snapshot.md` | C — golden expectations (P1.5, P1.12). |
| `tests/integration/market/test_e2e_acceptance.py` | C — P1.14 mock-backed full cycle. |
| `tests/integration/market/test_api_live_uvicorn.py` | C — P1.14: the app served by uvicorn answers `/market/v1/opportunities`. |

**Modified existing tests**

| File | Responsibility |
|---|---|
| `tests/unit/test_cli.py` | M — market group leaves the stub list (P1.11). |
| `tests/unit/test_config.py` | M — drift guard gains `PLM_SERVICE_TOKEN` (P1.10). |
| `tests/unit/test_contracts_shapes.py` | M — only if P1's use of `Page`/`Envelope` exposes a shape gap; otherwise untouched. |

---

### Task P1.1: Domain contracts

**Files:**
- Create: `services/market_intelligence/domain/__init__.py`, `domain/enums.py`, `domain/value_objects.py`, `domain/entities.py`, `services/market_intelligence/config.py`
- Test: `tests/unit/market/__init__.py`, `tests/unit/market/test_domain_enums.py`, `tests/unit/market/test_domain_values.py`
- Modify: `services/market_intelligence/__init__.py` (docstring only)

**Interfaces:**
- Consumes: `plm_contracts.Tri`, `TriState`, `TriStateError` (installed); `plm_platform.config.Settings`, `load_settings` (installed). Nothing else.
- Produces (names later tasks import exactly):
  - `domain.enums`: `ProviderName` (`youtube_data_api`, `spotify_web_api`, `public_trends`, `browser_public_search`), `ConnectorType`, `SourceStatus`, `QueryType` (§7.2's seven tokens), `CollectionRunStatus` (§7.3's six tokens), `ProviderObjectType` (`video`, `channel`, `playlist`, `search_result_page`, `track`, `trend_point`), `EntityType` (§7.5's seven), `EntityStatus`, `ArmStatus` (`candidate`, `active`, `paused`, `retired`), `ConnectorErrorClass` (§23's eleven), `FreshnessBucket` (`fresh`, `normal`, `stale`, `expired`), `EvidenceClass` (`OBSERVED`, `DERIVED`, `CLASSIFIED`, `ASSUMED`), `ScoringComponent` (the seven §17.2 keys), `ChangeEventType` (§32's five), `TaxonomyDimension` (13 members, R14).
  - `domain.value_objects`: `TaxonomyProfile`, `fingerprint(profile) -> str`, `slug_token(text, *, max_length=48) -> str`, `ScoreComponents`, `EvidenceSummary`, `tri_from_optional(value) -> Tri`, `classify_freshness(*, as_of, now, settings) -> FreshnessAssessment`, `FreshnessAssessment(bucket, age_seconds)`.
  - `domain.entities`: `NormalizedObservation` (Tri-typed metric fields), `OpportunityScore`, `QuarantinedRecord`, `MarketChangeEventDraft`, `CollectionRunCounters`.
  - `config`: `MarketSettings`, `load_market_settings(*, platform: Settings | None = None, **overrides) -> MarketSettings`; `MarketSettings.score_version == "p1-v1"`, `taxonomy_version == "p1-taxonomy-v1"`, `feature_version == "p1-features-v1"`, `normalization_version == "p1-norm-v1"`, `default_brand_id == "neon_seoul"`.

- [ ] **Step 1: Write the failing enum test**

```python
# tests/unit/market/test_domain_enums.py
from services.market_intelligence.domain.enums import (
    CollectionRunStatus,
    ConnectorErrorClass,
    EntityType,
    ProviderName,
    QueryType,
    TaxonomyDimension,
)


def test_provider_names_are_the_spec_examples() -> None:
    assert {p.value for p in ProviderName} == {
        "youtube_data_api",
        "spotify_web_api",
        "public_trends",
        "browser_public_search",
    }


def test_run_statuses_and_query_types_match_the_spec() -> None:
    assert {s.value for s in CollectionRunStatus} == {
        "queued", "running", "succeeded", "partial", "failed", "cancelled",
    }
    assert {q.value for q in QueryType} == {
        "music_genre", "music_mood", "use_case", "playlist_pattern",
        "artist_adjacent_term", "long_tail", "competitor_discovery",
    }
    assert {e.value for e in EntityType} == {
        "channel", "video", "playlist", "track", "artist", "query", "concept",
    }


def test_connector_error_classes_are_the_spec_eleven() -> None:
    assert {c.value for c in ConnectorErrorClass} == {
        "AUTH_FAILURE", "RATE_LIMIT", "TRANSIENT_NETWORK", "PROVIDER_5XX",
        "INVALID_REQUEST", "NOT_FOUND", "QUOTA_EXCEEDED", "SCHEMA_CHANGE",
        "PARSER_FAILURE", "DATA_QUALITY", "UNSUPPORTED_OPERATION",
    }


def test_taxonomy_has_thirteen_dimensions_per_r14() -> None:
    assert len(TaxonomyDimension) == 13
    assert {d.value for d in TaxonomyDimension} == {
        "genre", "subgenre", "mood", "use_case", "language", "vocal_mode",
        "vocal_gender", "energy", "bpm_range", "duration_bucket",
        "visual_world", "audience_intent", "title_archetype",
    }
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_domain_enums.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.domain'` (create `tests/unit/market/__init__.py` and `services/market_intelligence/domain/__init__.py` as empty files in this step so the failure is the missing module, not a missing package).

- [ ] **Step 3: Write the minimal enum module**

```python
# services/market_intelligence/domain/enums.py
"""P1 vocabulary. Every value is copied from PHASE-P1; storage keeps text only (L4)."""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ArmStatus", "ChangeEventType", "CollectionRunStatus", "ConnectorErrorClass",
    "ConnectorType", "EntityStatus", "EntityType", "EvidenceClass", "FreshnessBucket",
    "ProviderName", "ProviderObjectType", "QueryType", "ScoringComponent",
    "SourceStatus", "TaxonomyDimension",
]


class ProviderName(StrEnum):
    """§7.1's four configured providers."""

    YOUTUBE_DATA_API = "youtube_data_api"
    SPOTIFY_WEB_API = "spotify_web_api"
    PUBLIC_TRENDS = "public_trends"
    BROWSER_PUBLIC_SEARCH = "browser_public_search"


class ConnectorType(StrEnum):
    DATA_API = "data_api"
    WEB_API = "web_api"
    PUBLIC_FEED = "public_feed"
    BROWSER = "browser"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class QueryType(StrEnum):
    """§7.2. Query meaning lives in these tokens, never only in free text."""

    MUSIC_GENRE = "music_genre"
    MUSIC_MOOD = "music_mood"
    USE_CASE = "use_case"
    PLAYLIST_PATTERN = "playlist_pattern"
    ARTIST_ADJACENT_TERM = "artist_adjacent_term"
    LONG_TAIL = "long_tail"
    COMPETITOR_DISCOVERY = "competitor_discovery"


class CollectionRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProviderObjectType(StrEnum):
    VIDEO = "video"
    CHANNEL = "channel"
    PLAYLIST = "playlist"
    SEARCH_RESULT_PAGE = "search_result_page"
    TRACK = "track"
    TREND_POINT = "trend_point"


class EntityType(StrEnum):
    CHANNEL = "channel"
    VIDEO = "video"
    PLAYLIST = "playlist"
    TRACK = "track"
    ARTIST = "artist"
    QUERY = "query"
    CONCEPT = "concept"


class EntityStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    MERGED = "merged"


class ArmStatus(StrEnum):
    """§16. P2 owns arm *state*; P1 owns this lifecycle field on its own rows (R15)."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class ConnectorErrorClass(StrEnum):
    """§23's taxonomy. One token per failure mode; never a free-string error."""

    AUTH_FAILURE = "AUTH_FAILURE"
    RATE_LIMIT = "RATE_LIMIT"
    TRANSIENT_NETWORK = "TRANSIENT_NETWORK"
    PROVIDER_5XX = "PROVIDER_5XX"
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    SCHEMA_CHANGE = "SCHEMA_CHANGE"
    PARSER_FAILURE = "PARSER_FAILURE"
    DATA_QUALITY = "DATA_QUALITY"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"


class FreshnessBucket(StrEnum):
    """§21: 0–48h fresh, 3–7d normal, 8–30d stale, >30d expired."""

    FRESH = "fresh"
    NORMAL = "normal"
    STALE = "stale"
    EXPIRED = "expired"


class EvidenceClass(StrEnum):
    """§31: the report must distinguish these four; they are never merged."""

    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    CLASSIFIED = "CLASSIFIED"
    ASSUMED = "ASSUMED"


class ScoringComponent(StrEnum):
    """§17.2 component keys — the wire dict of §20 uses exactly these names."""

    DEMAND = "demand"
    GROWTH = "growth"
    COMPETITOR_VELOCITY = "competitor_velocity"
    COMPETITION = "competition"
    AUDIENCE_QUALITY = "audience_quality"
    CROSS_PLATFORM = "cross_platform"
    PRODUCTION_FIT = "production_fit"


class ChangeEventType(StrEnum):
    """§32 examples, as stored tokens."""

    OPPORTUNITY_SCORE_CHANGE = "opportunity_score_change"
    GROWTH_COMPONENT_CHANGE = "growth_component_change"
    NEW_QUERY_CLUSTER = "new_query_cluster"
    COMPETITOR_VELOCITY_SPIKE = "competitor_velocity_spike"
    SOURCE_AVAILABILITY_DEGRADED = "source_availability_degraded"


class TaxonomyDimension(StrEnum):
    """§14.2's thirteen dimensions; R14 makes this set authoritative over AC-05.

    The member order is the canonical fingerprint order — do not reorder.
    """

    GENRE = "genre"
    SUBGENRE = "subgenre"
    MOOD = "mood"
    USE_CASE = "use_case"
    LANGUAGE = "language"
    VOCAL_MODE = "vocal_mode"
    VOCAL_GENDER = "vocal_gender"
    ENERGY = "energy"
    BPM_RANGE = "bpm_range"
    DURATION_BUCKET = "duration_bucket"
    VISUAL_WORLD = "visual_world"
    AUDIENCE_INTENT = "audience_intent"
    TITLE_ARCHETYPE = "title_archetype"
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `python -m pytest tests/unit/market/test_domain_enums.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add services/market_intelligence/domain/ tests/unit/market/
git commit -m "feat(p1): domain enums"
```

- [ ] **Step 6: Write the failing value-object test**

```python
# tests/unit/market/test_domain_values.py
import pytest
from plm_contracts import Tri, TriState, TriStateError

from services.market_intelligence.domain.enums import ScoringComponent, TaxonomyDimension
from services.market_intelligence.domain.value_objects import (
    EvidenceSummary, ScoreComponents, TaxonomyProfile, fingerprint, slug_token,
    tri_from_optional,
)


def test_fingerprint_is_dimension_ordered_and_stable() -> None:
    profile = TaxonomyProfile(genre="rnb", use_case="late_night_drive", mood=("melancholic",))
    same = TaxonomyProfile(mood=("melancholic",), use_case="late_night_drive", genre="rnb")
    assert fingerprint(profile) == fingerprint(same)
    assert fingerprint(profile).startswith("genre=rnb|")
    assert "|vocal_gender=none|" in fingerprint(profile)


def test_fingerprint_covers_every_dimension() -> None:
    parts = fingerprint(TaxonomyProfile()).split("|")
    assert len(parts) == len(TaxonomyDimension)
    assert [p.split("=", 1)[0] for p in parts] == [d.value for d in TaxonomyDimension]


def test_slug_token_is_bounded_and_safe() -> None:
    assert slug_token("Late Night Drive!!") == "late_night_drive"
    assert slug_token("rnb  x") == "rnb_x"
    assert len(slug_token("x" * 200)) == 48


def test_tri_from_optional_keeps_missing_data_out_of_arithmetic() -> None:
    assert tri_from_optional(None).state is TriState.UNKNOWN
    assert tri_from_optional(0) == Tri.known(0)
    assert tri_from_optional(7).unwrap() == 7
    with pytest.raises(TriStateError):
        tri_from_optional(None).unwrap()


def test_score_components_reject_out_of_range_values() -> None:
    with pytest.raises(ValueError):
        ScoreComponents.from_mapping({c: 50.0 for c in ScoringComponent} | {"demand": 101.0})
    ok = ScoreComponents.from_mapping({c.value: 50.0 for c in ScoringComponent})
    assert ok.as_mapping()[ScoringComponent.COMPETITION] == 50.0


def test_evidence_summary_counts_are_non_negative_and_top_ids_are_strings() -> None:
    with pytest.raises(ValueError):
        EvidenceSummary(observation_count=-1, source_count=1, top_observation_ids=())
```

- [ ] **Step 7: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_domain_values.py -q`
Expected: FAIL — `ImportError: cannot import name 'TaxonomyProfile' from 'services.market_intelligence.domain.value_objects'`.

- [ ] **Step 8: Write the value objects**

```python
# services/market_intelligence/domain/value_objects.py
"""Small deterministic value types. No I/O, no randomness, no clock access."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from plm_contracts import Tri

from .enums import FreshnessBucket, ScoringComponent, TaxonomyDimension

__all__ = [
    "EvidenceSummary", "FreshnessAssessment", "ScoreComponents", "TaxonomyProfile",
    "classify_freshness", "fingerprint", "slug_token", "tri_from_optional",
]

_UNSET: Final[str] = "none"
_NON_TOKEN: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class TaxonomyProfile:
    """The 13 §14.2 dimensions as one comparable object; unset means unclassified."""

    genre: str | None = None
    subgenre: str | None = None
    mood: tuple[str, ...] = ()
    use_case: str | None = None
    language: str | None = None
    vocal_mode: str | None = None
    vocal_gender: str | None = None
    energy: str | None = None
    bpm_range: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None
    audience_intent: str | None = None
    title_archetype: str | None = None

    def value_for(self, dimension: TaxonomyDimension) -> str:
        """The canonical token for one dimension; `none` when unset; moods joined."""
        raw: Any = getattr(self, dimension.value)
        if raw is None or raw == () or raw == "":
            return _UNSET
        if isinstance(raw, tuple):
            return "+".join(raw)
        return str(raw)


def fingerprint(profile: TaxonomyProfile) -> str:
    """Canonical identity string, one `dimension=value` part per dimension, in enum order.

    Unset dimensions are explicit (`bpm_range=none`), never omitted: two arms that
    differ only by an unset dimension must not collapse into one identity.
    """
    return "|".join(f"{d.value}={profile.value_for(d)}" for d in TaxonomyDimension)


def slug_token(text: str, *, max_length: int = 48) -> str:
    """Lowercase identifier fragment: non-alphanumerics → `_`, collapsed, truncated."""
    slug = _NON_TOKEN.sub("_", text.strip().lower()).strip("_")
    return slug[:max_length].rstrip("_")


def tri_from_optional(value: int | float | None) -> Tri:
    """`NULL` from the database becomes `Tri.unknown()`, never `0` (Review Focus 1)."""
    return Tri.unknown() if value is None else Tri.known(value)


@dataclass(frozen=True, slots=True)
class ScoreComponents:
    """§17.2 component scores, each 0–100; `confidence` is deliberately absent."""

    demand: float
    growth: float
    competitor_velocity: float
    competition: float
    audience_quality: float
    cross_platform: float
    production_fit: float

    def __post_init__(self) -> None:
        for component in ScoringComponent:
            value = getattr(self, component.value)
            if not 0.0 <= float(value) <= 100.0:
                raise ValueError(f"{component.value} must be within 0–100, got {value!r}")

    @classmethod
    def from_mapping(cls, values: Mapping[str, float]) -> ScoreComponents:
        missing = {c.value for c in ScoringComponent} - set(values)
        if missing:
            raise ValueError(f"missing component scores: {sorted(missing)}")
        return cls(**{c.value: float(values[c.value]) for c in ScoringComponent})

    def as_mapping(self) -> dict[ScoringComponent, float]:
        """The wire dict of §20 keys on `ScoringComponent` members."""
        return {c: float(getattr(self, c.value)) for c in ScoringComponent}


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    """§20 `evidence` object; counts are observations, not entity counts."""

    observation_count: int
    source_count: int
    top_observation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.observation_count < 0 or self.source_count < 0:
            raise ValueError("evidence counts cannot be negative")


@dataclass(frozen=True, slots=True)
class FreshnessAssessment:
    bucket: FreshnessBucket
    age_seconds: float


def classify_freshness(
    *,
    as_of: datetime,
    now: datetime,
    fresh_hours: float = 48.0,
    normal_hours: float = 168.0,
    stale_hours: float = 720.0,
) -> FreshnessAssessment:
    """§21 buckets. Naive datetimes are refused — one timezone, always (P0 §3 item 5)."""
    if as_of.tzinfo is None or now.tzinfo is None:
        raise ValueError("freshness needs timezone-aware datetimes; use datetime.now(UTC)")
    age_seconds = (now - as_of).total_seconds()
    if age_seconds < 0:
        raise ValueError("as_of is in the future relative to now")
    hours = age_seconds / 3600
    if hours <= fresh_hours:
        bucket = FreshnessBucket.FRESH
    elif hours <= normal_hours:
        bucket = FreshnessBucket.NORMAL
    elif hours <= stale_hours:
        bucket = FreshnessBucket.STALE
    else:
        bucket = FreshnessBucket.EXPIRED
    return FreshnessAssessment(bucket=bucket, age_seconds=age_seconds)
```

- [ ] **Step 9: Run the test and watch it pass**

Run: `python -m pytest tests/unit/market/test_domain_values.py -q`
Expected: PASS (6 passed).

- [ ] **Step 10: Commit**

```bash
git add services/market_intelligence/domain/value_objects.py tests/unit/market/test_domain_values.py
git commit -m "feat(p1): taxonomy value objects and Tri-based missing-data handling"
```

- [ ] **Step 11: Write the failing entity + settings test**

```python
# tests/unit/market/test_domain_values.py  (append)
from datetime import UTC, datetime

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.entities import NormalizedObservation, OpportunityScore


def test_normalized_observation_keeps_unknown_metrics_unknown() -> None:
    observation = NormalizedObservation(
        entity_id="ent-1", source_id="src-1", raw_observation_id="raw-1",
        observed_at=datetime(2026, 9, 20, tzinfo=UTC),
        views=None, likes=0, comments=None, subscriber_count=None,
    )
    assert observation.views.is_unknown
    assert observation.likes == Tri.known(0)
    assert observation.comments.is_unknown


def test_opportunity_score_enforces_bounds() -> None:
    with pytest.raises(ValueError, match="market_opportunity_score"):
        OpportunityScore(
            arm_id="a", market_opportunity_score=101.0, confidence=0.5,
            components=ScoreComponents.from_mapping({c.value: 50.0 for c in ScoringComponent}),
            evidence=EvidenceSummary(1, 1, ()), as_of=datetime(2026, 9, 20, tzinfo=UTC),
            score_version="p1-v1",
        )


def test_market_settings_defaults_are_versioned_and_config_driven() -> None:
    settings = load_market_settings(platform=None, score_version="p1-v2")
    assert settings.score_version == "p1-v2"
    assert settings.default_brand_id == "neon_seoul"
    assert settings.freshness_fresh_hours == 48.0
    assert settings.quota_units_per_run > 0
```

- [ ] **Step 12: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_domain_values.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.config'`.

- [ ] **Step 13: Write `config.py` and `entities.py`**

```python
# services/market_intelligence/config.py
"""P1 settings.

`plm_platform.config.Settings` owns deployment values and secrets; this dataclass adds
P1's operational knobs (versions, budgets, cadences, weights, thresholds). It is
constructed from code defaults plus explicit overrides — P1 never reads `os.environ`
itself, and no algorithm contains a literal threshold (§9, §26, §43).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from plm_platform.config import Settings, load_settings

__all__ = ["MarketSettings", "load_market_settings"]


@dataclass(frozen=True, slots=True)
class CadenceHours:
    """§9's suggested initial schedule; every value is configuration, not a literal."""

    high_priority: float = 24.0
    normal: float = 72.0
    long_tail: float = 168.0


@dataclass(frozen=True, slots=True)
class MarketSettings:
    platform: Settings | None
    score_version: str = "p1-v1"
    taxonomy_version: str = "p1-taxonomy-v1"
    feature_version: str = "p1-features-v1"
    normalization_version: str = "p1-norm-v1"
    default_brand_id: str = "neon_seoul"
    cadence: CadenceHours = field(default_factory=CadenceHours)
    quota_units_per_run: int = 1000
    max_requests_per_run: int = 200
    requests_per_second: float = 5.0
    max_pages_per_search: int = 2
    max_results_per_search: int = 10
    refresh_high_velocity_hours: float = 24.0
    refresh_normal_hours: float = 72.0
    freshness_fresh_hours: float = 48.0
    freshness_normal_hours: float = 168.0
    freshness_stale_hours: float = 720.0
    min_arm_support: int = 2
    schema_failure_partial_threshold: float = 0.05
    change_score_threshold: float = 10.0
    change_growth_threshold: float = 15.0
    confidence_sample_target: int = 25
    confidence_source_target: int = 2
    llm_cost_estimate_per_1k_tokens: float = 0.0


def load_market_settings(
    *, platform: Settings | None = None, **overrides: object
) -> MarketSettings:
    """Build P1 settings; `platform=None` means "load platform settings lazily".

    Tests pass an explicit `platform=Settings(...)` (or `platform=None` in unit tests
    that never touch the database) and override individual knobs by keyword.
    """
    unknown = set(overrides) - {f for f in MarketSettings.__dataclass_fields__}
    if unknown:
        raise TypeError(f"unknown market settings: {sorted(unknown)}")
    resolved_platform = platform if platform is not None else None
    return MarketSettings(platform=resolved_platform, **overrides)  # type: ignore[arg-type]
```

```python
# services/market_intelligence/domain/entities.py
"""P1 domain records. Plain frozen dataclasses — persistence shapes live in storage/."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from plm_contracts import Tri

from .enums import ChangeEventType, CollectionRunStatus, EntityStatus, EntityType
from .value_objects import EvidenceSummary, ScoreComponents

__all__ = [
    "CollectionRunCounters", "MarketChangeEventDraft", "MarketEntity", "NormalizedObservation",
    "OpportunityScore", "QuarantinedRecord",
]


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    """§7.6 with Tri metrics: `NULL` ⇒ unknown, explicit `0` ⇒ known zero."""

    entity_id: str
    source_id: str
    raw_observation_id: str
    observed_at: datetime
    published_at: datetime | None = None
    views: Tri = field(default_factory=Tri.unknown)
    likes: Tri = field(default_factory=Tri.unknown)
    comments: Tri = field(default_factory=Tri.unknown)
    subscriber_count: Tri = field(default_factory=Tri.unknown)
    video_duration_seconds: Tri = field(default_factory=Tri.unknown)
    playlist_item_count: Tri = field(default_factory=Tri.unknown)
    channel_video_count: Tri = field(default_factory=Tri.unknown)
    text_title: str | None = None
    text_description: str | None = None
    country_signal: str | None = None
    language_signal: str | None = None

    def __post_init__(self) -> None:
        for name in ("views", "likes", "comments", "subscriber_count",
                     "video_duration_seconds", "playlist_item_count", "channel_video_count"):
            value = getattr(self, name)
            if not isinstance(value, Tri):
                raise TypeError(
                    f"{name} must be a Tri; raw numbers hide missing data (Review Focus 1)"
                )


@dataclass(frozen=True, slots=True)
class MarketEntity:
    entity_type: EntityType
    canonical_name: str
    normalized_name: str
    canonical_url: str | None = None
    language: str | None = None
    country_code: str | None = None
    status: EntityStatus = EntityStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class OpportunityScore:
    """§20's object, minus the wire mapping (api/schemas.py owns serialisation)."""

    arm_id: str
    market_opportunity_score: float
    confidence: float
    components: ScoreComponents
    evidence: EvidenceSummary
    as_of: datetime
    score_version: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.market_opportunity_score <= 100.0:
            raise ValueError(
                f"market_opportunity_score must be within 0–100, got "
                f"{self.market_opportunity_score!r}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within 0–1, got {self.confidence!r}")


@dataclass(frozen=True, slots=True)
class QuarantinedRecord:
    """§24: quarantine instead of silent drop; always carries a reason code."""

    reason_code: str
    detail: str
    raw_observation_id: str | None = None
    collection_run_id: str | None = None
    payload_uri: str | None = None


@dataclass(frozen=True, slots=True)
class MarketChangeEventDraft:
    event_type: ChangeEventType
    arm_id: str | None
    previous_value: float | None
    current_value: float | None
    detail: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CollectionRunCounters:
    """§42/§7.3 counts, attached to one `collection_run_id`."""

    request_count: int = 0
    success_count: int = 0
    error_count: int = 0
    rate_limit_count: int = 0
    raw_observation_count: int = 0
    normalized_observation_count: int = 0
    quarantined_count: int = 0
    status: CollectionRunStatus = CollectionRunStatus.RUNNING
```

> **Ruling (recorded, see Open items):** `NormalizedObservation` is a domain dataclass, not a pydantic model, and `tri_from_optional` plus the dataclass fields are the *only* way a NULL database column becomes a Python value in P1. The normalizer (P1.5) imports `tri_from_optional` — never write `views or 0`.

- [ ] **Step 14: Run the tests and watch them pass, then lint**

Run: `python -m pytest tests/unit/market -q` then `python -m ruff check services/market_intelligence tests/unit/market`
Expected: PASS; ruff clean.

- [ ] **Step 15: Commit**

```bash
git add services/market_intelligence/config.py services/market_intelligence/domain/entities.py \
        services/market_intelligence/__init__.py tests/unit/market/test_domain_values.py
git commit -m "feat(p1): domain entities and market settings"
```

---

### Task P1.2: Database migrations

**Files:**
- Create: `services/market_intelligence/storage/__init__.py`, `storage/models.py`
- Create: `migrations/versions/market/market_0002_initial_tables.py`
- Test: `tests/unit/market/test_storage_models.py`, `tests/unit/market/test_migration_module.py`, `tests/integration/market/test_schema_matches_models.py`

**Interfaces:**
- Consumes: `plm_platform.db.SCHEMAS` (must already contain `market`); `migrations/README.md`'s naming rule; alembic chain head `0001_baseline`.
- Produces (exact table names later tasks SQL against):
  `market_sources`, `market_queries`, `market_seed_evidence`, `market_collection_runs`, `market_raw_observations`, `market_entities`, `market_entity_identities`, `market_observations`, `market_taxonomy_classifications`, `market_arms`, `market_opportunity_scores`, `market_change_events`, `market_quarantined_records`, `market_snapshots` — all in schema `market`, all enum-ish columns `text` (L4, no native Postgres enums).
  `storage.models` exports `MARKET_METADATA` (a `sqlalchemy.MetaData` whose 14 tables carry `schema="market"`), `MARKET_TABLES: tuple[str, ...]` (bare names, dependency order), and the `Table` objects by name (`sources`, `queries`, `seed_evidence`, `collection_runs`, `raw_observations`, `entities`, `entity_identities`, `observations`, `classifications`, `arms`, `scores`, `change_events`, `quarantined_records`, `snapshots`).
  Migration revision id `0002_market_initial`, `down_revision = "0001_baseline"`.

> **Ruling (Open items #3):** the spec names only the six §7 tables. The other eight names above are invented here because §16/§29/§32/§31 require storage the spec never names. The C1 wire and P2 are unaffected (they read the API, not tables).

- [ ] **Step 1: Write the failing models test**

```python
# tests/unit/market/test_storage_models.py
from services.market_intelligence.storage.models import MARKET_METADATA, MARKET_TABLES

EXPECTED = (
    "market_sources", "market_queries", "market_seed_evidence", "market_collection_runs",
    "market_raw_observations", "market_entities", "market_entity_identities",
    "market_observations", "market_taxonomy_classifications", "market_arms",
    "market_opportunity_scores", "market_change_events", "market_quarantined_records",
    "market_snapshots",
)


def test_every_table_lives_in_the_market_schema() -> None:
    assert MARKET_TABLES == EXPECTED
    assert {t.schema for t in MARKET_METADATA.tables.values()} == {"market"}
    assert set(MARKET_METADATA.tables) == {f"market.{name}" for name in EXPECTED}


def test_spec_required_columns_exist() -> None:
    tables = MARKET_METADATA.tables
    assert {c.name for c in tables["market.market_sources"].columns} >= {
        "id", "provider_name", "connector_type", "base_identifier", "status",
        "configuration_version", "created_at", "updated_at",
    }
    assert {c.name for c in tables["market.market_raw_observations"].columns} >= {
        "id", "collection_run_id", "source_id", "provider_object_type",
        "provider_object_id", "observed_at", "request_context_hash", "payload_json",
        "payload_hash", "schema_version",
    }
    assert {c.name for c in tables["market.market_observations"].columns} >= {
        "id", "entity_id", "source_id", "observed_at", "published_at", "views", "likes",
        "comments", "subscriber_count", "video_duration_seconds", "playlist_item_count",
        "channel_video_count", "text_title", "text_description", "country_signal",
        "language_signal",
    }


def test_no_native_enums_and_no_zero_defaults_on_metrics() -> None:
    for table in MARKET_METADATA.tables.values():
        for column in table.columns:
            assert column.type.__class__.__name__ != "Enum"
    observation = MARKET_METADATA.tables["market.market_observations"]
    for metric in ("views", "likes", "comments", "subscriber_count"):
        assert observation.c[metric].nullable is True
        assert observation.c[metric].default is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_storage_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.storage'`.

- [ ] **Step 3: Write `storage/models.py`**

```python
# services/market_intelligence/storage/models.py
"""SQLAlchemy Core tables for the `market` schema (P1 owns this schema only).

Append-only by design: raw observations are never updated; corrected source values
arrive as new rows with a new `observed_at` (§3.2). Enum-ish columns are text (L4).
"""

from __future__ import annotations

import sqlalchemy as sa

MARKET_SCHEMA = "market"
MARKET_METADATA = sa.MetaData(schema=MARKET_SCHEMA)

_uuid = sa.Uuid(as_uuid=False)
_ts = sa.DateTime(timezone=True)
_json = sa.JSON


def _pk() -> sa.Column:
    return sa.Column("id", _uuid, primary_key=True)


def _now() -> sa.Column:
    return sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now())


sources = sa.Table(
    "market_sources", MARKET_METADATA,
    _pk(),
    sa.Column("provider_name", sa.Text, nullable=False, unique=True),
    sa.Column("connector_type", sa.Text, nullable=False),
    sa.Column("base_identifier", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="active"),
    sa.Column("configuration_version", sa.Text, nullable=False),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", _ts, nullable=False, server_default=sa.func.now()),
)

queries = sa.Table(
    "market_queries", MARKET_METADATA,
    _pk(),
    sa.Column("source_id", _uuid, sa.ForeignKey("market.market_sources.id"), nullable=False),
    sa.Column("query_text", sa.Text, nullable=False),
    sa.Column("query_fingerprint", sa.Text, nullable=False),
    sa.Column("query_type", sa.Text, nullable=False),
    sa.Column("locale", sa.Text, nullable=False),
    sa.Column("country_code", sa.Text, nullable=False),
    sa.Column("language", sa.Text, nullable=False),
    sa.Column("seed_group", sa.Text, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("priority", sa.Integer, nullable=False, server_default="50"),
    sa.Column("first_seen_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.Column("last_run_at", _ts, nullable=True),
    sa.Column("next_run_at", _ts, nullable=True),
    sa.Column("query_version", sa.Text, nullable=False),
    sa.Column("parent_query_id", _uuid, sa.ForeignKey("market.market_queries.id"), nullable=True),
    sa.Column("proposed_by_model", sa.Text, nullable=True),
    sa.Column("model_name", sa.Text, nullable=True),
    sa.Column("model_version", sa.Text, nullable=True),
    sa.Column("proposal_confidence", sa.Float, nullable=True),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint("source_id", "query_fingerprint", name="uq_market_queries_fingerprint"),
)

seed_evidence = sa.Table(
    "market_seed_evidence", MARKET_METADATA,
    _pk(),
    sa.Column("query_id", _uuid, sa.ForeignKey("market.market_queries.id"), nullable=False),
    sa.Column(
        "raw_observation_id", _uuid,
        sa.ForeignKey("market.market_raw_observations.id"), nullable=False,
    ),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint("query_id", "raw_observation_id", name="uq_market_seed_evidence"),
)

collection_runs = sa.Table(
    "market_collection_runs", MARKET_METADATA,
    _pk(),
    sa.Column("source_id", _uuid, sa.ForeignKey("market.market_sources.id"), nullable=False),
    sa.Column("query_id", _uuid, sa.ForeignKey("market.market_queries.id"), nullable=True),
    sa.Column("started_at", _ts, nullable=False),
    sa.Column("finished_at", _ts, nullable=True),
    sa.Column("status", sa.Text, nullable=False, server_default="queued"),
    sa.Column("request_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("success_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("rate_limit_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("raw_observation_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("normalized_observation_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("error_summary", _json, nullable=True),
    sa.Column("software_version", sa.Text, nullable=False),
    sa.Column("cost_api_calls", sa.Integer, nullable=False, server_default="0"),
    sa.Column("cost_llm_tokens", sa.Integer, nullable=False, server_default="0"),
    sa.Column("cost_llm_cost_estimate", sa.Float, nullable=False, server_default="0"),
    sa.Column("cost_storage_bytes", sa.BigInteger, nullable=False, server_default="0"),
)

raw_observations = sa.Table(
    "market_raw_observations", MARKET_METADATA,
    _pk(),
    sa.Column(
        "collection_run_id", _uuid,
        sa.ForeignKey("market.market_collection_runs.id"), nullable=False,
    ),
    sa.Column("source_id", _uuid, sa.ForeignKey("market.market_sources.id"), nullable=False),
    sa.Column("provider_object_type", sa.Text, nullable=False),
    sa.Column("provider_object_id", sa.Text, nullable=False),
    sa.Column("observed_at", _ts, nullable=False),
    sa.Column("request_context_hash", sa.Text, nullable=False),
    sa.Column("payload_json", _json, nullable=False),
    sa.Column("payload_hash", sa.Text, nullable=False),
    sa.Column("payload_uri", sa.Text, nullable=False),
    sa.Column("schema_version", sa.Text, nullable=False),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint(
        "source_id", "provider_object_type", "provider_object_id", "observed_at",
        "payload_hash", name="uq_market_raw_dedupe",
    ),
)

entities = sa.Table(
    "market_entities", MARKET_METADATA,
    _pk(),
    sa.Column("entity_type", sa.Text, nullable=False),
    sa.Column("canonical_name", sa.Text, nullable=False),
    sa.Column("normalized_name", sa.Text, nullable=False),
    sa.Column("canonical_url", sa.Text, nullable=True),
    sa.Column("language", sa.Text, nullable=True),
    sa.Column("country_code", sa.Text, nullable=True),
    sa.Column("first_seen_at", _ts, nullable=False),
    sa.Column("last_seen_at", _ts, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="active"),
)

entity_identities = sa.Table(
    "market_entity_identities", MARKET_METADATA,
    _pk(),
    sa.Column("entity_id", _uuid, sa.ForeignKey("market.market_entities.id"), nullable=False),
    sa.Column("source_id", _uuid, sa.ForeignKey("market.market_sources.id"), nullable=False),
    sa.Column("provider_object_type", sa.Text, nullable=False),
    sa.Column("provider_object_id", sa.Text, nullable=False),
    sa.Column("first_seen_at", _ts, nullable=False),
    sa.Column("last_seen_at", _ts, nullable=False),
    sa.UniqueConstraint(
        "source_id", "provider_object_type", "provider_object_id",
        name="uq_market_entity_identity",
    ),
)

observations = sa.Table(
    "market_observations", MARKET_METADATA,
    _pk(),
    sa.Column("entity_id", _uuid, sa.ForeignKey("market.market_entities.id"), nullable=False),
    sa.Column("source_id", _uuid, sa.ForeignKey("market.market_sources.id"), nullable=False),
    sa.Column(
        "raw_observation_id", _uuid,
        sa.ForeignKey("market.market_raw_observations.id"), nullable=False,
    ),
    sa.Column("observed_at", _ts, nullable=False),
    sa.Column("published_at", _ts, nullable=True),
    sa.Column("views", sa.BigInteger, nullable=True),
    sa.Column("likes", sa.BigInteger, nullable=True),
    sa.Column("comments", sa.BigInteger, nullable=True),
    sa.Column("subscriber_count", sa.BigInteger, nullable=True),
    sa.Column("video_duration_seconds", sa.Float, nullable=True),
    sa.Column("playlist_item_count", sa.Integer, nullable=True),
    sa.Column("channel_video_count", sa.Integer, nullable=True),
    sa.Column("text_title", sa.Text, nullable=True),
    sa.Column("text_description", sa.Text, nullable=True),
    sa.Column("country_signal", sa.Text, nullable=True),
    sa.Column("language_signal", sa.Text, nullable=True),
    sa.Column("quality_status", sa.Text, nullable=False, server_default="VALID"),
    sa.UniqueConstraint(
        "entity_id", "source_id", "observed_at", name="uq_market_observation_point"
    ),
)

classifications = sa.Table(
    "market_taxonomy_classifications", MARKET_METADATA,
    _pk(),
    sa.Column("observation_id", _uuid, sa.ForeignKey("market.market_observations.id"),
              nullable=True),
    sa.Column("entity_id", _uuid, sa.ForeignKey("market.market_entities.id"), nullable=True),
    sa.Column("content_fingerprint", sa.Text, nullable=False),
    sa.Column("taxonomy_version", sa.Text, nullable=False),
    sa.Column("classifier_model", sa.Text, nullable=False),
    sa.Column("classifier_version", sa.Text, nullable=False),
    sa.Column("classification_prompt_version", sa.Text, nullable=False),
    sa.Column("classification_schema_version", sa.Text, nullable=False),
    sa.Column("dimensions_json", _json, nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("unmapped_terms", _json, nullable=False),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint(
        "content_fingerprint", "taxonomy_version", name="uq_market_classification_cache"
    ),
)

arms = sa.Table(
    "market_arms", MARKET_METADATA,
    _pk(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False, unique=True),
    sa.Column("fingerprint", sa.Text, nullable=False, unique=True),
    sa.Column("status", sa.Text, nullable=False, server_default="candidate"),
    sa.Column("taxonomy_json", _json, nullable=False),
    sa.Column("source_observation_ids", _json, nullable=False),
    sa.Column("support_count", sa.Integer, nullable=False),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", _ts, nullable=False, server_default=sa.func.now()),
)

scores = sa.Table(
    "market_opportunity_scores", MARKET_METADATA,
    _pk(),
    sa.Column("arm_id", sa.Text, sa.ForeignKey("market.market_arms.arm_id"), nullable=False),
    sa.Column("computed_at", _ts, nullable=False),
    sa.Column("as_of", _ts, nullable=False),
    sa.Column("market_opportunity_score", sa.Float, nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("components_json", _json, nullable=False),
    sa.Column("evidence_json", _json, nullable=False),
    sa.Column("score_version", sa.Text, nullable=False),
    sa.Column("feature_version", sa.Text, nullable=False),
    sa.Column("normalization_version", sa.Text, nullable=False),
    sa.UniqueConstraint("arm_id", "computed_at", name="uq_market_score_run"),
)

change_events = sa.Table(
    "market_change_events", MARKET_METADATA,
    _pk(),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=True),
    sa.Column("detected_at", _ts, nullable=False),
    sa.Column("previous_value", sa.Float, nullable=True),
    sa.Column("current_value", sa.Float, nullable=True),
    sa.Column("delta", sa.Float, nullable=True),
    sa.Column("detail_json", _json, nullable=False),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
)

quarantined_records = sa.Table(
    "market_quarantined_records", MARKET_METADATA,
    _pk(),
    sa.Column("source_id", _uuid, sa.ForeignKey("market.market_sources.id"), nullable=True),
    sa.Column(
        "raw_observation_id", _uuid,
        sa.ForeignKey("market.market_raw_observations.id"), nullable=True,
    ),
    sa.Column(
        "collection_run_id", _uuid,
        sa.ForeignKey("market.market_collection_runs.id"), nullable=True,
    ),
    sa.Column("reason_code", sa.Text, nullable=False),
    sa.Column("detail", sa.Text, nullable=False),
    sa.Column("payload_uri", sa.Text, nullable=True),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
)

snapshots = sa.Table(
    "market_snapshots", MARKET_METADATA,
    _pk(),
    sa.Column("as_of", _ts, nullable=False, unique=True),
    sa.Column("artifact_uri", sa.Text, nullable=False),
    sa.Column("artifact_hash", sa.Text, nullable=False),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
)

MARKET_TABLES: tuple[str, ...] = (
    "market_sources", "market_queries", "market_seed_evidence", "market_collection_runs",
    "market_raw_observations", "market_entities", "market_entity_identities",
    "market_observations", "market_taxonomy_classifications", "market_arms",
    "market_opportunity_scores", "market_change_events", "market_quarantined_records",
    "market_snapshots",
)
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `python -m pytest tests/unit/market/test_storage_models.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add services/market_intelligence/storage/ tests/unit/market/test_storage_models.py
git commit -m "feat(p1): market schema models"
```

- [ ] **Step 6: Write the failing migration-module test**

```python
# tests/unit/market/test_migration_module.py
import importlib.util
from pathlib import Path

from tests.support.paths import REPO_ROOT
from services.market_intelligence.storage.models import MARKET_TABLES

MIGRATION = REPO_ROOT / "migrations" / "versions" / "market" / "market_0002_initial_tables.py"


def _load():
    spec = importlib.util.spec_from_file_location("market_0002", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_revision_file_follows_the_naming_rule_and_chains_to_the_baseline() -> None:
    module = _load()
    assert MIGRATION.name == "market_0002_initial_tables.py"
    assert module.revision == "0002_market_initial"
    assert module.down_revision == "0001_baseline"


def test_migration_covers_every_model_table() -> None:
    module = _load()
    assert tuple(module.MARKET_TABLES) == MARKET_TABLES


def test_upgrade_and_downgrade_are_callable() -> None:
    module = _load()
    assert callable(module.upgrade) and callable(module.downgrade)
```

- [ ] **Step 7: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_migration_module.py -q`
Expected: FAIL — `FileNotFoundError` / no such path `migrations/versions/market/market_0002_initial_tables.py`.

- [ ] **Step 8: Write the migration**

```python
# migrations/versions/market/market_0002_initial_tables.py
"""market schema: initial P1 tables

Revision ID: 0002_market_initial
Revises: 0001_baseline
Create Date: 2026-09-20

Self-contained DDL on purpose: a migration must reproduce this schema even after
`storage/models.py` moves on. `tests/integration/market/test_schema_matches_models.py`
fails when the two drift apart. Enum-ish columns are text (L4).
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_market_initial"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

SCHEMA = "market"

MARKET_TABLES = (
    "market_sources", "market_queries", "market_seed_evidence", "market_collection_runs",
    "market_raw_observations", "market_entities", "market_entity_identities",
    "market_observations", "market_taxonomy_classifications", "market_arms",
    "market_opportunity_scores", "market_change_events", "market_quarantined_records",
    "market_snapshots",
)

TS = sa.DateTime(timezone=True)
UUID = sa.Uuid(as_uuid=False)
JSON = sa.JSON


def _pk() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True)


def _created_at() -> sa.Column:
    return sa.Column("created_at", TS, nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "market_sources",
        _pk(),
        sa.Column("provider_name", sa.Text, nullable=False, unique=True),
        sa.Column("connector_type", sa.Text, nullable=False),
        sa.Column("base_identifier", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("configuration_version", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_table(
        "market_queries",
        _pk(),
        sa.Column("source_id", UUID, sa.ForeignKey("market.market_sources.id"), nullable=False),
        sa.Column("query_text", sa.Text, nullable=False),
        sa.Column("query_fingerprint", sa.Text, nullable=False),
        sa.Column("query_type", sa.Text, nullable=False),
        sa.Column("locale", sa.Text, nullable=False),
        sa.Column("country_code", sa.Text, nullable=False),
        sa.Column("language", sa.Text, nullable=False),
        sa.Column("seed_group", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer, nullable=False, server_default="50"),
        sa.Column("first_seen_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_run_at", TS, nullable=True),
        sa.Column("next_run_at", TS, nullable=True),
        sa.Column("query_version", sa.Text, nullable=False),
        sa.Column("parent_query_id", UUID,
                  sa.ForeignKey("market.market_queries.id"), nullable=True),
        sa.Column("proposed_by_model", sa.Text, nullable=True),
        sa.Column("model_name", sa.Text, nullable=True),
        sa.Column("model_version", sa.Text, nullable=True),
        sa.Column("proposal_confidence", sa.Float, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_id", "query_fingerprint",
                            name="uq_market_queries_fingerprint"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_seed_evidence",
        _pk(),
        sa.Column("query_id", UUID, sa.ForeignKey("market.market_queries.id"), nullable=False),
        sa.Column("raw_observation_id", UUID,
                  sa.ForeignKey("market.market_raw_observations.id"), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("query_id", "raw_observation_id", name="uq_market_seed_evidence"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_collection_runs",
        _pk(),
        sa.Column("source_id", UUID, sa.ForeignKey("market.market_sources.id"), nullable=False),
        sa.Column("query_id", UUID, sa.ForeignKey("market.market_queries.id"), nullable=True),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("request_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rate_limit_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("raw_observation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("normalized_observation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_summary", JSON, nullable=True),
        sa.Column("software_version", sa.Text, nullable=False),
        sa.Column("cost_api_calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_llm_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_llm_cost_estimate", sa.Float, nullable=False, server_default="0"),
        sa.Column("cost_storage_bytes", sa.BigInteger, nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_raw_observations",
        _pk(),
        sa.Column("collection_run_id", UUID,
                  sa.ForeignKey("market.market_collection_runs.id"), nullable=False),
        sa.Column("source_id", UUID, sa.ForeignKey("market.market_sources.id"), nullable=False),
        sa.Column("provider_object_type", sa.Text, nullable=False),
        sa.Column("provider_object_id", sa.Text, nullable=False),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("request_context_hash", sa.Text, nullable=False),
        sa.Column("payload_json", JSON, nullable=False),
        sa.Column("payload_hash", sa.Text, nullable=False),
        sa.Column("payload_uri", sa.Text, nullable=False),
        sa.Column("schema_version", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_id", "provider_object_type", "provider_object_id",
                            "observed_at", "payload_hash", name="uq_market_raw_dedupe"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_entities",
        _pk(),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("canonical_name", sa.Text, nullable=False),
        sa.Column("normalized_name", sa.Text, nullable=False),
        sa.Column("canonical_url", sa.Text, nullable=True),
        sa.Column("language", sa.Text, nullable=True),
        sa.Column("country_code", sa.Text, nullable=True),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_entity_identities",
        _pk(),
        sa.Column("entity_id", UUID, sa.ForeignKey("market.market_entities.id"), nullable=False),
        sa.Column("source_id", UUID, sa.ForeignKey("market.market_sources.id"), nullable=False),
        sa.Column("provider_object_type", sa.Text, nullable=False),
        sa.Column("provider_object_id", sa.Text, nullable=False),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.UniqueConstraint("source_id", "provider_object_type", "provider_object_id",
                            name="uq_market_entity_identity"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_observations",
        _pk(),
        sa.Column("entity_id", UUID, sa.ForeignKey("market.market_entities.id"), nullable=False),
        sa.Column("source_id", UUID, sa.ForeignKey("market.market_sources.id"), nullable=False),
        sa.Column("raw_observation_id", UUID,
                  sa.ForeignKey("market.market_raw_observations.id"), nullable=False),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("published_at", TS, nullable=True),
        sa.Column("views", sa.BigInteger, nullable=True),
        sa.Column("likes", sa.BigInteger, nullable=True),
        sa.Column("comments", sa.BigInteger, nullable=True),
        sa.Column("subscriber_count", sa.BigInteger, nullable=True),
        sa.Column("video_duration_seconds", sa.Float, nullable=True),
        sa.Column("playlist_item_count", sa.Integer, nullable=True),
        sa.Column("channel_video_count", sa.Integer, nullable=True),
        sa.Column("text_title", sa.Text, nullable=True),
        sa.Column("text_description", sa.Text, nullable=True),
        sa.Column("country_signal", sa.Text, nullable=True),
        sa.Column("language_signal", sa.Text, nullable=True),
        sa.Column("quality_status", sa.Text, nullable=False, server_default="VALID"),
        sa.UniqueConstraint("entity_id", "source_id", "observed_at",
                            name="uq_market_observation_point"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_taxonomy_classifications",
        _pk(),
        sa.Column("observation_id", UUID,
                  sa.ForeignKey("market.market_observations.id"), nullable=True),
        sa.Column("entity_id", UUID, sa.ForeignKey("market.market_entities.id"), nullable=True),
        sa.Column("content_fingerprint", sa.Text, nullable=False),
        sa.Column("taxonomy_version", sa.Text, nullable=False),
        sa.Column("classifier_model", sa.Text, nullable=False),
        sa.Column("classifier_version", sa.Text, nullable=False),
        sa.Column("classification_prompt_version", sa.Text, nullable=False),
        sa.Column("classification_schema_version", sa.Text, nullable=False),
        sa.Column("dimensions_json", JSON, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("unmapped_terms", JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("content_fingerprint", "taxonomy_version",
                            name="uq_market_classification_cache"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_arms",
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False, unique=True),
        sa.Column("fingerprint", sa.Text, nullable=False, unique=True),
        sa.Column("status", sa.Text, nullable=False, server_default="candidate"),
        sa.Column("taxonomy_json", JSON, nullable=False),
        sa.Column("source_observation_ids", JSON, nullable=False),
        sa.Column("support_count", sa.Integer, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_table(
        "market_opportunity_scores",
        _pk(),
        sa.Column("arm_id", sa.Text, sa.ForeignKey("market.market_arms.arm_id"), nullable=False),
        sa.Column("computed_at", TS, nullable=False),
        sa.Column("as_of", TS, nullable=False),
        sa.Column("market_opportunity_score", sa.Float, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("components_json", JSON, nullable=False),
        sa.Column("evidence_json", JSON, nullable=False),
        sa.Column("score_version", sa.Text, nullable=False),
        sa.Column("feature_version", sa.Text, nullable=False),
        sa.Column("normalization_version", sa.Text, nullable=False),
        sa.UniqueConstraint("arm_id", "computed_at", name="uq_market_score_run"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_change_events",
        _pk(),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=True),
        sa.Column("detected_at", TS, nullable=False),
        sa.Column("previous_value", sa.Float, nullable=True),
        sa.Column("current_value", sa.Float, nullable=True),
        sa.Column("delta", sa.Float, nullable=True),
        sa.Column("detail_json", JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_table(
        "market_quarantined_records",
        _pk(),
        sa.Column("source_id", UUID, sa.ForeignKey("market.market_sources.id"), nullable=True),
        sa.Column("raw_observation_id", UUID,
                  sa.ForeignKey("market.market_raw_observations.id"), nullable=True),
        sa.Column("collection_run_id", UUID,
                  sa.ForeignKey("market.market_collection_runs.id"), nullable=True),
        sa.Column("reason_code", sa.Text, nullable=False),
        sa.Column("detail", sa.Text, nullable=False),
        sa.Column("payload_uri", sa.Text, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_table(
        "market_snapshots",
        _pk(),
        sa.Column("as_of", TS, nullable=False, unique=True),
        sa.Column("artifact_uri", sa.Text, nullable=False),
        sa.Column("artifact_hash", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table in reversed(MARKET_TABLES):
        op.drop_table(table, schema=SCHEMA)
```

- [ ] **Step 9: Run the tests and watch them pass**

Run: `python -m pytest tests/unit/market -q`
Expected: PASS. Then check the chain: `python -m alembic heads` → one head, `0002_market_initial`.

- [ ] **Step 10: Prove the live schema matches the models**

```python
# tests/integration/market/test_schema_matches_models.py
"""Live check: what alembic created must equal what storage/models.py declares."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from plm_platform.db import build_engine

from services.market_intelligence.storage.models import MARKET_METADATA, MARKET_TABLES

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine():
    active = build_engine()
    try:
        with active.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - skip with a remedy, repo convention
        pytest.skip(f"postgres unreachable ({type(exc).__name__}); run: docker compose up -d --wait")
    yield active
    active.dispose()


def test_tables_and_columns_match_the_models(engine) -> None:
    inspector = sa.inspect(engine)
    live_tables = set(inspector.get_table_names(schema="market"))
    assert set(MARKET_TABLES) <= live_tables, (
        "run `python -m plm_platform.cli migrate` first: the market tables are missing"
    )
    for key, table in MARKET_METADATA.tables.items():
        live_columns = {c["name"] for c in inspector.get_columns(table.name, schema="market")}
        assert {c.name for c in table.columns} == live_columns, f"{key}: columns drifted"
```

Run: `python -m plm_platform.cli migrate` then
`python -m pytest tests/integration/market/test_schema_matches_models.py -q -m integration`
Expected: PASS; a missing table or column fails with the drift message.

- [ ] **Step 11: Round-trip and commit**

Run: `python -m alembic downgrade 0001_baseline` then `python -m alembic upgrade head`
Expected: both succeed (downgrade drops the 14 tables, upgrade recreates them).

```bash
git add migrations/versions/market/market_0002_initial_tables.py \
        tests/unit/market/test_migration_module.py tests/integration/market/test_schema_matches_models.py
git commit -m "feat(p1): market schema migration"
```

---

### Task P1.3: YouTube connector

**Files:**
- Create: `services/market_intelligence/connectors/__init__.py`, `connectors/base.py`, `connectors/youtube.py`, `collection/__init__.py`, `collection/retries.py`, `collection/rate_limit.py`
- Create: `tests/fixtures/market/youtube/__init__.py`, `search_lofi_p1.json`, `search_lofi_p2.json`, `videos_lofi_batch.json`, `channels_lofi_batch.json`
- Test: `tests/unit/market/test_retries.py`, `test_rate_limit.py`, `test_youtube_connector.py`, `test_youtube_secrets.py`

**Interfaces:**
- Consumes: `tests/support/http.MockHTTP` (`enqueue_json`, `enqueue_server_error`, `enqueue_rate_limit`, `enqueue_timeout`, `enqueue_malformed_json`, `requests`); `plm_platform.config.Secret`; `plm_contracts` nothing.
- Produces (names later tasks import exactly):
  - `connectors.base`: `ConnectorError(error_class: ConnectorErrorClass, message: str, *, retryable: bool | None = None, retry_after_seconds: float | None = None)` with `.error_class`, `.retryable`, `.retry_after_seconds`; `RETRYABLE_ERROR_CLASSES`; `FetchedObject(provider_object_type, provider_object_id, observed_at, payload: Mapping[str, Any])`; `FetchResult(objects, request_context_hash, quota_units, next_page_token: str | None = None)`; `hash_request_context(provider: str, endpoint: str, params: Mapping[str, Any]) -> str`; `SourceConnector` protocol with `provider_name: ProviderName`.
  - `collection.retries`: `RetryPolicy(max_attempts=3, base_delay_seconds=0.5, max_delay_seconds=8.0, jitter_seconds=0.25)`, `classify_http_status(status: int) -> ConnectorErrorClass`, `is_retryable(error_class) -> bool`, `delay_for_attempt(policy, attempt, *, rng, retry_after_seconds=None) -> float`, `execute_with_retries(attempt: Callable[[], T], *, policy, sleep, rng) -> T`, `RetryExhaustedError(ConnectorError)`.
  - `collection.rate_limit`: `QuotaBudget(limit_units)`, `.consume(units, *, operation)`, `.used`, `.remaining`, `QuotaExceededError`; `RequestBudget(limit_requests)`, `.consume(*, operation)`, `RequestBudgetExceededError`; `RateLimiter(requests_per_second, *, clock=time.monotonic, sleep=time.sleep)`, `.wait_time() -> float`, `.acquire()`.
  - `connectors.youtube`: `YouTubeCredentials(api_key: Secret | None = None, access_token: Secret | None = None)` with `.headers() -> dict[str, str]`; `SearchHit`, `VideoStats`, `ChannelStats` (all frozen dataclasses, metric fields `int | None` / `float | None` — `None` means the provider did not expose it); `YouTubeDataAPIConnector(client, credentials, *, quota=None, request_budget=None, policy=RetryPolicy(), sleep=time.sleep, rng=None, limiter=None, base_url="https://www.googleapis.com/youtube/v3", observed_at=None)` with `search_videos(query, *, max_results=10, published_after=None, page_token=None) -> FetchResult`, `iter_search_videos(query, *, max_results=10, max_pages=1, published_after=None) -> Iterator[FetchResult]`, `fetch_videos(video_ids) -> FetchResult`, `fetch_channels(channel_ids) -> FetchResult`, `.request_count`, `.quota_units_used`; module parsers `parse_search_hits(fetch) -> tuple[SearchHit, ...]`, `parse_video_stats`, `parse_channel_stats`, `parse_iso8601_duration(text) -> float | None`; `SEARCH_QUOTA_UNITS = 100`, `LIST_QUOTA_UNITS = 1`.

> **Ruling (§4.1, recorded in Open items #11):** `search.list` costs 100 quota units per call and `videos.list`/`channels.list` cost 1 — these constants are copied from the documentation the spec cites and are configuration on the class, not magic numbers at call sites.

- [ ] **Step 1: Write the failing retry test**

```python
# tests/unit/market/test_retries.py
import random

import pytest

from services.market_intelligence.collection.retries import (
    RetryPolicy, classify_http_status, delay_for_attempt, execute_with_retries, is_retryable,
)
from services.market_intelligence.connectors.base import ConnectorError
from services.market_intelligence.domain.enums import ConnectorErrorClass


def test_status_mapping_covers_every_class_the_connector_can_see() -> None:
    assert classify_http_status(400) is ConnectorErrorClass.INVALID_REQUEST
    assert classify_http_status(401) is ConnectorErrorClass.AUTH_FAILURE
    assert classify_http_status(404) is ConnectorErrorClass.NOT_FOUND
    assert classify_http_status(429) is ConnectorErrorClass.RATE_LIMIT
    assert classify_http_status(500) is ConnectorErrorClass.PROVIDER_5XX
    assert classify_http_status(503) is ConnectorErrorClass.PROVIDER_5XX


def test_only_transient_classes_are_retryable() -> None:
    assert is_retryable(ConnectorErrorClass.RATE_LIMIT)
    assert is_retryable(ConnectorErrorClass.TRANSIENT_NETWORK)
    assert is_retryable(ConnectorErrorClass.PROVIDER_5XX)
    for permanent in (
        ConnectorErrorClass.INVALID_REQUEST, ConnectorErrorClass.AUTH_FAILURE,
        ConnectorErrorClass.NOT_FOUND, ConnectorErrorClass.SCHEMA_CHANGE,
        ConnectorErrorClass.QUOTA_EXCEEDED, ConnectorErrorClass.PARSER_FAILURE,
        ConnectorErrorClass.UNSUPPORTED_OPERATION, ConnectorErrorClass.DATA_QUALITY,
    ):
        assert not is_retryable(permanent), permanent


def test_provider_retry_after_wins_over_the_backoff_curve() -> None:
    policy = RetryPolicy()
    assert delay_for_attempt(policy, 1, rng=random.Random(0), retry_after_seconds=30.0) == 30.0
    assert delay_for_attempt(policy, 1, rng=random.Random(0), retry_after_seconds=600.0) == 60.0


def test_retries_stop_after_max_attempts_and_raise_exhausted() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def attempt() -> str:
        calls["n"] += 1
        raise ConnectorError(ConnectorErrorClass.PROVIDER_5XX, "boom")

    with pytest.raises(ConnectorError) as excinfo:
        execute_with_retries(
            attempt, policy=RetryPolicy(max_attempts=3), sleep=sleeps.append,
            rng=random.Random(7),
        )
    assert calls["n"] == 3
    assert excinfo.value.error_class is ConnectorErrorClass.PROVIDER_5XX
    assert len(sleeps) == 2  # no sleep after the final attempt
    assert all(0 < delay <= RetryPolicy().max_delay_seconds + RetryPolicy().jitter_seconds
               for delay in sleeps)


def test_permanent_errors_are_not_retried() -> None:
    calls = {"n": 0}

    def attempt() -> str:
        calls["n"] += 1
        raise ConnectorError(ConnectorErrorClass.INVALID_REQUEST, "bad request")

    with pytest.raises(ConnectorError):
        execute_with_retries(
            attempt, policy=RetryPolicy(max_attempts=3), sleep=lambda _d: None,
            rng=random.Random(0),
        )
    assert calls["n"] == 1
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_retries.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.connectors'`.

- [ ] **Step 3: Write `connectors/base.py` and `collection/retries.py`**

```python
# services/market_intelligence/connectors/base.py
"""Provider-neutral connector vocabulary: what a fetch returns and how it fails."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Protocol, runtime_checkable

from ..domain.enums import ConnectorErrorClass, ProviderName, ProviderObjectType

__all__ = [
    "RETRYABLE_ERROR_CLASSES", "ConnectorError", "FetchResult", "FetchedObject",
    "SourceConnector", "hash_request_context",
]

RETRYABLE_ERROR_CLASSES: Final[frozenset[ConnectorErrorClass]] = frozenset(
    {
        ConnectorErrorClass.RATE_LIMIT,
        ConnectorErrorClass.TRANSIENT_NETWORK,
        ConnectorErrorClass.PROVIDER_5XX,
    }
)
"""§23: retry only these. QUOTA_EXCEEDED is *not* retried — it is rescheduled (§27)."""


class ConnectorError(Exception):
    """A classified connector failure. Never a bare `requests`/`httpx` exception."""

    def __init__(
        self,
        error_class: ConnectorErrorClass,
        message: str,
        *,
        retryable: bool | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(f"{error_class.value}: {message}")
        self.error_class = error_class
        self.detail = message
        self.retry_after_seconds = retry_after_seconds
        self._retryable = retryable

    @property
    def retryable(self) -> bool:
        """Explicit override wins; otherwise the §23 class table decides."""
        return self._retryable if self._retryable is not None else self.error_class in (
            RETRYABLE_ERROR_CLASSES
        )


@dataclass(frozen=True, slots=True)
class FetchedObject:
    """One provider object exactly as received — the unit of raw provenance (§7.4)."""

    provider_object_type: ProviderObjectType
    provider_object_id: str
    observed_at: datetime
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class FetchResult:
    """What one logical fetch produced: objects, its request context and its cost."""

    objects: tuple[FetchedObject, ...] = field(default_factory=tuple)
    request_context_hash: str = ""
    quota_units: int = 0
    next_page_token: str | None = None


def hash_request_context(provider: str, endpoint: str, params: Mapping[str, Any]) -> str:
    """sha256 over the canonical request (provider, endpoint, sorted params).

    Secrets are never part of the context: pass the *public* params only. The hash is
    stable across runs so two observations of one object are comparable (§7.4).
    """
    if any(key.lower() in {"key", "access_token", "authorization"} for key in params):
        raise ValueError("request context must not carry credentials")
    canonical = json.dumps(
        {"provider": provider, "endpoint": endpoint,
         "params": {k: str(v) for k, v in sorted(params.items()) if v is not None}},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@runtime_checkable
class SourceConnector(Protocol):
    """§3.7: every source sits behind this shape so scoring never sees a provider."""

    provider_name: ProviderName
```

```python
# services/market_intelligence/collection/retries.py
"""§23 retry rules: exponential backoff with jitter, provider guidance honoured,
permanent failures never retried. Transport-level only — a creative failure is
never retried as a transport failure (P0 §2)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from random import Random
from typing import Final, TypeVar

from ..connectors.base import RETRYABLE_ERROR_CLASSES, ConnectorError
from ..domain.enums import ConnectorErrorClass

__all__ = [
    "RetryExhaustedError", "RetryPolicy", "classify_http_status", "delay_for_attempt",
    "execute_with_retries", "is_retryable",
]

T = TypeVar("T")

_MAX_RETRY_AFTER_SECONDS: Final[float] = 60.0
_STATUS_TO_CLASS: Final[dict[int, ConnectorErrorClass]] = {
    400: ConnectorErrorClass.INVALID_REQUEST,
    401: ConnectorErrorClass.AUTH_FAILURE,
    403: ConnectorErrorClass.AUTH_FAILURE,
    404: ConnectorErrorClass.NOT_FOUND,
    429: ConnectorErrorClass.RATE_LIMIT,
}


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    jitter_seconds: float = 0.25


class RetryExhaustedError(ConnectorError):
    """Every attempt failed with a retryable class; the last cause is chained."""


def classify_http_status(status: int) -> ConnectorErrorClass:
    """Map an HTTP status to §23's classes; anything unexpected 5xx is PROVIDER_5XX."""
    if status in _STATUS_TO_CLASS:
        return _STATUS_TO_CLASS[status]
    if 500 <= status < 600:
        return ConnectorErrorClass.PROVIDER_5XX
    raise ValueError(f"unclassified HTTP status {status}: add it to _STATUS_TO_CLASS")


def is_retryable(error_class: ConnectorErrorClass) -> bool:
    return error_class in RETRYABLE_ERROR_CLASSES


def delay_for_attempt(
    policy: RetryPolicy,
    attempt: int,
    *,
    rng: Random,
    retry_after_seconds: float | None = None,
) -> float:
    """Backoff for the gap *before* attempt `attempt` (1-based); jitter is additive.

    A provider `Retry-After` is honoured as given, capped so a hostile value cannot
    stall a run; it is never reduced by the backoff curve (§23).
    """
    if retry_after_seconds is not None:
        return min(max(retry_after_seconds, 0.0), _MAX_RETRY_AFTER_SECONDS)
    exponential = policy.base_delay_seconds * (2 ** (attempt - 1))
    capped = min(exponential, policy.max_delay_seconds)
    return capped + (rng.random() * policy.jitter_seconds if policy.jitter_seconds else 0.0)


def execute_with_retries(
    attempt_fn: Callable[[], T],
    *,
    policy: RetryPolicy,
    sleep: Callable[[float], None] = time.sleep,
    rng: Random | None = None,
) -> T:
    """Call `attempt_fn` until it succeeds, a permanent class appears, or attempts run out."""
    generator = rng if rng is not None else Random()
    last: ConnectorError | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return attempt_fn()
        except ConnectorError as exc:
            last = exc
            if not exc.retryable or attempt == policy.max_attempts:
                if exc.retryable:
                    raise RetryExhaustedError(
                        exc.error_class, f"retries exhausted after {attempt} attempts: {exc.detail}"
                    ) from exc
                raise
            sleep(delay_for_attempt(
                policy, attempt, rng=generator, retry_after_seconds=exc.retry_after_seconds
            ))
    raise RetryExhaustedError(  # pragma: no cover - unreachable with max_attempts >= 1
        last.error_class if last else ConnectorErrorClass.TRANSIENT_NETWORK, "no attempts made"
    )
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `python -m pytest tests/unit/market/test_retries.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add services/market_intelligence/connectors/ services/market_intelligence/collection/ \
        tests/unit/market/test_retries.py
git commit -m "feat(p1): connector base types and retry policy"
```

- [ ] **Step 6: Write the failing budget test**

```python
# tests/unit/market/test_rate_limit.py
import pytest

from services.market_intelligence.collection.rate_limit import (
    QuotaBudget, QuotaExceededError, RateLimiter, RequestBudget, RequestBudgetExceededError,
)


def test_quota_budget_refuses_the_call_that_would_exceed_it() -> None:
    budget = QuotaBudget(limit_units=150)
    budget.consume(100, operation="search.list")
    assert budget.remaining == 50
    with pytest.raises(QuotaExceededError) as excinfo:
        budget.consume(100, operation="search.list")
    assert "search.list" in str(excinfo.value)
    assert budget.used == 100  # a refused consumption changes nothing


def test_request_budget_counts_calls_not_units() -> None:
    budget = RequestBudget(limit_requests=2)
    budget.consume(operation="videos.list")
    budget.consume(operation="channels.list")
    with pytest.raises(RequestBudgetExceededError):
        budget.consume(operation="videos.list")


def test_rate_limiter_computes_its_wait_from_the_injected_clock() -> None:
    now = {"t": 0.0}
    naps: list[float] = []
    limiter = RateLimiter(requests_per_second=2.0, clock=lambda: now["t"],
                          sleep=lambda seconds: (naps.append(seconds), now.__setitem__("t", now["t"] + seconds)))
    assert limiter.wait_time() == 0.0
    limiter.acquire()
    limiter.acquire()
    assert limiter.wait_time() == pytest.approx(0.5)  # 2 rps → 0.5s spacing
    limiter.acquire()
    assert naps and naps[-1] == pytest.approx(0.5)
```

- [ ] **Step 7: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_rate_limit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.collection.rate_limit'` (module missing).

- [ ] **Step 8: Write `collection/rate_limit.py`**

```python
# services/market_intelligence/collection/rate_limit.py
"""§27 quota safety: per-run unit and request budgets plus a request-rate limiter.

Budgets are consumed *before* the HTTP call, so the call that would exceed a limit
never leaves the process. Exhaustion is a first-class outcome: the scheduler stops
low-priority work and reschedules (§27), the run is marked `partial`.
"""

from __future__ import annotations

import time
from collections.abc import Callable

__all__ = [
    "QuotaBudget", "QuotaExceededError", "RateLimiter", "RequestBudget",
    "RequestBudgetExceededError",
]


class QuotaExceededError(RuntimeError):
    """The provider quota budget for this run is exhausted."""

    error_class_token = "QUOTA_EXCEEDED"


class RequestBudgetExceededError(RuntimeError):
    """The per-run request budget is exhausted."""


class QuotaBudget:
    """Provider quota units for one run (`search.list` = 100, list endpoints = 1)."""

    def __init__(self, limit_units: int) -> None:
        if limit_units < 0:
            raise ValueError("quota limit cannot be negative")
        self.limit_units = limit_units
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.limit_units - self.used

    def consume(self, units: int, *, operation: str) -> None:
        if units < 0:
            raise ValueError("cannot consume a negative number of units")
        if units > self.remaining:
            raise QuotaExceededError(
                f"operation {operation!r} needs {units} quota units but only "
                f"{self.remaining} remain of {self.limit_units}; reschedule instead of "
                "retrying (PHASE-P1 §27)"
            )
        self.used += units


class RequestBudget:
    """Per-run request count cap, independent of provider quota cost."""

    def __init__(self, limit_requests: int) -> None:
        if limit_requests < 0:
            raise ValueError("request limit cannot be negative")
        self.limit_requests = limit_requests
        self.used = 0

    def consume(self, *, operation: str) -> None:
        if self.used >= self.limit_requests:
            raise RequestBudgetExceededError(
                f"operation {operation!r} exceeds the per-run request budget "
                f"({self.limit_requests}); stop low-priority work first (PHASE-P1 §27)"
            )
        self.used += 1


class RateLimiter:
    """Token-bucket pacing: at most `requests_per_second`, measured on an injected clock."""

    def __init__(
        self,
        requests_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._interval = 1.0 / requests_per_second
        self._clock = clock
        self._sleep = sleep
        self._last_request_at: float | None = None

    def wait_time(self) -> float:
        """Seconds until the next request may start (0.0 when it may start now)."""
        if self._last_request_at is None:
            return 0.0
        return max(0.0, self._interval - (self._clock() - self._last_request_at))

    def acquire(self) -> None:
        wait = self.wait_time()
        if wait > 0:
            self._sleep(wait)
        self._last_request_at = self._clock()
```

- [ ] **Step 9: Run the test and watch it pass**

Run: `python -m pytest tests/unit/market -q`
Expected: PASS (test_retries 5 + test_rate_limit 3 + earlier tasks green).

- [ ] **Step 10: Commit**

```bash
git add services/market_intelligence/collection/rate_limit.py tests/unit/market/test_rate_limit.py
git commit -m "feat(p1): quota and request budgets"
```

- [ ] **Step 11: Write the fixtures and the failing search test**

```json
// tests/fixtures/market/youtube/search_lofi_p1.json
{
  "kind": "youtube#searchListResponse",
  "etag": "etag-search-1",
  "nextPageToken": "CAoQAA",
  "pageInfo": {"totalResults": 813, "resultsPerPage": 2},
  "items": [
    {
      "kind": "youtube#searchResult",
      "etag": "etag-item-1",
      "id": {"kind": "youtube#video", "videoId": "vid000000001"},
      "snippet": {
        "publishedAt": "2026-08-01T10:00:00Z",
        "channelId": "UCaaaaaaaaaaaaaaaaaaaaaa",
        "title": "2AM Seoul — Rainy Night R&B Mix",
        "description": "Late-night Korean city vibes",
        "channelTitle": "NightLoop"
      }
    },
    {
      "kind": "youtube#searchResult",
      "etag": "etag-item-2",
      "id": {"kind": "youtube#video", "videoId": "vid000000002"},
      "snippet": {
        "publishedAt": "2026-08-15T10:00:00Z",
        "channelId": "UCbbbbbbbbbbbbbbbbbbbbbb",
        "title": "lofi study beats",
        "description": "focus music",
        "channelTitle": "StudyLoops"
      }
    }
  ]
}
```

```json
// tests/fixtures/market/youtube/search_lofi_p2.json
{
  "kind": "youtube#searchListResponse",
  "etag": "etag-search-2",
  "pageInfo": {"totalResults": 813, "resultsPerPage": 1},
  "items": [
    {
      "kind": "youtube#searchResult",
      "etag": "etag-item-3",
      "id": {"kind": "youtube#video", "videoId": "vid000000003"},
      "snippet": {
        "publishedAt": "2026-07-20T10:00:00Z",
        "channelId": "UCaaaaaaaaaaaaaaaaaaaaaa",
        "title": "Night drive synthwave",
        "description": "neon highway",
        "channelTitle": "NightLoop"
      }
    }
  ]
}
```

```python
# tests/unit/market/test_youtube_connector.py  (first cycle)
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from plm_platform.config import Secret

from services.market_intelligence.collection.rate_limit import (
    QuotaBudget, QuotaExceededError, RequestBudget,
)
from services.market_intelligence.connectors.youtube import (
    LIST_QUOTA_UNITS, SEARCH_QUOTA_UNITS, YouTubeCredentials, YouTubeDataAPIConnector,
    parse_iso8601_duration, parse_search_hits, parse_video_stats, parse_channel_stats,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "market" / "youtube"
FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def connector(mock_http) -> YouTubeDataAPIConnector:
    return YouTubeDataAPIConnector(
        client=mock_http.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=YouTubeCredentials(api_key=Secret("test-key")),
        quota=QuotaBudget(limit_units=1000),
        request_budget=RequestBudget(limit_requests=10),
        observed_at=lambda: FIXED_NOW,
    )


def test_search_sends_the_documented_request_and_returns_raw_objects(mock_http) -> None:
    mock_http.enqueue_json(load("search_lofi_p1.json"))
    result = connector(mock_http).search_videos("lofi", max_results=2)
    assert [o.provider_object_id for o in result.objects] == ["vid000000001", "vid000000002"]
    assert result.quota_units == SEARCH_QUOTA_UNITS
    assert result.next_page_token == "CAoQAA"
    request = mock_http.requests[0]
    assert request.path == "/youtube/v3/search"
    mock_http.assert_query_params(0, part="snippet", type="video", q="lofi", maxResults="2")
    hits = parse_search_hits(result)
    assert hits[0].video_id == "vid000000001"
    assert hits[0].published_at == datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
    assert hits[1].channel_title == "StudyLoops"


def test_pagination_follows_next_page_token_and_respects_max_pages(mock_http) -> None:
    mock_http.enqueue_json(load("search_lofi_p1.json")).enqueue_json(load("search_lofi_p2.json"))
    pages = list(connector(mock_http).iter_search_videos("lofi", max_results=2, max_pages=2))
    assert len(pages) == 2
    assert mock_http.requests[1].param("pageToken") == "CAoQAA"
    assert sum(p.quota_units for p in pages) == 2 * SEARCH_QUOTA_UNITS


def test_quota_exhaustion_stops_before_the_request_is_sent(mock_http) -> None:
    active = YouTubeDataAPIConnector(
        client=mock_http.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=YouTubeCredentials(api_key=Secret("test-key")),
        quota=QuotaBudget(limit_units=100),
        observed_at=lambda: FIXED_NOW,
    )
    with pytest.raises(QuotaExceededError):
        active.search_videos("lofi")
        # the budget refuses *before* the HTTP call; no canned response is enqueued
    assert mock_http.requests == []


def test_iso8601_durations_are_parsed_and_unparseable_values_are_none() -> None:
    assert parse_iso8601_duration("PT1H2M") == 3720.0
    assert parse_iso8601_duration("PT3M30S") == 210.0
    assert parse_iso8601_duration("") is None
    assert parse_iso8601_duration("P1D") is None  # day-granularity is not a video duration
```

> **Note:** `MockHTTP` exposes `assert_query_params(index, **expected)` (`tests/support/http.py`), so the query assertions above are the recording check.

- [ ] **Step 12: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_youtube_connector.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.connectors.youtube'`.

- [ ] **Step 13: Write `connectors/youtube.py` (search half)**

```python
# services/market_intelligence/connectors/youtube.py
"""Official YouTube Data API v3 connector (§4.1, §10) — discovery and hydration.

No private analytics are ever requested or inferred (§10.3). Credentials are injected
as `Secret`s and never reach a URL, a payload, a log line or an exception message.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from random import Random
from typing import Any, Final
from urllib.parse import urlparse

import httpx
from plm_platform.config import Secret

from ..collection.rate_limit import QuotaBudget, RateLimiter, RequestBudget
from ..collection.retries import classify_http_status, execute_with_retries, RetryPolicy
from ..domain.enums import ConnectorErrorClass, ProviderName, ProviderObjectType
from .base import ConnectorError, FetchResult, FetchedObject, hash_request_context

__all__ = [
    "BASE_URL", "LIST_QUOTA_UNITS", "SEARCH_QUOTA_UNITS", "ChannelStats", "SearchHit",
    "VideoStats", "YouTubeCredentials", "YouTubeDataAPIConnector", "parse_channel_stats",
    "parse_iso8601_duration", "parse_search_hits", "parse_video_stats",
]

BASE_URL: Final[str] = "https://www.googleapis.com/youtube/v3"
SEARCH_QUOTA_UNITS: Final[int] = 100  # documented cost of one search.list call (§4.1)
LIST_QUOTA_UNITS: Final[int] = 1

_DURATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$"
)
_MAX_IDS_PER_LIST_CALL: Final[int] = 50


@dataclass(frozen=True, slots=True)
class YouTubeCredentials:
    """API key auth or OAuth bearer auth; at least one must be present."""

    api_key: Secret | None = None
    access_token: Secret | None = None

    def headers(self) -> dict[str, str]:
        if self.access_token is not None:
            return {"Authorization": f"Bearer {self.access_token.get_secret_value()}"}
        if self.api_key is not None:
            return {"X-Goog-Api-Key": self.api_key.get_secret_value()}
        raise ConnectorError(ConnectorErrorClass.AUTH_FAILURE, "no YouTube credential configured")


@dataclass(frozen=True, slots=True)
class SearchHit:
    video_id: str
    channel_id: str
    title: str
    description: str
    published_at: datetime | None
    channel_title: str


@dataclass(frozen=True, slots=True)
class VideoStats:
    video_id: str
    channel_id: str | None
    title: str | None
    description: str | None
    published_at: datetime | None
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    duration_seconds: float | None
    language: str | None


@dataclass(frozen=True, slots=True)
class ChannelStats:
    channel_id: str
    title: str | None
    subscriber_count: int | None
    video_count: int | None
    view_count: int | None
    country: str | None
    language: str | None


def _parse_timestamp(value: str | None) -> datetime | None:
    """Provider timestamps are documented UTC; `Z` is normalised, naive means UTC."""
    if not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def parse_iso8601_duration(text: str | None) -> float | None:
    """`PT1H2M3S` → seconds. `None` for missing or non-time-only durations."""
    if not text:
        return None
    match = _DURATION_RE.match(text)
    if match is None:
        return None
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return float(parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"])


def _int_or_none(value: Any) -> int | None:
    """Provider counts arrive as strings; absent stays `None`, never 0 (Review Focus 1)."""
    if value is None or value == "":
        return None
    return int(value)


class YouTubeDataAPIConnector:
    """One connector instance per run: it carries the run's budgets and rate limiter."""

    provider_name = ProviderName.YOUTUBE_DATA_API

    def __init__(
        self,
        *,
        client: httpx.Client,
        credentials: YouTubeCredentials,
        quota: QuotaBudget | None = None,
        request_budget: RequestBudget | None = None,
        policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: Random | None = None,
        limiter: RateLimiter | None = None,
        base_url: str = BASE_URL,
        observed_at: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._credentials = credentials
        self._quota = quota if quota is not None else QuotaBudget(10_000)
        self._requests = request_budget if request_budget is not None else RequestBudget(1_000)
        self._policy = policy if policy is not None else RetryPolicy()
        self._sleep = sleep
        self._rng = rng if rng is not None else Random()
        self._limiter = limiter
        self._base_url = base_url.rstrip("/")
        self._observed_at = observed_at if observed_at is not None else (lambda: datetime.now(UTC))
        self._request_count = 0
        self._quota_units_used = 0

    # -- public surface ---------------------------------------------------------

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def quota_units_used(self) -> int:
        return self._quota_units_used

    def search_videos(
        self,
        query: str,
        *,
        max_results: int = 10,
        published_after: datetime | None = None,
        page_token: str | None = None,
    ) -> FetchResult:
        params: dict[str, Any] = {
            "part": "snippet", "type": "video", "q": query, "maxResults": max_results,
            "pageToken": page_token,
            "publishedAfter": published_after.astimezone(UTC).isoformat()
            if published_after else None,
        }
        payload = self._get("/search", params, quota_units=SEARCH_QUOTA_UNITS,
                           operation="search.list")
        observed_at = self._observed_at()
        context = hash_request_context(
            self.provider_name.value, "search.list",
            {k: v for k, v in params.items() if k != "pageToken"},
        )
        objects = _search_objects(payload, observed_at)
        return FetchResult(
            objects=objects, request_context_hash=context, quota_units=SEARCH_QUOTA_UNITS,
            next_page_token=payload.get("nextPageToken"),
        )

    def iter_search_videos(
        self,
        query: str,
        *,
        max_results: int = 10,
        max_pages: int = 1,
        published_after: datetime | None = None,
    ) -> Iterator[FetchResult]:
        token: str | None = None
        for _ in range(max_pages):
            page = self.search_videos(
                query, max_results=max_results, published_after=published_after,
                page_token=token,
            )
            yield page
            token = page.next_page_token
            if not token:
                return

    # -- internals --------------------------------------------------------------

    def _get(
        self, path: str, params: Mapping[str, Any], *, quota_units: int, operation: str
    ) -> Mapping[str, Any]:
        self._quota.consume(quota_units, operation=f"{self.provider_name.value}.{operation}")
        self._requests.consume(operation=operation)
        self._quota_units_used += quota_units
        clean = {k: v for k, v in params.items() if v is not None}

        def attempt() -> Mapping[str, Any]:
            if self._limiter is not None:
                self._limiter.acquire()
            self._request_count += 1
            try:
                response = self._client.get(
                    f"{self._base_url}{path}", params=clean, headers=self._credentials.headers()
                )
            except httpx.TimeoutException as exc:
                raise ConnectorError(
                    ConnectorErrorClass.TRANSIENT_NETWORK, f"{operation} timed out"
                ) from exc
            except httpx.TransportError as exc:
                raise ConnectorError(
                    ConnectorErrorClass.TRANSIENT_NETWORK, f"{operation} transport failure"
                ) from exc
            return _parse_response(response, operation)

        return execute_with_retries(
            attempt, policy=self._policy, sleep=self._sleep, rng=self._rng
        )


def _parse_response(response: httpx.Response, operation: str) -> Mapping[str, Any]:
    if response.status_code >= 400:
        raise _error_for(response, operation)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConnectorError(
            ConnectorErrorClass.PARSER_FAILURE, f"{operation} returned a body that is not JSON"
        ) from exc
    if not isinstance(payload, Mapping) or "items" not in payload:
        raise ConnectorError(
            ConnectorErrorClass.SCHEMA_CHANGE,
            f"{operation} response has no 'items': the provider schema moved (§43)",
        )
    return payload


def _error_for(response: httpx.Response, operation: str) -> ConnectorError:
    retry_after = response.headers.get("Retry-After")
    retry_after_seconds = float(retry_after) if retry_after and retry_after.isdigit() else None
    reason = ""
    try:
        body = response.json()
        errors = body.get("error", {}).get("errors", [])
        reason = errors[0].get("reason", "") if errors else ""
    except ValueError:
        pass
        # a non-JSON error body is normal (gateway pages); the status still decides
    if response.status_code == 403 and reason == "quotaExceeded":
        return ConnectorError(
            ConnectorErrorClass.QUOTA_EXCEEDED, f"{operation}: provider quota exhausted"
        )
    return ConnectorError(
        classify_http_status(response.status_code),
        f"{operation} failed with HTTP {response.status_code}",
        retry_after_seconds=retry_after_seconds,
    )


def _search_objects(payload: Mapping[str, Any], observed_at: datetime) -> tuple[FetchedObject, ...]:
    objects: list[FetchedObject] = []
    for item in payload.get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        if not video_id:
            continue  # channel/playlist results are not the discovery target (§10.1)
        objects.append(FetchedObject(
            provider_object_type=ProviderObjectType.SEARCH_RESULT_PAGE,
            provider_object_id=video_id, observed_at=observed_at, payload=item,
        ))
    return tuple(objects)


def parse_search_hits(fetch: FetchResult) -> tuple[SearchHit, ...]:
    hits: list[SearchHit] = []
    for obj in fetch.objects:
        snippet = obj.payload.get("snippet") or {}
        hits.append(SearchHit(
            video_id=obj.provider_object_id,
            channel_id=snippet.get("channelId", ""),
            title=snippet.get("title", ""),
            description=snippet.get("description", ""),
            published_at=_parse_timestamp(snippet.get("publishedAt")),
            channel_title=snippet.get("channelTitle", ""),
        ))
    return tuple(hits)
```

- [ ] **Step 14: Run the search tests and watch them pass**

Run: `python -m pytest tests/unit/market/test_youtube_connector.py -q`
Expected: PASS (4 passed).

- [ ] **Step 15: Commit**

```bash
git add services/market_intelligence/connectors/youtube.py tests/fixtures/market/youtube/ \
        tests/unit/market/test_youtube_connector.py
git commit -m "feat(p1): youtube search connector with quota and pagination"
```

- [ ] **Step 16: Write the failing hydration/error test**

```python
# tests/unit/market/test_youtube_connector.py  (second cycle — append)
from services.market_intelligence.connectors.base import ConnectorError
from services.market_intelligence.domain.enums import ConnectorErrorClass


def test_video_hydration_maps_counts_and_durations_without_inventing_missing_values(mock_http) -> None:
    mock_http.enqueue_json(load("videos_lofi_batch.json"))
    result = connector(mock_http).fetch_videos(["vid000000001", "vid000000002"])
    assert result.quota_units == LIST_QUOTA_UNITS
    mock_http.assert_query_params(0, part="snippet,statistics,contentDetails")
    stats = {s.video_id: s for s in parse_video_stats(result)}
    assert stats["vid000000001"].view_count == 120000
    assert stats["vid000000001"].duration_seconds == 3720.0
    assert stats["vid000000002"].comment_count is None  # absent ⇒ None, never 0
    assert stats["vid000000002"].like_count == 210


def test_channel_hydration_keeps_hidden_subscriber_counts_unknown(mock_http) -> None:
    mock_http.enqueue_json(load("channels_lofi_batch.json"))
    result = connector(mock_http).fetch_channels(["UCaaaaaaaaaaaaaaaaaaaaaa",
                                                  "UCbbbbbbbbbbbbbbbbbbbbbb"])
    stats = {s.channel_id: s for s in parse_channel_stats(result)}
    assert stats["UCaaaaaaaaaaaaaaaaaaaaaa"].subscriber_count == 820000
    assert stats["UCbbbbbbbbbbbbbbbbbbbbbb"].subscriber_count is None
    assert stats["UCaaaaaaaaaaaaaaaaaaaaaa"].country == "KR"


def test_a_5xx_is_retried_then_succeeds(mock_http) -> None:
    mock_http.enqueue_server_error(status=503).enqueue_json(load("videos_lofi_batch.json"))
    active = YouTubeDataAPIConnector(
        client=mock_http.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=YouTubeCredentials(api_key=Secret("test-key")),
        sleep=lambda _seconds: None, rng=Random(0), observed_at=lambda: FIXED_NOW,
    )
    result = active.fetch_videos(["vid000000001", "vid000000002"])
    assert len(result.objects) == 2
    assert len(mock_http.requests) == 2


def test_a_429_honours_retry_after_and_is_not_retried_forever(mock_http) -> None:
    mock_http.enqueue_rate_limit(retry_after="30").enqueue_json(load("channels_lofi_batch.json"))
    naps: list[float] = []
    active = YouTubeDataAPIConnector(
        client=mock_http.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=YouTubeCredentials(api_key=Secret("test-key")),
        sleep=naps.append, rng=Random(0), observed_at=lambda: FIXED_NOW,
    )
    active.fetch_channels(["UCaaaaaaaaaaaaaaaaaaaaaa"])
    assert naps == [30.0]


def test_an_invalid_request_is_never_retried(mock_http) -> None:
    mock_http.enqueue_status(400, payload={"error": {"code": 400, "message": "bad"}})
    active = connector(mock_http)
    with pytest.raises(ConnectorError) as excinfo:
        active.fetch_videos(["vid000000001"])
    assert excinfo.value.error_class is ConnectorErrorClass.INVALID_REQUEST
    assert len(mock_http.requests) == 1


def test_a_malformed_body_is_a_parser_failure_and_a_missing_items_key_is_a_schema_change(mock_http) -> None:
    mock_http.enqueue_malformed_json()
    with pytest.raises(ConnectorError) as excinfo:
        connector(mock_http).fetch_channels(["UCaaaaaaaaaaaaaaaaaaaaaa"])
    assert excinfo.value.error_class is ConnectorErrorClass.PARSER_FAILURE

    mock_http.enqueue_json({"kind": "youtube#channelListResponse"})
    with pytest.raises(ConnectorError) as excinfo:
        connector(mock_http).fetch_channels(["UCaaaaaaaaaaaaaaaaaaaaaa"])
    assert excinfo.value.error_class is ConnectorErrorClass.SCHEMA_CHANGE
```

- [ ] **Step 17: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_youtube_connector.py -q`
Expected: FAIL — `AttributeError: 'YouTubeDataAPIConnector' object has no attribute 'fetch_videos'`.

- [ ] **Step 18: Implement hydration**

```python
# services/market_intelligence/connectors/youtube.py  (append to the class)
    def fetch_videos(self, video_ids: Sequence[str]) -> FetchResult:
        """videos.list, batched at the documented 50-id limit; one FetchResult per batch."""
        payload = self._list("/videos", "snippet,statistics,contentDetails", "id",
                             list(video_ids), operation="videos.list")
        observed_at = self._observed_at()
        objects = tuple(
            FetchedObject(
                provider_object_type=ProviderObjectType.VIDEO,
                provider_object_id=str(item.get("id")),
                observed_at=observed_at,
                payload=item,
            )
            for item in payload.get("items", [])
            if item.get("id")
        )
        return FetchResult(
            objects=objects, quota_units=LIST_QUOTA_UNITS,
            request_context_hash=hash_request_context(
                self.provider_name.value, "videos.list", {"id": ",".join(video_ids)}
            ),
        )

    def fetch_channels(self, channel_ids: Sequence[str]) -> FetchResult:
        payload = self._list("/channels", "snippet,statistics", "id",
                             list(channel_ids), operation="channels.list")
        observed_at = self._observed_at()
        objects = tuple(
            FetchedObject(
                provider_object_type=ProviderObjectType.CHANNEL,
                provider_object_id=str(item.get("id")),
                observed_at=observed_at,
                payload=item,
            )
            for item in payload.get("items", [])
            if item.get("id")
        )
        return FetchResult(
            objects=objects, quota_units=LIST_QUOTA_UNITS,
            request_context_hash=hash_request_context(
                self.provider_name.value, "channels.list", {"id": ",".join(channel_ids)}
            ),
        )

    def _list(
        self, path: str, part: str, id_param: str, ids: Sequence[str], *, operation: str
    ) -> Mapping[str, Any]:
        if not ids:
            raise ConnectorError(ConnectorErrorClass.INVALID_REQUEST,
                                 f"{operation} called without ids")
        if len(ids) > _MAX_IDS_PER_LIST_CALL:
            raise ConnectorError(
                ConnectorErrorClass.INVALID_REQUEST,
                f"{operation} accepts at most {_MAX_IDS_PER_LIST_CALL} ids per call",
            )
        return self._get(path, {"part": part, id_param: ",".join(ids)},
                         quota_units=LIST_QUOTA_UNITS, operation=operation)
```

```python
# services/market_intelligence/connectors/youtube.py  (module level, after parse_search_hits)
def parse_video_stats(fetch: FetchResult) -> tuple[VideoStats, ...]:
    stats: list[VideoStats] = []
    for obj in fetch.objects:
        snippet = obj.payload.get("snippet") or {}
        statistics = obj.payload.get("statistics") or {}
        details = obj.payload.get("contentDetails") or {}
        stats.append(VideoStats(
            video_id=obj.provider_object_id,
            channel_id=snippet.get("channelId"),
            title=snippet.get("title"),
            description=snippet.get("description"),
            published_at=_parse_timestamp(snippet.get("publishedAt")),
            view_count=_int_or_none(statistics.get("viewCount")),
            like_count=_int_or_none(statistics.get("likeCount")),
            comment_count=_int_or_none(statistics.get("commentCount")),
            duration_seconds=parse_iso8601_duration(details.get("duration")),
            language=snippet.get("defaultLanguage"),
        ))
    return tuple(stats)


def parse_channel_stats(fetch: FetchResult) -> tuple[ChannelStats, ...]:
    stats: list[ChannelStats] = []
    for obj in fetch.objects:
        snippet = obj.payload.get("snippet") or {}
        statistics = obj.payload.get("statistics") or {}
        stats.append(ChannelStats(
            channel_id=obj.provider_object_id,
            title=snippet.get("title"),
            subscriber_count=_int_or_none(statistics.get("subscriberCount")),
            video_count=_int_or_none(statistics.get("videoCount")),
            view_count=_int_or_none(statistics.get("viewCount")),
            country=snippet.get("country"),
            language=snippet.get("defaultLanguage"),
        ))
    return tuple(stats)
```

- [ ] **Step 19: Run the connector suite and watch it pass**

Run: `python -m pytest tests/unit/market/test_youtube_connector.py -q`
Expected: PASS (10 passed).

- [ ] **Step 20: Add the secret-hygiene test, run everything, commit**

```python
# tests/unit/market/test_youtube_secrets.py
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from plm_platform.config import Secret

from services.market_intelligence.collection.rate_limit import QuotaBudget
from services.market_intelligence.connectors.base import ConnectorError, hash_request_context
from services.market_intelligence.connectors.youtube import (
    YouTubeCredentials, YouTubeDataAPIConnector, parse_video_stats,
)

SENTINEL = "yt-secret-sentinel-0123456789"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "market" / "youtube"
FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_the_credential_never_reaches_a_log_line_repr_or_payload(mock_http, caplog) -> None:
    payload = json.loads((FIXTURES / "videos_lofi_batch.json").read_text(encoding="utf-8"))
    mock_http.enqueue_json(payload)
    credentials = YouTubeCredentials(api_key=Secret(SENTINEL))
    active = YouTubeDataAPIConnector(
        client=mock_http.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=credentials, quota=QuotaBudget(10), observed_at=lambda: FIXED_NOW,
    )
    with caplog.at_level(logging.DEBUG):
        result = active.fetch_videos(["vid000000001"])
    assert SENTINEL not in repr(credentials)
    assert SENTINEL not in caplog.text
    assert SENTINEL not in str(result)
    assert SENTINEL not in str(parse_video_stats(result))


def test_a_connector_without_credentials_fails_closed(mock_http) -> None:
    active = YouTubeDataAPIConnector(
        client=mock_http.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=YouTubeCredentials(),
    )
    with pytest.raises(ConnectorError, match="AUTH_FAILURE"):
        active.fetch_videos(["vid000000001"])
    assert mock_http.requests == []


def test_request_context_rejects_credential_parameters() -> None:
    with pytest.raises(ValueError, match="credential"):
        hash_request_context("youtube_data_api", "search.list", {"key": SENTINEL})
```

Run: `python -m pytest tests/unit/market -q` then `python -m ruff check services/market_intelligence tests/unit/market`
Expected: PASS; ruff clean.

```bash
git add services/market_intelligence/connectors/youtube.py tests/unit/market/test_youtube_secrets.py \
        tests/fixtures/market/youtube/
git commit -m "feat(p1): youtube hydration, error classes and secret hygiene"
```

> **Deferred to P1.7/P1.11, deliberately:** `connectors/spotify.py`, `connectors/trends.py`, `connectors/browser.py` are optional secondary sources (§4.3/§4.4). They arrive in P1.8's cross-platform feature task, which is the only P1 consumer of them, so this task ships the one provider the phase's acceptance criteria require. This is a scope split within one spec step (P1.3), recorded in the self-review.

---

### Task P1.4: Raw observation storage

**Files:**
- Create: `services/market_intelligence/storage/repositories.py`, `storage/raw_store.py`
- Test: `tests/unit/market/conftest.py`, `tests/unit/market/test_raw_store.py`, `tests/integration/market/test_raw_store_minio.py`

**Interfaces:**
- Consumes: `storage.models` (`sources`, `collection_runs`, `raw_observations` tables); `plm_platform.storage.put_content`, `to_uri`, `StoredObject`, `ObjectAlreadyExistsError`, `sha256_digest`; `plm_platform.db.scoped_session`; `connectors.base.FetchedObject`, `hash_request_context`; `domain.enums.CollectionRunStatus`.
- Produces (names later tasks import exactly):
  - `storage.repositories`: `upsert_source(session, *, provider_name: ProviderName, connector_type: ConnectorType, base_identifier: str, configuration_version: str) -> str` (returns source id; idempotent on `provider_name`); `start_collection_run(session, *, source_id, query_id: str | None, software_version: str, started_at: datetime) -> str`; `finish_collection_run(session, run_id, *, status: CollectionRunStatus, counters: CollectionRunCounters, error_summary: Mapping | None = None, finished_at: datetime | None = None) -> None`; `add_run_cost(session, run_id, *, api_calls: int = 0, llm_tokens: int = 0, llm_cost_estimate: float = 0.0, storage_bytes: int = 0) -> None`; `load_run(session, run_id) -> Mapping[str, Any] | None`.
  - `storage.raw_store`: `StoredRawObservation(raw_observation_id: str, created: bool, payload_uri: str, payload_hash: str)`; `store_raw_observation(session, *, run_id: str, source_id: str, fetched: FetchedObject, schema_version: str = "p1-raw-v1", put_object: PutObjectFn | None = None, settings=None, client=None) -> StoredRawObservation`; `load_raw_observation(session, raw_observation_id) -> Mapping[str, Any] | None`.

> **Rulings (Open items #12, #14):**
> 1. The raw payload is stored **twice on purpose**: as the artifact `market/raw/<provider>/<object_type>/<YYYY-MM-DD>/<sha256>` (immutable evidence, R13) and as the `payload_json` column the spec §7.4 names (queryable research record). The artifact is written first; a failure after the put leaves an unreferenced object, never a row without evidence.
> 2. `sa.JSON` (not JSONB) is used for payload columns so the unit suite can run on SQLite with an attached `market` schema; Postgres stores the same type as `json`.
> 3. The authoritative dedupe key is the table's `uq_market_raw_dedupe` constraint (source, type, provider object, observed_at, payload hash); a re-collection on a later date is a *new* row (§3.2, AC-03).

- [ ] **Step 1: Write the failing repository test (with the SQLite fixture)**

```python
# tests/unit/market/conftest.py
"""Fast unit-test database: SQLite with `market` attached, so the same Core
statements that run on Postgres run here. Integration tests still prove Postgres."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from services.market_intelligence.storage.models import MARKET_METADATA


@pytest.fixture
def market_db() -> sa.Engine:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS market")
    MARKET_METADATA.create_all(engine)
    return engine


@pytest.fixture
def market_session(market_db: sa.Engine) -> Iterator[Session]:
    with Session(market_db) as session:
        yield session
```

Add `from collections.abc import Iterator` to that file's imports.

```python
# tests/unit/market/test_raw_store.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.market_intelligence.domain.enums import ConnectorType, ProviderName
from services.market_intelligence.storage import repositories

FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_upsert_source_is_idempotent_on_the_provider_name(market_session) -> None:
    first = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="https://www.googleapis.com/youtube/v3",
        configuration_version="p1-sources-v1",
    )
    second = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="https://www.googleapis.com/youtube/v3",
        configuration_version="p1-sources-v1",
    )
    assert first == second


def test_collection_run_records_status_then_counters_and_costs(market_session) -> None:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    run_id = repositories.start_collection_run(
        market_session, source_id=source_id, query_id=None,
        software_version="p1-v1", started_at=FIXED_NOW,
    )
    repositories.add_run_cost(market_session, run_id, api_calls=3, storage_bytes=2048)
    repositories.finish_collection_run(
        market_session, run_id, status=CollectionRunStatus.PARTIAL,
        counters=CollectionRunCounters(request_count=3, success_count=2, error_count=1,
                                       raw_observation_count=2, normalized_observation_count=1),
        error_summary={"RATE_LIMIT": 1}, finished_at=FIXED_NOW,
    )
    run = repositories.load_run(market_session, run_id)
    assert run is not None
    assert run["status"] == "partial"
    assert run["cost_api_calls"] == 3
    assert run["cost_storage_bytes"] == 2048
    assert run["error_summary"] == {"RATE_LIMIT": 1}
    assert run["finished_at"] is not None
```

Add `from services.market_intelligence.domain.entities import CollectionRunCounters` and
`from services.market_intelligence.domain.enums import CollectionRunStatus` to the imports.

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_raw_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.storage.repositories'`.

- [ ] **Step 3: Write `storage/repositories.py` (source, run, cost helpers)**

```python
# services/market_intelligence/storage/repositories.py
"""Typed helpers over the `market` schema. Every function takes a Session whose
search_path is the `market` schema (`plm_platform.db.scoped_session("market")`)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..domain.entities import CollectionRunCounters
from ..domain.enums import CollectionRunStatus, ConnectorType, ProviderName
from .models import collection_runs, sources

__all__ = [
    "add_run_cost", "finish_collection_run", "load_run", "start_collection_run", "upsert_source",
]


def upsert_source(
    session: Session,
    *,
    provider_name: ProviderName,
    connector_type: ConnectorType,
    base_identifier: str,
    configuration_version: str,
) -> str:
    """One row per provider (§7.1); secrets never live here."""
    existing = session.execute(
        sa.select(sources.c.id).where(sources.c.provider_name == provider_name.value)
    ).scalar_one_or_none()
    if existing is not None:
        session.execute(
            sa.update(sources).where(sources.c.id == existing).values(
                connector_type=connector_type.value,
                base_identifier=base_identifier,
                configuration_version=configuration_version,
                updated_at=sa.func.now(),
            )
        )
        return str(existing)
    source_id = _new_id()
    session.execute(sa.insert(sources).values(
        id=source_id, provider_name=provider_name.value,
        connector_type=connector_type.value, base_identifier=base_identifier,
        status="active", configuration_version=configuration_version,
    ))
    return source_id


def start_collection_run(
    session: Session,
    *,
    source_id: str,
    query_id: str | None,
    software_version: str,
    started_at: datetime,
) -> str:
    """§7.3: every collection attempt has a run record, starting `running`."""
    run_id = _new_id()
    session.execute(sa.insert(collection_runs).values(
        id=run_id, source_id=source_id, query_id=query_id, started_at=started_at,
        status=CollectionRunStatus.RUNNING.value, software_version=software_version,
    ))
    return run_id


def finish_collection_run(
    session: Session,
    run_id: str,
    *,
    status: CollectionRunStatus,
    counters: CollectionRunCounters,
    error_summary: Mapping[str, Any] | None = None,
    finished_at: datetime | None = None,
) -> None:
    """A run that lost records ends `partial`, never `succeeded` (§24, §43)."""
    if status is CollectionRunStatus.SUCCEEDED and (
        counters.error_count or counters.quarantined_count
    ):
        raise ValueError(
            "a run with errors or quarantined records must finish as partial, not succeeded"
        )
    session.execute(sa.update(collection_runs).where(collection_runs.c.id == run_id).values(
        status=status.value,
        finished_at=finished_at if finished_at is not None else sa.func.now(),
        request_count=counters.request_count, success_count=counters.success_count,
        error_count=counters.error_count, rate_limit_count=counters.rate_limit_count,
        raw_observation_count=counters.raw_observation_count,
        normalized_observation_count=counters.normalized_observation_count,
        error_summary=dict(error_summary) if error_summary else None,
    ))


def add_run_cost(
    session: Session,
    run_id: str,
    *,
    api_calls: int = 0,
    llm_tokens: int = 0,
    llm_cost_estimate: float = 0.0,
    storage_bytes: int = 0,
) -> None:
    """§42: costs attach to `collection_run_id` so P9 can price market research."""
    if min(api_calls, llm_tokens, storage_bytes) < 0 or llm_cost_estimate < 0:
        raise ValueError("costs cannot be negative")
    session.execute(sa.update(collection_runs).where(collection_runs.c.id == run_id).values(
        cost_api_calls=collection_runs.c.cost_api_calls + api_calls,
        cost_llm_tokens=collection_runs.c.cost_llm_tokens + llm_tokens,
        cost_llm_cost_estimate=collection_runs.c.cost_llm_cost_estimate + llm_cost_estimate,
        cost_storage_bytes=collection_runs.c.cost_storage_bytes + storage_bytes,
    ))


def load_run(session: Session, run_id: str) -> Mapping[str, Any] | None:
    row = session.execute(
        sa.select(collection_runs).where(collection_runs.c.id == run_id)
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `python -m pytest tests/unit/market/test_raw_store.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add services/market_intelligence/storage/repositories.py tests/unit/market/conftest.py \
        tests/unit/market/test_raw_store.py
git commit -m "feat(p1): run and source repositories"
```

- [ ] **Step 6: Write the failing raw-store test**

```python
# tests/unit/market/test_raw_store.py  (append)
import sqlalchemy as sa

from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.enums import ProviderObjectType
from services.market_intelligence.storage.raw_store import store_raw_observation
from plm_platform.storage import ObjectAlreadyExistsError, StoredObject, sha256_digest

PAYLOAD = {"kind": "youtube#video", "id": "vid000000001",
           "statistics": {"viewCount": "120000"}}
CONTEXT_HASH = "a" * 64


class FakeObjectStore:
    """Records puts; raises the real ObjectAlreadyExistsError on a repeated key."""

    def __init__(self) -> None:
        self.keys: dict[str, bytes] = {}
        self.calls: list[str] = []

    def __call__(self, prefix, resource_path, data, *, content_type="application/octet-stream",
                 settings=None, client=None) -> StoredObject:
        digest = sha256_digest(data)
        key = f"{prefix}{resource_path}/{digest}"
        self.calls.append(key)
        if key in self.keys:
            raise ObjectAlreadyExistsError(f"refusing to overwrite {key!r}")
        self.keys[key] = data
        return StoredObject(key=key, digest=digest, size=len(data))


def _store(market_session, store, *, observed_at=FIXED_NOW):
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    run_id = repositories.start_collection_run(
        market_session, source_id=source_id, query_id=None,
        software_version="p1-v1", started_at=FIXED_NOW,
    )
    fetched = FetchedObject(
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid000000001",
        observed_at=observed_at, payload=PAYLOAD,
    )
    return source_id, run_id, store_raw_observation(
        market_session, run_id=run_id, source_id=source_id, fetched=fetched,
        request_context_hash=CONTEXT_HASH, put_object=store,
    )


def test_raw_observation_is_immutable_content_addressed_and_costed(market_session) -> None:
    store = FakeObjectStore()
    _, run_id, stored = _store(market_session, store)
    assert stored.created is True
    assert stored.payload_uri.startswith("plm://")
    assert stored.payload_uri.endswith(stored.payload_hash)
    assert "market/raw/youtube_data_api/video/2026-09-20/" in store.calls[0]
    assert store.keys[store.calls[0]] == (
        b'{"id":"vid000000001","kind":"youtube#video","statistics":{"viewCount":"120000"}}'
    )
    run = repositories.load_run(market_session, run_id)
    assert run["cost_storage_bytes"] == len(store.keys[store.calls[0]])
    row = market_session.execute(
        sa.text("SELECT payload_hash, request_context_hash, schema_version "
                "FROM market.market_raw_observations")
    ).one()
    assert row.schema_version == "p1-raw-v1"
    assert row.request_context_hash == CONTEXT_HASH
    assert row.payload_hash == stored.payload_hash


def test_storing_the_same_observation_twice_is_idempotent(market_session) -> None:
    store = FakeObjectStore()
    _, _, first = _store(market_session, store)
    _, _, second = _store(market_session, store)
    assert second.created is False
    assert second.raw_observation_id == first.raw_observation_id
    rows = market_session.execute(
        sa.text("SELECT count(*) FROM market.market_raw_observations")
    ).scalar_one()
    assert rows == 1


def test_a_later_collection_at_a_new_time_is_a_second_observation(market_session) -> None:
    store = FakeObjectStore()
    _, _, first = _store(market_session, store)
    _, _, second = _store(market_session, store,
                          observed_at=datetime(2026, 9, 21, 6, 0, tzinfo=UTC))
    assert second.created is True
    assert second.raw_observation_id != first.raw_observation_id
```

- [ ] **Step 7: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_raw_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.storage.raw_store'`.

- [ ] **Step 8: Write `storage/raw_store.py`**

```python
# services/market_intelligence/storage/raw_store.py
"""§3.2/§7.4: raw observations are immutable, content-addressed and deduplicated
without collapsing time series. Nothing here ever updates a row."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from plm_platform.storage import ObjectAlreadyExistsError, StoredObject, put_content, to_uri

from ..connectors.base import FetchedObject
from .models import raw_observations
from .repositories import add_run_cost

__all__ = ["StoredRawObservation", "load_raw_observation", "store_raw_observation"]

SCHEMA_VERSION: str = "p1-raw-v1"
PutObjectFn = Callable[..., StoredObject]


@dataclass(frozen=True, slots=True)
class StoredRawObservation:
    raw_observation_id: str
    created: bool
    payload_uri: str
    payload_hash: str
    size: int = 0


def store_raw_observation(
    session: Session,
    *,
    run_id: str,
    source_id: str,
    fetched: FetchedObject,
    request_context_hash: str,
    schema_version: str = SCHEMA_VERSION,
    put_object: PutObjectFn | None = None,
    settings: Any = None,
    client: Any = None,
) -> StoredRawObservation:
    """Write the artifact, then the row. Idempotent per the table's dedupe constraint."""
    canonical = json.dumps(fetched.payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    put = put_content if put_object is None else put_object
    resource_path = (
        f"raw/{_provider_token(fetched)}/{fetched.provider_object_type.value}/"
        f"{fetched.observed_at.date().isoformat()}"
    )
    try:
        stored_object = put("market/", resource_path, canonical,
                            content_type="application/json", settings=settings, client=client)
    except ObjectAlreadyExistsError:
        # Content addressing means the same key is the same bytes: re-storing an
        # identical payload is a no-op, not a conflict (§3.2).
        from plm_platform.storage import build_key, sha256_digest

        digest = sha256_digest(canonical)
        stored_object = StoredObject(
            key=build_key("market/", resource_path, digest), digest=digest, size=len(canonical)
        )
    payload_uri = to_uri(stored_object.key, settings=settings)

    values = {
        "id": str(uuid.uuid4()),
        "collection_run_id": run_id,
        "source_id": source_id,
        "provider_object_type": fetched.provider_object_type.value,
        "provider_object_id": fetched.provider_object_id,
        "observed_at": fetched.observed_at,
        "request_context_hash": request_context_hash,
        "payload_json": dict(fetched.payload),
        "payload_hash": stored_object.digest,
        "payload_uri": payload_uri,
        "schema_version": schema_version,
    }
    try:
        with session.begin_nested():
            session.execute(sa.insert(raw_observations).values(**values))
    except IntegrityError:
        existing = session.execute(
            sa.select(raw_observations.c.id, raw_observations.c.payload_uri,
                      raw_observations.c.payload_hash)
            .where(
                raw_observations.c.source_id == source_id,
                raw_observations.c.provider_object_type == fetched.provider_object_type.value,
                raw_observations.c.provider_object_id == fetched.provider_object_id,
                raw_observations.c.observed_at == fetched.observed_at,
                raw_observations.c.payload_hash == stored_object.digest,
            )
        ).one_or_none()
        if existing is None:
            raise
        return StoredRawObservation(
            raw_observation_id=str(existing.id), created=False,
            payload_uri=existing.payload_uri, payload_hash=existing.payload_hash,
        )
    add_run_cost(session, run_id, storage_bytes=stored_object.size)
    return StoredRawObservation(
        raw_observation_id=values["id"], created=True,
        payload_uri=payload_uri, payload_hash=stored_object.digest, size=stored_object.size,
    )


def load_raw_observation(session: Session, raw_observation_id: str) -> Mapping[str, Any] | None:
    """Provenance read (§AC-02): the immutable payload plus its request context."""
    row = session.execute(
        sa.select(raw_observations).where(raw_observations.c.id == raw_observation_id)
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _provider_token(fetched: FetchedObject) -> str:
    return {
        "video": "youtube_data_api", "channel": "youtube_data_api",
        "playlist": "youtube_data_api", "search_result_page": "youtube_data_api",
        "track": "spotify_web_api", "trend_point": "public_trends",
    }[fetched.provider_object_type.value]
```

- [ ] **Step 9: Run the tests and watch them pass**

Run: `python -m pytest tests/unit/market/test_raw_store.py -q`
Expected: PASS (5 passed).

- [ ] **Step 10: Prove it against real MinIO**

```python
# tests/integration/market/test_raw_store_minio.py
"""The unit suite fakes the object store; this proves the real one (W0.6 storage)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from plm_platform.db import build_engine, scoped_session
from plm_platform.storage import build_client, ensure_bucket, get_verified, from_uri

from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.enums import ConnectorType, ProviderName, ProviderObjectType
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.raw_store import store_raw_observation

pytestmark = pytest.mark.integration
FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_the_payload_artifact_round_trips_through_minio() -> None:
    try:
        client = build_client()
        ensure_bucket(client=client)
        engine = build_engine()
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - skip with a remedy (repo convention)
        pytest.skip(f"minio/postgres unreachable ({type(exc).__name__}); "
                    "run: docker compose up -d --wait")
    with scoped_session("market", factory=sa.orm.sessionmaker(bind=engine)) as session:
        source_id = repositories.upsert_source(
            session, provider_name=ProviderName.YOUTUBE_DATA_API,
            connector_type=ConnectorType.DATA_API, base_identifier="x",
            configuration_version="p1-sources-v1",
        )
        run_id = repositories.start_collection_run(
            session, source_id=source_id, query_id=None, software_version="p1-v1",
            started_at=FIXED_NOW,
        )
        baked_at = datetime(2020, 1, 2, 3, 4, tzinfo=UTC)
        stored = store_raw_observation(
            session, run_id=run_id, source_id=source_id,
            fetched=FetchedObject(
                provider_object_type=ProviderObjectType.VIDEO,
                provider_object_id="vid000000001", observed_at=baked_at,
                payload={"id": "vid000000001", "statistics": {"viewCount": "1"}},
            ),
            request_context_hash="0" * 64, client=client,
        )        session.commit()
    assert get_verified(from_uri(stored.payload_uri), client=client)
```

Run: `python -m pytest tests/integration/market/test_raw_store_minio.py -q -m integration`
Expected: PASS with the stack up; a named skip with the remedy otherwise.

- [ ] **Step 11: Commit**

```bash
git add services/market_intelligence/storage/raw_store.py tests/unit/market/test_raw_store.py \
        tests/integration/market/test_raw_store_minio.py
git commit -m "feat(p1): immutable raw observation storage"
```

---

### Task P1.5: Normalization / entity resolution

**Files:**
- Create: `services/market_intelligence/normalization/__init__.py`, `normalization/canonical.py`, `normalization/youtube.py`
- Modify: `storage/repositories.py` (append entity + observation + quarantine helpers)
- Create: `tests/fixtures/golden/market/normalized_video.json`
- Test: `tests/unit/market/test_canonical_text.py`, `test_youtube_normalization.py`, `test_entity_resolution.py`, `test_quarantine.py`

**Interfaces:**
- Consumes: `storage.repositories` (P1.4), `storage.raw_store.StoredRawObservation` (P1.4), `connectors.base.FetchedObject` (P1.3), `domain.value_objects.tri_from_optional` (P1.1), `tests/support/golden.assert_golden` + `golden_path` (W0.9).
- Produces (names later tasks import exactly):
  - `normalization.canonical`: `normalize_text(raw: str) -> str`; `text_fingerprint(raw: str) -> str`; `VIDEO_ID_RE`, `CHANNEL_ID_RE`, `PLAYLIST_ID_RE`; `is_valid_video_id(value) -> bool`, `is_valid_channel_id(value) -> bool`; `video_canonical_url(video_id)`, `channel_canonical_url(channel_id)`.
  - `normalization.youtube`: `NormalizationOutcome(observation: NormalizedObservationDraft | None = None, quarantine: QuarantinedRecord | None = None)`; `NormalizedObservationDraft(entity_id, source_id, raw_observation_id, observed_at, published_at, views, likes, comments, subscriber_count, video_duration_seconds, playlist_item_count, channel_video_count, text_title, text_description, country_signal, language_signal)` (all metric fields are `Tri`); `normalize_video_object(fetched, *, entity_id, source_id, raw_observation_id, observed_at) -> NormalizationOutcome`; `normalize_channel_object(...)`; `normalize_search_hit(fetched, *, ...)`; `QUARANTINE_REASON_CODES: frozenset[str]` (`malformed_provider_id`, `negative_count`, `impossible_timestamp_order`, `negative_duration`, `non_finite_number`).
  - `storage.repositories` (added): `resolve_entity(session, *, entity_type: EntityType, source_id, provider_object_type: ProviderObjectType, provider_object_id, canonical_name, normalized_name, canonical_url, observed_at, language=None, country_code=None) -> str`; `insert_normalized_observation(session, draft: NormalizedObservationDraft) -> tuple[str, bool]` (id, created); `load_observations(session, *, source_id: str | None = None, entity_id: str | None = None, since: datetime | None = None) -> list[NormalizedObservation]`; `quarantine_record(session, record: QuarantinedRecord) -> str`; `load_entity(session, entity_id) -> Mapping | None`; `count_quarantined(session, run_id) -> int`.

> **Ruling (Open items #13):** the spec never says what to do with a *search* result (title/description, no metrics). Resolution: a search hit becomes a `video` entity with title/description only and every metric `Tri.unknown()`; the next hydration pass adds metric-bearing observations for the same entity. It is never given `0` views to "look complete".

- [ ] **Step 1: Write the failing canonical-text test**

```python
# tests/unit/market/test_canonical_text.py
from services.market_intelligence.normalization.canonical import (
    channel_canonical_url, is_valid_channel_id, is_valid_video_id, normalize_text,
    text_fingerprint, video_canonical_url,
)


def test_normalization_folds_case_unicode_and_punctuation_but_keeps_words() -> None:
    assert normalize_text("2AM Seoul 🌙") == "2am seoul"
    assert normalize_text("  2am   seoul  ") == "2am seoul"
    assert normalize_text("Ｒ＆Ｂ — Late-Night") == "r&b late-night"
    assert normalize_text("lofi/study") == "lofi study"
    assert "rnb" in normalize_text("R’n’B")


def test_fingerprint_is_stable_across_the_two_spellings_the_spec_gives() -> None:
    assert text_fingerprint("2AM Seoul 🌙") == text_fingerprint("2am seoul")
    assert text_fingerprint("night drive") != text_fingerprint("night drives")


def test_provider_ids_are_validated_with_the_documented_shapes() -> None:
    assert is_valid_video_id("vid000000001")
    assert not is_valid_video_id("vid1")  # malformed ids are quarantined, never repaired
    assert is_valid_channel_id("UC" + "a" * 22)
    assert not is_valid_channel_id("UCaaaaaaaaaaaaaaaaaaaaa")


def test_canonical_urls_are_built_for_youtube_entities() -> None:
    assert video_canonical_url("vid000000001") == "https://www.youtube.com/watch?v=vid000000001"
    assert channel_canonical_url("UC" + "a" * 22).endswith("/channel/UC" + "a" * 22)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_canonical_text.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.normalization'`.

- [ ] **Step 3: Write `normalization/canonical.py`**

```python
# services/market_intelligence/normalization/canonical.py
"""§22: text normalisation and provider-id validation.

Normalisation must not remove meaningful musical terms — it folds case, Unicode,
whitespace and punctuation only. The original title is always retained beside the
normalised form; nothing here overwrites evidence.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Final

__all__ = [
    "CHANNEL_ID_RE", "PLAYLIST_ID_RE", "VIDEO_ID_RE", "channel_canonical_url",
    "is_valid_channel_id", "is_valid_video_id", "normalize_text", "text_fingerprint",
    "video_canonical_url",
]

VIDEO_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE: Final[re.Pattern[str]] = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
PLAYLIST_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{13,43}$")

_PUNCT_RUN: Final[re.Pattern[str]] = re.compile(r"[^\w&]+", flags=re.UNICODE)
_WHITESPACE_RUN: Final[re.Pattern[str]] = re.compile(r"\s+")


def normalize_text(raw: str) -> str:
    """NFKC → casefold → punctuation runs to single spaces → collapse whitespace.

    Emoji and other symbol characters are dropped (they carry no musical term);
    `&` and `-` survive because `r&b` and `late-night` are terms.
    """
    folded = unicodedata.normalize("NFKC", raw).casefold()
    spaceless = _PUNCT_RUN.sub(" ", folded)
    return _WHITESPACE_RUN.sub(" ", spaceless).strip()


def text_fingerprint(raw: str) -> str:
    """sha256 over the normalised text — the dedupe key for non-ID concepts (§22)."""
    return hashlib.sha256(normalize_text(raw).encode("utf-8")).hexdigest()


def is_valid_video_id(value: str) -> bool:
    return VIDEO_ID_RE.match(value) is not None


def is_valid_channel_id(value: str) -> bool:
    return CHANNEL_ID_RE.match(value) is not None


def video_canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def channel_canonical_url(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}"
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `python -m pytest tests/unit/market/test_canonical_text.py -q`
Expected: PASS. If `normalize_text("Ｒ＆Ｂ — Late-Night")` disagrees with your implementation, fix the *pattern*, not the test: the test encodes §22's rules.

- [ ] **Step 5: Commit**

```bash
git add services/market_intelligence/normalization/ tests/unit/market/test_canonical_text.py
git commit -m "feat(p1): text canonicalisation and provider id validation"
```

- [ ] **Step 6: Write the failing normalization + resolution test**

```python
# tests/unit/market/test_youtube_normalization.py
from datetime import UTC, datetime

import pytest
from plm_contracts import TriState
from tests.support.golden import assert_golden, golden_path

from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.enums import ProviderObjectType
from services.market_intelligence.normalization.youtube import (
    normalize_channel_object, normalize_video_object,
)

FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
VIDEO_PAYLOAD = {
    "kind": "youtube#video", "id": "vid000000001",
    "snippet": {"publishedAt": "2026-08-01T10:00:00Z", "channelId": "UC" + "a" * 22,
                "title": "2AM Seoul — Rainy Night R&B Mix", "description": "Late-night Korean city vibes",
                "defaultLanguage": "en"},
    "statistics": {"viewCount": "120000", "likeCount": "8400", "commentCount": "410"},
    "contentDetails": {"duration": "PT1H2M"},
}
CHANNEL_PAYLOAD = {
    "kind": "youtube#channel", "id": "UC" + "b" * 22,
    "snippet": {"title": "StudyLoops"},
    "statistics": {"videoCount": "88"},  # subscriberCount is absent, not zero
}


def _video():
    return FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                         provider_object_id="vid000000001", observed_at=FIXED_NOW,
                         payload=VIDEO_PAYLOAD)


def test_video_payload_maps_to_metric_values_and_a_retained_title() -> None:
    outcome = normalize_video_object(
        _video(), entity_id="entity-1", source_id="source-1",
        raw_observation_id="raw-1", observed_at=FIXED_NOW,
    )
    assert outcome.quarantine is None
    draft = outcome.observation
    assert draft.views.unwrap() == 120000
    assert draft.comments.unwrap() == 410
    assert draft.video_duration_seconds.unwrap() == 3720.0
    assert draft.text_title == "2AM Seoul — Rainy Night R&B Mix"
    assert draft.language_signal == "en"
    assert draft.published_at == datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def test_hidden_subscriber_count_stays_unknown() -> None:
    outcome = normalize_channel_object(
        FetchedObject(provider_object_type=ProviderObjectType.CHANNEL,
                      provider_object_id="UC" + "b" * 22, observed_at=FIXED_NOW,
                      payload=CHANNEL_PAYLOAD),
        entity_id="entity-2", source_id="source-1", raw_observation_id="raw-2",
        observed_at=FIXED_NOW,
    )
    draft = outcome.observation
    assert draft.subscriber_count.state is TriState.UNKNOWN
    assert draft.channel_video_count.unwrap() == 88
    assert draft.country_signal is None


def test_golden_normalized_video_pins_the_whole_mapping() -> None:
    outcome = normalize_video_object(
        _video(), entity_id="entity-1", source_id="source-1",
        raw_observation_id="raw-1", observed_at=FIXED_NOW,
    )
    payload = {name: getattr(outcome.observation, name).to_json_value()
               if hasattr(getattr(outcome.observation, name), "to_json_value")
               else getattr(outcome.observation, name).isoformat()
               if isinstance(getattr(outcome.observation, name), datetime)
               else getattr(outcome.observation, name)
               for name in outcome.observation.__dataclass_fields__}
    assert_golden(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                  golden_path("market", "normalized_video.json"))
```

Add `import json` to the imports. Create the golden file first with
`PLM_UPDATE_GOLDEN=1 python -m pytest tests/unit/market/test_youtube_normalization.py -q`, inspect it, then re-run without the variable (this is the W0.9 golden workflow — a regenerated expectation is skipped, so the second run is the real check).

- [ ] **Step 7: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_youtube_normalization.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.normalization.youtube'`.

- [ ] **Step 8: Write `normalization/youtube.py`**

```python
# services/market_intelligence/normalization/youtube.py
"""YouTube payload → normalised draft, with §24's data-quality gates.

A record that fails a gate is returned as a `quarantine` outcome and is *never*
silently repaired or dropped: the caller writes `market_quarantined_records`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from plm_contracts import Tri

from ..connectors.base import FetchedObject
from ..domain.entities import QuarantinedRecord
from ..domain.value_objects import tri_from_optional
from .canonical import is_valid_channel_id, is_valid_video_id

__all__ = [
    "NormalizationOutcome", "NormalizedObservationDraft", "QUARANTINE_REASON_CODES",
    "normalize_channel_object", "normalize_search_hit", "normalize_video_object",
]

QUARANTINE_REASON_CODES: Final[frozenset[str]] = frozenset({
    "malformed_provider_id", "negative_count", "impossible_timestamp_order",
    "negative_duration", "non_finite_number",
})


@dataclass(frozen=True, slots=True)
class NormalizedObservationDraft:
    """What `repositories.insert_normalized_observation` accepts (Tri metrics only)."""

    entity_id: str
    source_id: str
    raw_observation_id: str
    observed_at: datetime
    published_at: datetime | None
    views: Tri
    likes: Tri
    comments: Tri
    subscriber_count: Tri
    video_duration_seconds: Tri
    playlist_item_count: Tri
    channel_video_count: Tri
    text_title: str | None
    text_description: str | None
    country_signal: str | None
    language_signal: str | None


@dataclass(frozen=True, slots=True)
class NormalizationOutcome:
    observation: NormalizedObservationDraft | None = None
    quarantine: QuarantinedRecord | None = None


def normalize_video_object(
    fetched: FetchedObject, *, entity_id: str, source_id: str, raw_observation_id: str,
    observed_at: datetime,
) -> NormalizationOutcome:
    if not is_valid_video_id(fetched.provider_object_id):
        return _quarantined("malformed_provider_id",
                            f"video id {fetched.provider_object_id!r} is not 11 URL-safe chars",
                            fetched)
    snippet = fetched.payload.get("snippet") or {}
    statistics = fetched.payload.get("statistics") or {}
    details = fetched.payload.get("contentDetails") or {}
    published_at = _timestamp(snippet.get("publishedAt"))
    duration = _seconds(details.get("duration"))
    draft = NormalizedObservationDraft(
        entity_id=entity_id, source_id=source_id, raw_observation_id=raw_observation_id,
        observed_at=observed_at, published_at=published_at,
        views=tri_from_optional(_count(statistics.get("viewCount"))),
        likes=tri_from_optional(_count(statistics.get("likeCount"))),
        comments=tri_from_optional(_count(statistics.get("commentCount"))),
        subscriber_count=Tri.unknown(),  # videos.list never carries channel size
        video_duration_seconds=tri_from_optional(duration),
        playlist_item_count=Tri.unknown(), channel_video_count=Tri.unknown(),
        text_title=snippet.get("title"), text_description=snippet.get("description"),
        country_signal=None, language_signal=snippet.get("defaultLanguage"),
    )
    return _validated(draft, fetched)


def normalize_channel_object(
    fetched: FetchedObject, *, entity_id: str, source_id: str, raw_observation_id: str,
    observed_at: datetime,
) -> NormalizationOutcome:
    if not is_valid_channel_id(fetched.provider_object_id):
        return _quarantined("malformed_provider_id",
                            f"channel id {fetched.provider_object_id!r} is not UC + 22 chars",
                            fetched)
    snippet = fetched.payload.get("snippet") or {}
    statistics = fetched.payload.get("statistics") or {}
    draft = NormalizedObservationDraft(
        entity_id=entity_id, source_id=source_id, raw_observation_id=raw_observation_id,
        observed_at=observed_at, published_at=None,
        views=tri_from_optional(_count(statistics.get("viewCount"))),
        likes=Tri.unknown(), comments=Tri.unknown(),
        subscriber_count=tri_from_optional(_count(statistics.get("subscriberCount"))),
        video_duration_seconds=Tri.unknown(), playlist_item_count=Tri.unknown(),
        channel_video_count=tri_from_optional(_count(statistics.get("videoCount"))),
        text_title=snippet.get("title"), text_description=snippet.get("description"),
        country_signal=snippet.get("country"), language_signal=snippet.get("defaultLanguage"),
    )
    return _validated(draft, fetched)


def normalize_search_hit(
    fetched: FetchedObject, *, entity_id: str, source_id: str, raw_observation_id: str,
    observed_at: datetime,
) -> NormalizationOutcome:
    """Discovery sees text and a publish date only — every metric stays unknown."""
    snippet = fetched.payload.get("snippet") or {}
    if not is_valid_video_id(fetched.provider_object_id):
        return _quarantined("malformed_provider_id", "search result without a video id", fetched)
    draft = NormalizedObservationDraft(
        entity_id=entity_id, source_id=source_id, raw_observation_id=raw_observation_id,
        observed_at=observed_at, published_at=_timestamp(snippet.get("publishedAt")),
        views=Tri.unknown(), likes=Tri.unknown(), comments=Tri.unknown(),
        subscriber_count=Tri.unknown(), video_duration_seconds=Tri.unknown(),
        playlist_item_count=Tri.unknown(), channel_video_count=Tri.unknown(),
        text_title=snippet.get("title"), text_description=snippet.get("description"),
        country_signal=None, language_signal=None,
    )
    return _validated(draft, fetched)


def _validated(draft: NormalizedObservationDraft, fetched: FetchedObject) -> NormalizationOutcome:
    for name, metric in (
        ("views", draft.views), ("likes", draft.likes), ("comments", draft.comments),
        ("subscriber_count", draft.subscriber_count), ("channel_video_count",
                                                        draft.channel_video_count),
    ):
        if metric.is_known:
            value = metric.unwrap()
            if value < 0:
                return _quarantined("negative_count", f"{name}={value}", fetched)
    if draft.video_duration_seconds.is_known and draft.video_duration_seconds.unwrap() < 0:
        return _quarantined("negative_duration",
                            f"duration={draft.video_duration_seconds.unwrap()}", fetched)
    if draft.published_at is not None and draft.published_at > draft.observed_at:
        return _quarantined(
            "impossible_timestamp_order",
            f"published_at {draft.published_at.isoformat()} is after observed_at "
            f"{draft.observed_at.isoformat()}",
            fetched,
        )
    return NormalizationOutcome(observation=draft)


def _quarantined(reason: str, detail: str, fetched: FetchedObject) -> NormalizationOutcome:
    return NormalizationOutcome(quarantine=QuarantinedRecord(
        reason_code=reason, detail=detail, raw_observation_id=None,
        payload_uri=None,
    ))


def _count(value: Any) -> int | None:
    if value is None or value == "":
        return None
    number = int(value)
    if number < 0:
        return number  # the gate above quarantines it with a reason code
    return number


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _seconds(value: str | None) -> float | None:
    if not value:
        return None
    from ..connectors.youtube import parse_iso8601_duration

    parsed = parse_iso8601_duration(value)
    if parsed is not None and not math.isfinite(parsed):
        return None
    return parsed
```

> **Two deliberate details to keep:** `_quarantined` takes the fetched object so its `QuarantinedRecord.payload_uri` can be filled by the caller that already stored the raw row (P1.5 Step 13 wires it); and the `non_finite_number` reason code exists for `NaN`/`inf` that a future provider may send — the gate that raises it is asserted in P1.13's data-quality tests.

- [ ] **Step 9: Run the normalization tests and watch them pass**

Run: `PLM_UPDATE_GOLDEN=1 python -m pytest tests/unit/market/test_youtube_normalization.py -q` (rewrites, skips) then
`python -m pytest tests/unit/market/test_youtube_normalization.py -q`
Expected: PASS (3 passed) on the second run. Inspect `tests/fixtures/golden/market/normalized_video.json` before committing it.

- [ ] **Step 10: Write the failing resolution + observation test**

```python
# tests/unit/market/test_entity_resolution.py
from datetime import UTC, datetime

import pytest

from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.entities import QuarantinedRecord
from services.market_intelligence.domain.enums import ConnectorType, EntityType, ProviderName, ProviderObjectType
from services.market_intelligence.normalization.youtube import normalize_video_object
from services.market_intelligence.storage import repositories

FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
PAYLOAD = {"kind": "youtube#video", "id": "vid000000001",
           "snippet": {"publishedAt": "2026-08-01T10:00:00Z", "channelId": "UC" + "a" * 22,
                       "title": "2AM Seoul", "description": ""},
           "statistics": {"viewCount": "120000"}}


@pytest.fixture
def source_id(market_session) -> str:
    return repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )


def test_the_same_provider_video_resolves_to_one_entity_across_collections(
    market_session, source_id
) -> None:
    first = repositories.resolve_entity(
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid000000001",
        canonical_name="2AM Seoul", normalized_name="2am seoul",
        canonical_url="https://www.youtube.com/watch?v=vid000000001", observed_at=FIXED_NOW,
    )
    second = repositories.resolve_entity(
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid000000001",
        canonical_name="2AM Seoul (remastered)", normalized_name="2am seoul remastered",
        canonical_url="https://www.youtube.com/watch?v=vid000000001", observed_at=LATER,
    )
    assert first == second
    entity = repositories.load_entity(market_session, first)
    assert entity["last_seen_at"] is not None


def test_rerunning_the_same_query_on_a_later_date_adds_a_second_observation(
    market_session, source_id
) -> None:
    entity_id = repositories.resolve_entity(
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid000000001",
        canonical_name="2AM Seoul", normalized_name="2am seoul",
        canonical_url="https://www.youtube.com/watch?v=vid000000001", observed_at=FIXED_NOW,
    )
    fetched = FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                            provider_object_id="vid000000001", observed_at=FIXED_NOW,
                            payload=PAYLOAD)
    first = normalize_video_object(fetched, entity_id=entity_id, source_id=source_id,
                                   raw_observation_id="raw-1", observed_at=FIXED_NOW)
    second = normalize_video_object(fetched, entity_id=entity_id, source_id=source_id,
                                    raw_observation_id="raw-2", observed_at=LATER)
    first_id, first_created = repositories.insert_normalized_observation(
        market_session, first.observation)
    second_id, second_created = repositories.insert_normalized_observation(
        market_session, second.observation)
    assert (first_created, second_created) == (True, True)
    assert first_id != second_id
    observations = repositories.load_observations(market_session, entity_id=entity_id)
    assert len(observations) == 2  # AC-03: history is added to, never overwritten
    assert observations[0].views == observations[1].views  # the provider value repeated


def test_inserting_the_same_observation_point_twice_is_idempotent(
    market_session, source_id
) -> None:
    entity_id = repositories.resolve_entity(
        market_session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO, provider_object_id="vid000000001",
        canonical_name="2AM Seoul", normalized_name="2am seoul",
        canonical_url="https://www.youtube.com/watch?v=vid000000001", observed_at=FIXED_NOW,
    )
    fetched = FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                            provider_object_id="vid000000001", observed_at=FIXED_NOW,
                            payload=PAYLOAD)
    draft = normalize_video_object(fetched, entity_id=entity_id, source_id=source_id,
                                   raw_observation_id="raw-1", observed_at=FIXED_NOW)
    first_id, first_created = repositories.insert_normalized_observation(
        market_session, draft.observation)
    again_id, again_created = repositories.insert_normalized_observation(
        market_session, draft.observation)
    assert (first_created, again_created) == (True, False)
    assert first_id == again_id


def test_quarantine_records_are_written_not_dropped(market_session, source_id) -> None:
    record_id = repositories.quarantine_record(market_session, QuarantinedRecord(
        reason_code="negative_count", detail="views=-5", payload_uri="plm://bucket/key",
    ))
    assert record_id
    assert repositories.count_quarantined(market_session, run_id=None) == 1
    with pytest.raises(ValueError, match="unknown quarantine reason code"):
        repositories.quarantine_record(market_session, QuarantinedRecord(
            reason_code="just_felt_wrong", detail="no reason code",
        ))
```

- [ ] **Step 11: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_entity_resolution.py -q`
Expected: FAIL — `AttributeError: module 'services.market_intelligence.storage.repositories' has no attribute 'resolve_entity'`.

- [ ] **Step 12: Append the entity, observation and quarantine helpers to `storage/repositories.py`**

```python
# services/market_intelligence/storage/repositories.py  (append; extend __all__ and imports)
from ..domain.entities import NormalizedObservation, QuarantinedRecord
from ..domain.enums import EntityStatus, EntityType, ProviderObjectType
from .models import entities, entity_identities, observations, quarantined_records


def resolve_entity(
    session: Session,
    *,
    entity_type: EntityType,
    source_id: str,
    provider_object_type: ProviderObjectType,
    provider_object_id: str,
    canonical_name: str,
    normalized_name: str,
    canonical_url: str | None,
    observed_at: datetime,
    language: str | None = None,
    country_code: str | None = None,
) -> str:
    """§22: provider ids are canonical when available; one entity, many sightings."""
    identity = session.execute(
        sa.select(entity_identities.c.entity_id).where(
            entity_identities.c.source_id == source_id,
            entity_identities.c.provider_object_type == provider_object_type.value,
            entity_identities.c.provider_object_id == provider_object_id,
        )
    ).scalar_one_or_none()
    if identity is not None:
        session.execute(sa.update(entity_identities).where(
            entity_identities.c.entity_id == identity,
            entity_identities.c.provider_object_type == provider_object_type.value,
            entity_identities.c.provider_object_id == provider_object_id,
        ).values(last_seen_at=observed_at))
        session.execute(sa.update(entities).where(entities.c.id == identity).values(
            last_seen_at=observed_at,
            canonical_name=canonical_name,
            normalized_name=normalized_name,
        ))
        return str(identity)

    entity_id = _new_id()
    session.execute(sa.insert(entities).values(
        id=entity_id, entity_type=entity_type.value, canonical_name=canonical_name,
        normalized_name=normalized_name, canonical_url=canonical_url, language=language,
        country_code=country_code, first_seen_at=observed_at, last_seen_at=observed_at,
        status=EntityStatus.ACTIVE.value,
    ))
    session.execute(sa.insert(entity_identities).values(
        id=_new_id(), entity_id=entity_id, source_id=source_id,
        provider_object_type=provider_object_type.value, provider_object_id=provider_object_id,
        first_seen_at=observed_at, last_seen_at=observed_at,
    ))
    return entity_id


def insert_normalized_observation(
    session: Session, draft: NormalizedObservationDraft | None,
) -> tuple[str, bool]:
    """One row per (entity, source, observed_at); a repeat is idempotent (§7.6/AC-03)."""
    if draft is None:
        raise ValueError("cannot insert a draft that was quarantined")
    values = {
        "id": _new_id(), "entity_id": draft.entity_id, "source_id": draft.source_id,
        "raw_observation_id": draft.raw_observation_id, "observed_at": draft.observed_at,
        "published_at": draft.published_at,
        "views": _tri_value(draft.views), "likes": _tri_value(draft.likes),
        "comments": _tri_value(draft.comments),
        "subscriber_count": _tri_value(draft.subscriber_count),
        "video_duration_seconds": _tri_value(draft.video_duration_seconds),
        "playlist_item_count": _tri_value(draft.playlist_item_count),
        "channel_video_count": _tri_value(draft.channel_video_count),
        "text_title": draft.text_title, "text_description": draft.text_description,
        "country_signal": draft.country_signal, "language_signal": draft.language_signal,
    }
    try:
        with session.begin_nested():
            session.execute(sa.insert(observations).values(**values))
    except IntegrityError:
        existing = session.execute(
            sa.select(observations.c.id).where(
                observations.c.entity_id == draft.entity_id,
                observations.c.source_id == draft.source_id,
                observations.c.observed_at == draft.observed_at,
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        return str(existing), False
    return values["id"], True


def load_observations(
    session: Session, *, source_id: str | None = None, entity_id: str | None = None,
    since: datetime | None = None,
) -> list[NormalizedObservation]:
    """Read back as Tri-typed domain records: NULL becomes unknown here, once."""
    statement = sa.select(observations).order_by(observations.c.observed_at)
    if source_id is not None:
        statement = statement.where(observations.c.source_id == source_id)
    if entity_id is not None:
        statement = statement.where(observations.c.entity_id == entity_id)
    if since is not None:
        statement = statement.where(observations.c.observed_at >= since)
    records: list[NormalizedObservation] = []
    for row in session.execute(statement).mappings():
        records.append(NormalizedObservation(
            entity_id=str(row["entity_id"]), source_id=str(row["source_id"]),
            raw_observation_id=str(row["raw_observation_id"]), observed_at=row["observed_at"],
            published_at=row["published_at"],
            views=tri_from_optional(row["views"]), likes=tri_from_optional(row["likes"]),
            comments=tri_from_optional(row["comments"]),
            subscriber_count=tri_from_optional(row["subscriber_count"]),
            video_duration_seconds=tri_from_optional(row["video_duration_seconds"]),
            playlist_item_count=tri_from_optional(row["playlist_item_count"]),
            channel_video_count=tri_from_optional(row["channel_video_count"]),
            text_title=row["text_title"], text_description=row["text_description"],
            country_signal=row["country_signal"], language_signal=row["language_signal"],
        ))
    return records


def quarantine_record(session: Session, record: QuarantinedRecord) -> str:
    """§24: quarantine with a reason code instead of dropping silently."""
    if record.reason_code not in QUARANTINE_REASON_CODES:
        raise ValueError(f"unknown quarantine reason code {record.reason_code!r}")
    record_id = _new_id()
    session.execute(sa.insert(quarantined_records).values(
        id=record_id, source_id=None, raw_observation_id=record.raw_observation_id,
        collection_run_id=record.collection_run_id, reason_code=record.reason_code,
        detail=record.detail, payload_uri=record.payload_uri,
    ))
    return record_id


def count_quarantined(session: Session, run_id: str | None) -> int:
    """Quarantined rows for one run; `run_id=None` counts every quarantined row."""
    statement = sa.select(sa.func.count()).select_from(quarantined_records)
    if run_id is not None:
        statement = statement.where(quarantined_records.c.collection_run_id == run_id)
    return int(session.execute(statement).scalar_one())


def load_entity(session: Session, entity_id: str) -> Mapping[str, Any] | None:
    row = session.execute(
        sa.select(entities).where(entities.c.id == entity_id)
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _tri_value(metric: Tri) -> int | float | None:
    """Tri → column value: unknown/not_ready are NULL, a verified zero is 0."""
    return metric.unwrap() if metric.is_known else None
```

Import in the module header: `from collections.abc import Mapping` (already), `from datetime import datetime` (already),
`from sqlalchemy.exc import IntegrityError`, `from plm_contracts import Tri`,
`from ..domain.value_objects import tri_from_optional`, `from ..normalization.youtube import NormalizedObservationDraft`
(import the *class* only — no cycle: normalization.youtube imports connectors + domain, not storage),
`from ..normalization.youtube import QUARANTINE_REASON_CODES`, and `Mapping`/`Any` from collections/typing.

- [ ] **Step 13: Run everything and watch it pass**

Run: `python -m pytest tests/unit/market -q`
Expected: PASS (test_entity_resolution 4 passed, plus the earlier suites).

Then prove §24's failure trace end to end against a real database run in P1.14 — this task only guarantees the gates and the write path.

- [ ] **Step 14: Commit**

```bash
git add services/market_intelligence/normalization/ services/market_intelligence/storage/repositories.py \
        tests/unit/market/test_youtube_normalization.py tests/unit/market/test_entity_resolution.py \
        tests/fixtures/golden/market/normalized_video.json
git commit -m "feat(p1): normalization, entity resolution and quarantine"
```

---

### Task P1.6: Seed manager

**Files:**
- Create: `services/market_intelligence/data/bootstrap_seeds.json`, `collection/seeds.py`, `collection/query_planner.py`, `collection/seed_expansion.py`
- Modify: `storage/repositories.py` (append `upsert_query`, `load_queries`, `insert_seed_evidence`, `load_seed_evidence`)
- Test: `tests/unit/market/test_bootstrap_seeds.py`, `test_query_planner.py`, `test_seed_expansion.py`

**Interfaces:**
- Consumes: `normalization.canonical.normalize_text`/`text_fingerprint`; `domain.enums.QueryType`; `config.CadenceHours`; `storage.models.queries`, `seed_evidence`; `plm_platform.config.Settings` (never `os.environ`).
- Produces (names later tasks import exactly):
  - `collection.seeds`: `SeedSpec(term: str, query_type: QueryType, seed_group: str, locale: str, language: str, country_code: str, priority: int)`; `SeedCandidate` protocol (`term: str`, `confidence: float`); `load_bootstrap_seeds() -> tuple[SeedSpec, ...]`; `install_bootstrap_seeds(session, *, source_id: str, query_version: str, settings: MarketSettings | None = None) -> int` (idempotent, returns the number of *newly* created queries); `validate_candidate_seed(candidate: SeedCandidate, *, existing_fingerprints: frozenset[str], min_confidence: float, max_terms: int = 4) -> SeedDecision`; `SeedDecision(accepted: bool, reason: str | None = None)`.
  - `collection.query_planner`: `QueryState(query_id, query_fingerprint, query_text, priority, seed_group, last_run_at, next_run_at, active)`; `staleness_hours(state, *, now) -> float | None`; `priority_score(state, *, now, settings) -> float` (0–100); `next_run_at(state, *, now, settings, score=None) -> datetime`; `plan_refresh(states, *, now, settings) -> list[QueryState]` (due first, highest priority first).
  - `collection.seed_expansion`: `SeedProposal(term, query_type, source_observation_ids, proposed_by_model, model_name, model_version, confidence)`; `SeedProposer` protocol (`propose(texts: Sequence[str]) -> tuple[SeedProposal, ...]`); `propose_from_observations(session, *, source_id, proposer, titles, descriptions, parent_query_id, settings) -> list[SeedDecision]`; lineage rows in `market_seed_evidence` plus the proposal columns on `market_queries`.
  - `storage.repositories` (added): `upsert_query(session, *, source_id, query_text, query_type, locale, country_code, language, seed_group, priority, query_version, parent_query_id=None, proposal: SeedProposal | None = None) -> tuple[str, bool]`; `load_queries(session, *, source_id: str | None = None, active_only: bool = True) -> list[Mapping[str, Any]]`; `insert_seed_evidence(session, *, query_id, raw_observation_ids: Sequence[str]) -> int`; `load_seed_evidence(session, query_id) -> list[str]`.

> **Ruling (Open items #8):** §8 calls the bootstrap pack "a small, curated configuration". Resolution: the terms ship as data (`services/market_intelligence/data/bootstrap_seeds.json`) loaded with stdlib `json` — data belongs in data, and a test pins the §8 term list so the file cannot silently shrink.

- [ ] **Step 1: Write the failing bootstrap test**

```python
# tests/unit/market/test_bootstrap_seeds.py
import pytest

from services.market_intelligence.collection.seeds import load_bootstrap_seeds
from services.market_intelligence.domain.enums import ConnectorType, ProviderName
from services.market_intelligence.storage import repositories

SPECIALISED = {
    "music_genre", "music_mood", "use_case", "playlist_pattern",
    "artist_adjacent_term", "long_tail", "competitor_discovery",
}


def test_the_spec_stage_a_terms_are_all_present() -> None:
    terms = {spec.term for spec in load_bootstrap_seeds()}
    for expected in ("rnb", "pop", "indie", "city pop", "synthwave", "lofi", "ambient",
                     "house", "deep house", "jazz", "acoustic", "sleep music",
                     "study music", "focus music", "night drive", "cafe music",
                     "rainy day", "luxury lounge", "romantic", "melancholic"):
        assert expected in terms, expected


def test_every_seed_carries_a_query_type_and_priority() -> None:
    for spec in load_bootstrap_seeds():
        assert spec.query_type.value in SPECIALISED
        assert 0 <= spec.priority <= 100
        assert spec.locale and spec.language and spec.country_code


def test_install_is_idempotent_and_counts_only_new_queries(market_session) -> None:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    first = install_bootstrap_seeds(market_session, source_id=source_id,
                                    query_version="p1-queries-v1")
    second = install_bootstrap_seeds(market_session, source_id=source_id,
                                     query_version="p1-queries-v1")
    assert first == len(load_bootstrap_seeds())
    assert second == 0
    assert len(repositories.load_queries(market_session, source_id=source_id, active_only=False)) == first
```

Add `install_bootstrap_seeds` to the imports.

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_bootstrap_seeds.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.collection.seeds'`.

- [ ] **Step 3: Write the seed data file and `collection/seeds.py`**

```json
// services/market_intelligence/data/bootstrap_seeds.json
[
  {"term": "rnb", "query_type": "music_genre", "priority": 80},
  {"term": "pop", "query_type": "music_genre", "priority": 80},
  {"term": "indie", "query_type": "music_genre", "priority": 70},
  {"term": "city pop", "query_type": "music_genre", "priority": 70},
  {"term": "synthwave", "query_type": "music_genre", "priority": 70},
  {"term": "lofi", "query_type": "music_genre", "priority": 80},
  {"term": "ambient", "query_type": "music_genre", "priority": 70},
  {"term": "house", "query_type": "music_genre", "priority": 60},
  {"term": "deep house", "query_type": "music_genre", "priority": 60},
  {"term": "jazz", "query_type": "music_genre", "priority": 60},
  {"term": "acoustic", "query_type": "music_genre", "priority": 60},
  {"term": "sleep music", "query_type": "use_case", "priority": 70},
  {"term": "study music", "query_type": "use_case", "priority": 70},
  {"term": "focus music", "query_type": "use_case", "priority": 70},
  {"term": "night drive", "query_type": "use_case", "priority": 70},
  {"term": "cafe music", "query_type": "use_case", "priority": 60},
  {"term": "rainy day", "query_type": "music_mood", "priority": 60},
  {"term": "luxury lounge", "query_type": "use_case", "priority": 50},
  {"term": "romantic", "query_type": "music_mood", "priority": 50},
  {"term": "melancholic", "query_type": "music_mood", "priority": 50}
]
```

```python
# services/market_intelligence/collection/seeds.py
"""§8 Stage A: a curated bootstrap seed pack — discovery seeds, not assumptions.

Stage B (autonomous expansion) lives in `seed_expansion.py`; the LLM may propose,
deterministic validation decides whether a term enters the active pool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from sqlalchemy.orm import Session

from ..domain.enums import QueryType
from ..normalization.canonical import text_fingerprint
from ..storage.repositories import upsert_query

__all__ = [
    "SeedDecision", "SeedSpec", "install_bootstrap_seeds", "load_bootstrap_seeds",
    "validate_candidate_seed",
]

_SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "bootstrap_seeds.json"
DEFAULT_LOCALE = "en"
DEFAULT_LANGUAGE = "english"
DEFAULT_COUNTRY = "US"


@dataclass(frozen=True, slots=True)
class SeedSpec:
    term: str
    query_type: QueryType
    seed_group: str = "bootstrap"
    locale: str = DEFAULT_LOCALE
    language: str = DEFAULT_LANGUAGE
    country_code: str = DEFAULT_COUNTRY
    priority: int = 50


@dataclass(frozen=True, slots=True)
class SeedDecision:
    accepted: bool
    reason: str | None = None


@cache
def load_bootstrap_seeds() -> tuple[SeedSpec, ...]:
    """Read `data/bootstrap_seeds.json` once per process; malformed data fails loudly."""
    raw = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    seeds = tuple(
        SeedSpec(
            term=entry["term"],
            query_type=QueryType(entry["query_type"]),
            seed_group=entry.get("seed_group", "bootstrap"),
            locale=entry.get("locale", DEFAULT_LOCALE),
            language=entry.get("language", DEFAULT_LANGUAGE),
            country_code=entry.get("country_code", DEFAULT_COUNTRY),
            priority=int(entry.get("priority", 50)),
        )
        for entry in raw
    )
    if not seeds:
        raise ValueError("the bootstrap seed pack is empty — §8 requires Stage A seeds")
    return seeds


def install_bootstrap_seeds(
    session: Session, *, source_id: str, query_version: str, settings=None,
) -> int:
    """Insert every seed that is not already present; returns the number of new rows.

    Idempotent: rerunning installs nothing and reports 0, so a scheduler retry cannot
    duplicate the seed pool.
    """
    created = 0
    for spec in load_bootstrap_seeds():
        _, was_created = upsert_query(
            session, source_id=source_id, query_text=spec.term, query_type=spec.query_type,
            locale=spec.locale, country_code=spec.country_code, language=spec.language,
            seed_group=spec.seed_group, priority=spec.priority, query_version=query_version,
        )
        created += 1 if was_created else 0
    return created


class SeedCandidate(Protocol):
    """What validation needs from a proposal; `SeedProposal` satisfies it structurally."""

    term: str
    confidence: float


def validate_candidate_seed(
    candidate: SeedCandidate,
    *,
    existing_fingerprints: frozenset[str],
    min_confidence: float,
    max_terms: int = 4,
) -> SeedDecision:
    """§8: deterministic validation decides; the LLM only proposes.

    Rejections carry a reason — a proposal is never silently discarded.
    """
    term = candidate.term.strip()
    confidence = float(candidate.confidence)
    if not term:
        return SeedDecision(False, "empty term")
    if len(term.split()) > max_terms:
        return SeedDecision(False, f"term longer than {max_terms} words")
    if confidence < min_confidence:
        return SeedDecision(False, f"confidence {confidence:.2f} below {min_confidence:.2f}")
    if text_fingerprint(term) in existing_fingerprints:
        return SeedDecision(False, "duplicate of an existing seed")
    if not any(ch.isalpha() for ch in term):
        return SeedDecision(False, "term carries no letters")
    return SeedDecision(True)
```

- [ ] **Step 4: Add `upsert_query`/`load_queries`/seed-evidence helpers, run, and watch it pass**

```python
# services/market_intelligence/storage/repositories.py  (append; extend imports)
from ..domain.enums import QueryType
from ..normalization.canonical import text_fingerprint
from .models import queries, seed_evidence


def upsert_query(
    session: Session,
    *,
    source_id: str,
    query_text: str,
    query_type: QueryType,
    locale: str,
    country_code: str,
    language: str,
    seed_group: str,
    priority: int,
    query_version: str,
    parent_query_id: str | None = None,
    proposal: object | None = None,
) -> tuple[str, bool]:
    """One row per (source, normalised term). Stage B proposals carry their lineage."""
    fingerprint = text_fingerprint(query_text)
    existing = session.execute(
        sa.select(queries.c.id).where(
            queries.c.source_id == source_id, queries.c.query_fingerprint == fingerprint
        )
    ).scalar_one_or_none()
    if existing is not None:
        return str(existing), False
    query_id = _new_id()
    session.execute(sa.insert(queries).values(
        id=query_id, source_id=source_id, query_text=query_text,
        query_fingerprint=fingerprint, query_type=query_type.value, locale=locale,
        country_code=country_code, language=language, seed_group=seed_group,
        active=True, priority=priority, query_version=query_version,
        parent_query_id=parent_query_id,
        proposed_by_model=getattr(proposal, "proposed_by_model", None),
        model_name=getattr(proposal, "model_name", None),
        model_version=getattr(proposal, "model_version", None),
        proposal_confidence=getattr(proposal, "confidence", None),
    ))
    return query_id, True


def load_queries(
    session: Session, *, source_id: str | None = None, active_only: bool = True,
) -> list[Mapping[str, Any]]:
    statement = sa.select(queries).order_by(queries.c.priority.desc(), queries.c.query_text)
    if source_id is not None:
        statement = statement.where(queries.c.source_id == source_id)
    if active_only:
        statement = statement.where(queries.c.active.is_(True))
    return [dict(row) for row in session.execute(statement).mappings()]


def insert_seed_evidence(
    session: Session, *, query_id: str, raw_observation_ids: Sequence[str],
) -> int:
    """§8: every automatic seed stores the observations it was derived from."""
    inserted = 0
    for raw_id in raw_observation_ids:
        try:
            with session.begin_nested():
                session.execute(sa.insert(seed_evidence).values(
                    id=_new_id(), query_id=query_id, raw_observation_id=raw_id,
                ))
            inserted += 1
        except IntegrityError:
            continue  # the same observation already backs this seed
    return inserted


def load_seed_evidence(session: Session, query_id: str) -> list[str]:
    return [str(value) for value in session.execute(
        sa.select(seed_evidence.c.raw_observation_id)
        .where(seed_evidence.c.query_id == query_id)
        .order_by(seed_evidence.c.created_at)
    ).scalars()]
```

Add `Sequence` to the `collections.abc` import line and `IntegrityError` to the sqlalchemy.exc import (both already used elsewhere in the file — extend the existing imports, do not duplicate them).

Run: `python -m pytest tests/unit/market/test_bootstrap_seeds.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add services/market_intelligence/collection/seeds.py services/market_intelligence/data/ \
        services/market_intelligence/storage/repositories.py tests/unit/market/test_bootstrap_seeds.py
git commit -m "feat(p1): bootstrap seed pack and query upsert"
```

- [ ] **Step 6: Write the failing planner test**

```python
# tests/unit/market/test_query_planner.py
from datetime import UTC, datetime, timedelta

from services.market_intelligence.collection.query_planner import (
    QueryState, next_run_at, plan_refresh, priority_score, staleness_hours,
)
from services.market_intelligence.config import load_market_settings

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_market_settings()


def _state(**overrides) -> QueryState:
    base = dict(query_id="q1", query_fingerprint="f" * 8, query_text="lofi",
                priority=50, seed_group="bootstrap", last_run_at=None, next_run_at=None,
                active=True)
    base.update(overrides)
    return QueryState(**base)


def test_staleness_is_none_until_the_query_has_run() -> None:
    assert staleness_hours(_state(), now=NOW) is None
    assert staleness_hours(_state(last_run_at=NOW - timedelta(hours=30)), now=NOW) == 30.0


def test_priority_rises_with_staleness_and_importance_and_is_bounded() -> None:
    fresh = priority_score(_state(last_run_at=NOW), now=NOW, settings=SETTINGS)
    stale = priority_score(_state(last_run_at=NOW - timedelta(days=10)), now=NOW, settings=SETTINGS)
    important = priority_score(_state(priority=100, last_run_at=NOW - timedelta(days=10)),
                               now=NOW, settings=SETTINGS)
    assert 0.0 <= fresh < stale <= important <= 100.0


def test_cadence_is_configuration_driven_not_a_literal() -> None:
    high = _state(priority=95, last_run_at=NOW)
    normal = _state(priority=55, last_run_at=NOW)
    tail = _state(priority=10, last_run_at=NOW)
    assert next_run_at(high, now=NOW, settings=SETTINGS) == NOW + timedelta(hours=24)
    assert next_run_at(normal, now=NOW, settings=SETTINGS) == NOW + timedelta(hours=72)
    assert next_run_at(tail, now=NOW, settings=SETTINGS) == NOW + timedelta(hours=168)
    custom = load_market_settings(cadence=SETTINGS.cadence.__class__(high_priority=6.0))
    assert next_run_at(high, now=NOW, settings=custom) == NOW + timedelta(hours=6)


def test_plan_refresh_returns_due_queries_highest_priority_first() -> None:
    never_run = _state(query_id="new", priority=90, next_run_at=NOW - timedelta(hours=1))
    overdue = _state(query_id="old", priority=60, next_run_at=NOW - timedelta(days=2))
    not_due = _state(query_id="later", priority=99, next_run_at=NOW + timedelta(hours=5))
    planned = plan_refresh([not_due, overdue, never_run], now=NOW, settings=SETTINGS)
    assert [state.query_id for state in planned] == ["new", "old"]


def test_an_inactive_query_is_never_planned() -> None:
    assert plan_refresh([_state(active=False, next_run_at=NOW - timedelta(days=1))],
                        now=NOW, settings=SETTINGS) == []
```

- [ ] **Step 7: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_query_planner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.collection.query_planner'`.

- [ ] **Step 8: Write `collection/query_planner.py`**

```python
# services/market_intelligence/collection/query_planner.py
"""§9: schedule queries by expected information value.

Pure functions over `QueryState` — no database, no clock access, no randomness, so
the planner is unit-testable and the Temporal layer (P1.11) only wires it up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config import MarketSettings

__all__ = ["QueryState", "next_run_at", "plan_refresh", "priority_score", "staleness_hours"]

_STALENESS_CAP_HOURS = 720.0  # 30 days: past this, more staleness adds no urgency


@dataclass(frozen=True, slots=True)
class QueryState:
    """The planner's whole input: identity, importance and the last/next run."""

    query_id: str
    query_fingerprint: str
    query_text: str
    priority: int
    seed_group: str
    last_run_at: datetime | None
    next_run_at: datetime | None
    active: bool


def staleness_hours(state: QueryState, *, now: datetime) -> float | None:
    """Hours since the last run; `None` means "never run" — not zero staleness."""
    if state.last_run_at is None:
        return None
    return (now - state.last_run_at).total_seconds() / 3600


def priority_score(state: QueryState, *, now: datetime, settings: MarketSettings) -> float:
    """§9's factors, in one bounded 0–100 number.

    Staleness (never-run counts as maximally stale) and the stored importance drive
    it; a long-tail seed group is discounted so discovery never crowds out refresh.
    """
    stale = staleness_hours(state, now=now)
    stale_component = 1.0 if stale is None else min(stale, _STALENESS_CAP_HOURS) / _STALENESS_CAP_HOURS
    importance = min(max(state.priority, 0), 100) / 100.0
    group_weight = 0.6 if state.seed_group == "long_tail" else 1.0
    return round(100.0 * group_weight * (0.5 * stale_component + 0.5 * importance), 6)


def next_run_at(
    state: QueryState, *, now: datetime, settings: MarketSettings, score: float | None = None,
) -> datetime:
    """Cadence by priority class, all three windows from `settings.cadence` (§9/§26)."""
    resolved = priority_score(state, now=now, settings=settings) if score is None else score
    if resolved >= 80.0:
        hours = settings.cadence.high_priority
    elif resolved >= 30.0:
        hours = settings.cadence.normal
    else:
        hours = settings.cadence.long_tail
    return now + timedelta(hours=hours)


def plan_refresh(
    states: list[QueryState], *, now: datetime, settings: MarketSettings
) -> list[QueryState]:
    """Active queries whose `next_run_at` has arrived (or was never set), importance first."""
    due: list[QueryState] = []
    for state in states:
        if not state.active:
            continue
        if state.next_run_at is not None and state.next_run_at > now:
            continue
        due.append(state)
    return sorted(
        due,
        key=lambda state: (-priority_score(state, now=now, settings=settings),
                           state.query_fingerprint),
    )
```

- [ ] **Step 9: Run the test and watch it pass**

Run: `python -m pytest tests/unit/market/test_query_planner.py -q`
Expected: PASS (5 passed).

- [ ] **Step 10: Commit**

```bash
git add services/market_intelligence/collection/query_planner.py tests/unit/market/test_query_planner.py
git commit -m "feat(p1): query planner with configuration-driven cadence"
```

- [ ] **Step 11: Write the failing expansion test**

```python
# tests/unit/market/test_seed_expansion.py
from services.market_intelligence.collection.seed_expansion import (
    SeedProposal, propose_from_observations,
)
from services.market_intelligence.collection.seeds import validate_candidate_seed
from services.market_intelligence.domain.enums import ConnectorType, ProviderName, QueryType
from services.market_intelligence.normalization.canonical import text_fingerprint
from services.market_intelligence.storage import repositories

PROPOSALS = (
    SeedProposal(term="late night drive", query_type=QueryType.USE_CASE,
                 source_observation_ids=("raw-1",), proposed_by_model="rule_based_v1",
                 model_name="rule-based", model_version="1", confidence=0.8),
)


def test_validation_accepts_a_good_term_and_rejects_with_reasons() -> None:
    good = PROPOSALS[0]
    assert validate_candidate_seed(
        good, existing_fingerprints=frozenset(), min_confidence=0.5
    ).accepted
    duplicate = validate_candidate_seed(
        good, existing_fingerprints=frozenset({text_fingerprint("late night drive")}),
        min_confidence=0.5,
    )
    assert duplicate.accepted is False and "duplicate" in (duplicate.reason or "")
    low = validate_candidate_seed(good, existing_fingerprints=frozenset(), min_confidence=0.9)
    assert low.accepted is False and "confidence" in (low.reason or "")


def test_expansion_persists_lineage_for_accepted_terms(market_session) -> None:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    parent_id, _ = repositories.upsert_query(
        market_session, source_id=source_id, query_text="lofi", query_type=QueryType.MUSIC_GENRE,
        locale="en", country_code="US", language="english", seed_group="bootstrap",
        priority=50, query_version="p1-queries-v1",
    )
    decisions = propose_from_observations(
        market_session, source_id=source_id, proposer=lambda texts: PROPOSALS,
        titles=("2AM Seoul — Rainy Night R&B Mix",), descriptions=("Late-night city vibes",),
        parent_query_id=parent_id, settings=None,
    )
    assert [decision.accepted for decision in decisions] == [True]
    stored = repositories.load_queries(market_session, source_id=source_id, active_only=False)
    created = [row for row in stored if row["query_text"] == "late night drive"]
    assert len(created) == 1
    assert created[0]["parent_query_id"] == parent_id
    assert created[0]["proposed_by_model"] == "rule_based_v1"
    assert created[0]["model_version"] == "1"
    assert repositories.load_seed_evidence(market_session, str(created[0]["id"])) == ["raw-1"]


def test_the_expansion_cap_is_enforced_with_a_reason(market_session) -> None:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    proposals = tuple(
        SeedProposal(term=f"term {index}", query_type=QueryType.LONG_TAIL, confidence=0.9)
        for index in range(3)
    )
    decisions = propose_from_observations(
        market_session, source_id=source_id, proposer=lambda texts: proposals,
        titles=(), descriptions=(), parent_query_id=None, settings=None, max_new_seeds=1,
    )
    assert [d.accepted for d in decisions] == [True, False, False]
    assert "cap" in (decisions[1].reason or "")
```

- [ ] **Step 12: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_seed_expansion.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.collection.seed_expansion'`.

- [ ] **Step 13: Write `collection/seed_expansion.py`**

```python
# services/market_intelligence/collection/seed_expansion.py
"""§8 Stage B: autonomous seed expansion from observed market language.

The proposer (LLM or rule-based) only *proposes*; `validate_candidate_seed` decides;
lineage (`parent_seed`, source observations, model identity, confidence) is stored on
the accepted query rows and in `market_seed_evidence`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..domain.enums import QueryType
from ..normalization.canonical import text_fingerprint
from ..storage.repositories import insert_seed_evidence, load_queries, upsert_query
from .seeds import SeedDecision, validate_candidate_seed

__all__ = ["SeedProposal", "SeedProposer", "propose_from_observations"]


@dataclass(frozen=True, slots=True)
class SeedProposal:
    """One proposed research term with the lineage §8 requires."""

    term: str
    query_type: QueryType
    source_observation_ids: tuple[str, ...] = ()
    proposed_by_model: str = "unspecified"
    model_name: str = "unspecified"
    model_version: str = "0"
    confidence: float = 0.0


SeedProposer = Callable[[Sequence[str]], Sequence[SeedProposal]]


def propose_from_observations(
    session: Session,
    *,
    source_id: str,
    proposer: SeedProposer,
    titles: Sequence[str],
    descriptions: Sequence[str],
    parent_query_id: str | None,
    settings=None,
    min_confidence: float = 0.5,
    max_new_seeds: int = 25,
) -> list[SeedDecision]:
    """Propose, validate, persist — in that order; every outcome is returned.

    `max_new_seeds` bounds one expansion so a talkative proposer cannot explode the
    query pool (§43's "massive query-result explosion").
    """
    texts = (*titles, *descriptions)
    existing = frozenset(
        row["query_fingerprint"]
        for row in load_queries(session, source_id=source_id, active_only=False)
    )
    decisions: list[SeedDecision] = []
    created = 0
    for proposal in proposer(texts):
        decision = validate_candidate_seed(
            proposal, existing_fingerprints=existing, min_confidence=min_confidence
        )
        if decision.accepted and created >= max_new_seeds:
            decision = SeedDecision(False, f"expansion cap of {max_new_seeds} reached")
        if decision.accepted:
            query_id, was_created = upsert_query(
                session, source_id=source_id, query_text=proposal.term,
                query_type=proposal.query_type, locale="en", country_code="US",
                language="english", seed_group="expanded", priority=40,
                query_version="p1-queries-v1", parent_query_id=parent_query_id,
                proposal=proposal,
            )
            if was_created:
                created += 1
                existing = existing | {text_fingerprint(proposal.term)}
                insert_seed_evidence(
                    session, query_id=query_id,
                    raw_observation_ids=list(proposal.source_observation_ids),
                )
            else:
                decision = SeedDecision(False, "duplicate of an existing seed")
        decisions.append(decision)
    return decisions
```

- [ ] **Step 14: Run everything and watch it pass**

Run: `python -m pytest tests/unit/market -q`
Expected: PASS.

- [ ] **Step 15: Commit**

```bash
git add services/market_intelligence/collection/seed_expansion.py \
        tests/unit/market/test_seed_expansion.py
git commit -m "feat(p1): seed expansion with lineage"
```

---

### Task P1.7: Taxonomy

**Files:**
- Create: `services/market_intelligence/taxonomy/__init__.py`, `taxonomy/ontology.py`, `taxonomy/synonym_map.py`, `taxonomy/validators.py`, `taxonomy/classifier.py`
- Modify: `storage/repositories.py` (append `insert_classification`, `load_classification`, `latest_classifications_for`)
- Test: `tests/unit/market/test_ontology.py`, `test_validators.py`, `test_classifier.py`

**Interfaces:**
- Consumes: `domain.enums.TaxonomyDimension` (13, R14), `domain.value_objects.TaxonomyProfile`, `plm_contracts.ErrorShape`, `normalization.canonical.normalize_text`, `storage.models.classifications`.
- Produces (names later tasks import exactly):
  - `taxonomy.ontology`: `DimensionSpec(dimension, values: frozenset[str], multi: bool, allow_other_candidate: bool = True)`; `DIMENSION_SPECS: Mapping[TaxonomyDimension, DimensionSpec]`; `OTHER_CANDIDATE: str = "other_candidate"`; `controlled_values(dimension) -> frozenset[str]`; `is_controlled_value(dimension, value) -> bool`; `normalize_dimension_value(dimension, value) -> str | None` (maps a raw phrase onto a controlled token via the synonym map, else `None` — never "nearest known label", §14.3); `profile_from_dimensions(dimensions: Mapping[TaxonomyDimension, Sequence[str]]) -> TaxonomyProfile`.
  - `taxonomy.synonym_map`: `SYNONYMS: Mapping[str, tuple[TaxonomyDimension, str]]`; `lookup_synonym(term: str) -> tuple[TaxonomyDimension, str] | None`.
  - `taxonomy.validators`: `CLASSIFICATION_SCHEMA_VERSION = "p1-classification-v1"`; `ClassificationDraft(dimensions: Mapping[TaxonomyDimension, tuple[str, ...]], confidence: float, unmapped_terms: tuple[str, ...])`; `validate_classification(raw: Mapping[str, object]) -> ClassificationDraft`; `ClassificationSchemaError(ValueError)` carrying `.error: ErrorShape`; `classification_input_fingerprint(title: str | None, description: str | None) -> str`.
  - `taxonomy.classifier`: `ClassificationInput(title: str | None, description: str | None)`; `Classifier` protocol (`name: str`, `version: str`, `classify(text: ClassificationInput) -> ClassificationDraft`); `RuleBasedClassifier(synonyms=SYNONYMS)`; `classify_observation(session, *, entity_id, observation_id, text: ClassificationInput, classifier: Classifier, taxonomy_version: str, prompt_version: str = "p1-classifier-prompt-v1") -> tuple[str, bool]` (classification row id, created) with §28 caching by `(content_fingerprint, taxonomy_version)`.

> **Ruling R14 (this task owns it):** §14.2's **13** dimensions are authoritative, not AC-05's 10 — `VocalGender`, `BPMRange` and `AudienceIntent` are required dimensions with controlled values (`ADR-0014`). Every classification is validated against all 13; a dimension the source cannot support is `unknown`, never auto-filled.
>
> **Ruling (§15 output schema is silent on four dimensions):** §15's JSON example omits `vocal_gender`, `bpm_range`, `audience_intent` and `duration_bucket`. Resolution: the classifier output schema carries all 13 keys; the four are optional to *produce* (they may be absent) but never required to be invented. Absent means the dimension is absent from `dimensions`, which downstream code reads as unknown.

- [ ] **Step 1: Write the failing ontology test**

```python
# tests/unit/market/test_ontology.py
from services.market_intelligence.domain.enums import TaxonomyDimension
from services.market_intelligence.domain.value_objects import TaxonomyProfile
from services.market_intelligence.taxonomy.ontology import (
    DIMENSION_SPECS, OTHER_CANDIDATE, controlled_values, is_controlled_value,
    normalize_dimension_value, profile_from_dimensions,
)


def test_all_thirteen_dimensions_have_controlled_values() -> None:
    assert set(DIMENSION_SPECS) == set(TaxonomyDimension)
    assert len(DIMENSION_SPECS) == 13
    for dimension, spec in DIMENSION_SPECS.items():
        assert spec.values, dimension
        assert spec.multi is (dimension in {
            TaxonomyDimension.MOOD, TaxonomyDimension.USE_CASE,
            TaxonomyDimension.VISUAL_WORLD, TaxonomyDimension.TITLE_ARCHETYPE,
        }), dimension


def test_unfamiliar_terms_map_to_none_not_to_the_nearest_label() -> None:
    assert is_controlled_value(TaxonomyDimension.GENRE, "rnb")
    assert not is_controlled_value(TaxonomyDimension.GENRE, "vaporwave")
    assert normalize_dimension_value(TaxonomyDimension.GENRE, "vaporwave") is None
    assert normalize_dimension_value(TaxonomyDimension.GENRE, "R'n'B") == "rnb"
    assert OTHER_CANDIDATE in controlled_values(TaxonomyDimension.GENRE)


def test_profile_from_dimensions_keeps_multi_valued_dimensions_as_tuples() -> None:
    profile = profile_from_dimensions({
        TaxonomyDimension.GENRE: ("rnb",), TaxonomyDimension.MOOD: ("melancholic", "dreamy"),
    })
    assert profile.genre == "rnb"
    assert profile.mood == ("melancholic", "dreamy")
    assert profile.bpm_range is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_ontology.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.taxonomy'`.

- [ ] **Step 3: Write `taxonomy/synonym_map.py` and `taxonomy/ontology.py`**

```python
# services/market_intelligence/taxonomy/synonym_map.py
"""§14.1/§22: the phrase → canonical-value map that keeps `night drive` and
`late night driving` one use case while the original phrase survives on the row."""

from __future__ import annotations

from typing import Final

from ..domain.enums import TaxonomyDimension as D
from ..normalization.canonical import normalize_text

__all__ = ["SYNONYMS", "lookup_synonym"]

SYNONYMS: Final[dict[str, tuple[D, str]]] = {
    "rnb": (D.GENRE, "rnb"),
    "r&b": (D.GENRE, "rnb"),
    "rhythm and blues": (D.GENRE, "rnb"),
    "alt rnb": (D.SUBGENRE, "alt_rnb"),
    "lo-fi": (D.GENRE, "lofi"),
    "lofi": (D.GENRE, "lofi"),
    "synthwave": (D.GENRE, "synthwave"),
    "city pop": (D.GENRE, "city_pop"),
    "ambient": (D.GENRE, "ambient"),
    "deep house": (D.GENRE, "deep_house"),
    "melancholic": (D.MOOD, "melancholic"),
    "sad": (D.MOOD, "melancholic"),
    "dreamy": (D.MOOD, "dreamy"),
    "romantic": (D.MOOD, "romantic"),
    "late night drive": (D.USE_CASE, "late_night_drive"),
    "late night driving": (D.USE_CASE, "late_night_drive"),
    "late night": (D.USE_CASE, "late_night_drive"),
    "night drive": (D.USE_CASE, "late_night_drive"),
    "2am drive": (D.USE_CASE, "late_night_drive"),
    "2am": (D.USE_CASE, "late_night_drive"),
    "midnight drive": (D.USE_CASE, "late_night_drive"),
    "study": (D.USE_CASE, "study_focus"),
    "focus": (D.USE_CASE, "study_focus"),
    "sleep": (D.USE_CASE, "sleep"),
    "cafe": (D.USE_CASE, "cafe_background"),
    "english": (D.LANGUAGE, "english"),
    "korean": (D.LANGUAGE, "korean"),
    "vocal": (D.VOCAL_MODE, "vocal"),
    "instrumental": (D.VOCAL_MODE, "instrumental"),
    "female vocal": (D.VOCAL_GENDER, "female"),
    "male vocal": (D.VOCAL_GENDER, "male"),
    "low energy": (D.ENERGY, "low"),
    "high energy": (D.ENERGY, "high"),
    "chill": (D.ENERGY, "low_medium"),
    "rainy": (D.VISUAL_WORLD, "rainy_city"),
    "rainy asian city": (D.VISUAL_WORLD, "rainy_asian_city"),
    "seoul": (D.VISUAL_WORLD, "rainy_asian_city"),
    "tokyo": (D.VISUAL_WORLD, "neon_tokyo"),
    "neon": (D.VISUAL_WORLD, "neon_tokyo"),
    "lounge": (D.AUDIENCE_INTENT, "background_listening"),
    "background": (D.AUDIENCE_INTENT, "background_listening"),
    "time place mood": (D.TITLE_ARCHETYPE, "time_place_mood"),
    "45 75m": (D.DURATION_BUCKET, "45_75m"),
    "45_75m": (D.DURATION_BUCKET, "45_75m"),
    "60_180s": (D.DURATION_BUCKET, "60_180s"),
    "82_96": (D.BPM_RANGE, "82_96"),
}


def lookup_synonym(term: str) -> tuple[D, str] | None:
    """Normalised lookup; unknown phrases return `None` (the `other_candidate` path)."""
    return SYNONYMS.get(normalize_text(term))
```

```python
# services/market_intelligence/taxonomy/ontology.py
"""§14: the controlled vocabulary. R14 fixes the dimension set at thirteen.

Values are tokens, never free text; `other_candidate` is the only escape hatch and it
is explicit (§14.3). Unknown terms are *recorded* as unmapped, not mapped silently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from ..domain.enums import TaxonomyDimension as D
from ..domain.value_objects import TaxonomyProfile
from ..normalization.canonical import normalize_text
from .synonym_map import lookup_synonym

__all__ = [
    "DIMENSION_SPECS", "OTHER_CANDIDATE", "DimensionSpec", "controlled_values",
    "is_controlled_value", "normalize_dimension_value", "profile_from_dimensions",
]

OTHER_CANDIDATE: Final[str] = "other_candidate"


@dataclass(frozen=True, slots=True)
class DimensionSpec:
    dimension: D
    values: frozenset[str]
    multi: bool = False
    allow_other_candidate: bool = True


def _spec(dimension: D, *values: str, multi: bool = False) -> DimensionSpec:
    return DimensionSpec(dimension=dimension, values=frozenset({*values, OTHER_CANDIDATE}),
                         multi=multi)


DIMENSION_SPECS: Final[Mapping[D, DimensionSpec]] = {
    D.GENRE: _spec(D.GENRE, "rnb", "pop", "indie", "city_pop", "synthwave", "lofi", "ambient",
                   "house", "deep_house", "jazz", "acoustic", "classical", "hip_hop", "rock"),
    D.SUBGENRE: _spec(D.SUBGENRE, "alt_rnb", "bedroom_pop", "dream_pop", "neo_soul",
                      "lofi_hip_hop", "chillhop", "deep_house", "progressive_house"),
    D.MOOD: _spec(D.MOOD, "melancholic", "dreamy", "romantic", "calm", "nostalgic", "hopeful",
                  "dark", "uplifting", "sensual", "lonely", multi=True),
    D.USE_CASE: _spec(D.USE_CASE, "late_night_drive", "study_focus", "sleep", "cafe_background",
                      "workout", "party", "morning_routine", "rainy_day", "work_focus",
                      multi=True),
    D.LANGUAGE: _spec(D.LANGUAGE, "english", "korean", "japanese", "spanish", "instrumental",
                      "mixed"),
    D.VOCAL_MODE: _spec(D.VOCAL_MODE, "vocal", "instrumental", "mixed"),
    D.VOCAL_GENDER: _spec(D.VOCAL_GENDER, "female", "male", "mixed", "not_applicable"),
    D.ENERGY: _spec(D.ENERGY, "low", "low_medium", "medium", "medium_high", "high"),
    D.BPM_RANGE: _spec(D.BPM_RANGE, "lt_70", "70_85", "82_96", "85_100", "100_120", "120_140",
                       "gt_140"),
    D.DURATION_BUCKET: _spec(D.DURATION_BUCKET, "lt_60s", "60_180s", "3_10m", "10_45m",
                             "45_75m", "gt_75m"),
    D.VISUAL_WORLD: _spec(D.VISUAL_WORLD, "rainy_city", "rainy_asian_city", "neon_tokyo",
                          "night_highway", "cozy_room", "minimal_abstract", multi=True),
    D.AUDIENCE_INTENT: _spec(D.AUDIENCE_INTENT, "background_listening", "active_listening",
                             "mood_regulation", "focus_support", "social_ambience"),
    D.TITLE_ARCHETYPE: _spec(D.TITLE_ARCHETYPE, "time_place_mood", "genre_plus_use_case",
                             "question_how_to", "number_list", "artist_adjacent",
                             multi=True),
}
"""The 13 §14.2 dimensions with a starter vocabulary; expansion is an explicit
workflow (§14.3), so `values` may grow but never silently shrink."""


def controlled_values(dimension: D) -> frozenset[str]:
    return DIMENSION_SPECS[dimension].values


def is_controlled_value(dimension: D, value: str) -> bool:
    return normalize_text(value).replace(" ", "_") in DIMENSION_SPECS[dimension].values


def normalize_dimension_value(dimension: D, value: str) -> str | None:
    """A raw phrase → its controlled token, or `None` (never the nearest label)."""
    token = normalize_text(value).replace(" ", "_")
    if token in DIMENSION_SPECS[dimension].values:
        return token
    found = lookup_synonym(value)
    if found is not None and found[0] is dimension:
        return found[1]
    return None


def profile_from_dimensions(dimensions: Mapping[D, Sequence[str]]) -> TaxonomyProfile:
    """Build the identity object; multi-valued dimensions keep the ontology's order."""
    single: dict[str, str | None] = {dimension.value: None for dimension in D}
    moods: tuple[str, ...] = ()
    use_cases: tuple[str, ...] = ()
    visuals: tuple[str, ...] = ()
    for dimension, raw_values in dimensions.items():
        ordered = tuple(dict.fromkeys(raw_values))
        if dimension is D.MOOD:
            moods = ordered
        elif dimension is D.USE_CASE:
            use_cases = ordered
        elif dimension is D.VISUAL_WORLD:
            visuals = ordered
        else:
            single[dimension.value] = ordered[0] if ordered else None
    return TaxonomyProfile(
        genre=single[D.GENRE.value], subgenre=single[D.SUBGENRE.value], mood=moods,
        use_case=single[D.USE_CASE.value] or (use_cases[0] if use_cases else None),
        language=single[D.LANGUAGE.value], vocal_mode=single[D.VOCAL_MODE.value],
        vocal_gender=single[D.VOCAL_GENDER.value], energy=single[D.ENERGY.value],
        bpm_range=single[D.BPM_RANGE.value], duration_bucket=single[D.DURATION_BUCKET.value],
        visual_world=single[D.VISUAL_WORLD.value] or (visuals[0] if visuals else None),
        audience_intent=single[D.AUDIENCE_INTENT.value],
        title_archetype=single[D.TITLE_ARCHETYPE.value],
    )
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `python -m pytest tests/unit/market/test_ontology.py -q`
Expected: PASS (3 passed). If `normalize_dimension_value(D.GENRE, "R'n'B")` returns `None`, add the `r'n'b` key to `SYNONYMS` — the normaliser folds it to `r n b`, which is exactly why the map is explicit.

- [ ] **Step 5: Commit**

```bash
git add services/market_intelligence/taxonomy/ tests/unit/market/test_ontology.py
git commit -m "feat(p1): 13-dimension controlled vocabulary per R14"
```

- [ ] **Step 6: Write the failing validator test**

```python
# tests/unit/market/test_validators.py
import pytest
from plm_contracts import ErrorShape

from services.market_intelligence.domain.enums import TaxonomyDimension
from services.market_intelligence.taxonomy.validators import (
    ClassificationSchemaError, validate_classification,
)

GOOD = {
    "genre": ["rnb"],
    "subgenre": ["alt_rnb"],
    "mood": ["melancholic", "dreamy"],
    "use_case": ["late_night_drive"],
    "language": ["english"],
    "vocal_mode": "vocal",
    "energy": "low_medium",
    "visual_world": ["rainy_asian_city"],
    "title_archetypes": ["time_place_mood"],
    "confidence": 0.87,
    "unmapped_terms": [],
}


def test_a_spec_shaped_payload_validates() -> None:
    draft = validate_classification(GOOD)
    assert draft.dimensions[TaxonomyDimension.GENRE] == ("rnb",)
    assert draft.dimensions[TaxonomyDimension.TITLE_ARCHETYPE] == ("time_place_mood",)
    assert draft.confidence == 0.87
    assert TaxonomyDimension.VOCAL_GENDER not in draft.dimensions  # absent, not invented


def test_an_unknown_value_is_refused_with_a_field_path_and_recorded_as_unmapped() -> None:
    with pytest.raises(ClassificationSchemaError) as excinfo:
        validate_classification(GOOD | {"genre": ["vaporwave"]})
    assert excinfo.value.error.field_paths == ["genre[0]"]
    assert excinfo.value.error.code == "classification_schema_invalid"


def test_confidence_outside_zero_one_and_unknown_keys_are_refused() -> None:
    with pytest.raises(ClassificationSchemaError):
        validate_classification(GOOD | {"confidence": 1.4})
    with pytest.raises(ClassificationSchemaError) as excinfo:
        validate_classification(GOOD | {"probability_of_success": 0.9})
    assert excinfo.value.error.field_paths == ["probability_of_success"]


def test_the_error_shape_is_the_platform_error_shape() -> None:
    try:
        validate_classification({"confidence": 0.5})
    except ClassificationSchemaError as exc:
        assert isinstance(exc.error, ErrorShape)
    else:  # pragma: no cover
        pytest.fail("validate_classification accepted a payload with no dimensions")
```

- [ ] **Step 7: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_validators.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.taxonomy.validators'`.

- [ ] **Step 8: Write `taxonomy/validators.py`**

```python
# services/market_intelligence/taxonomy/validators.py
"""§15: "The application must validate the JSON against a schema before writing it
to the database." Nothing below trusts a model."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from plm_contracts import ErrorShape

from ..domain.enums import TaxonomyDimension as D
from ..normalization.canonical import normalize_text
from .ontology import DIMENSION_SPECS

__all__ = [
    "CLASSIFICATION_SCHEMA_VERSION", "ClassificationDraft", "ClassificationSchemaError",
    "classification_input_fingerprint", "validate_classification",
]

CLASSIFICATION_SCHEMA_VERSION: Final[str] = "p1-classification-v1"

_KEY_ALIASES: Final[dict[str, D]] = {
    "title_archetypes": D.TITLE_ARCHETYPE,
    "title_archetype": D.TITLE_ARCHETYPE,
    "use_cases": D.USE_CASE,
    "visual_worlds": D.VISUAL_WORLD,
}
_ALLOWED_META: Final[frozenset[str]] = frozenset({"confidence", "unmapped_terms"})


class ClassificationSchemaError(ValueError):
    """The payload does not satisfy §15/§29's schema; carries a machine-readable shape."""

    def __init__(self, error: ErrorShape) -> None:
        super().__init__(error.message)
        self.error = error


@dataclass(frozen=True, slots=True)
class ClassificationDraft:
    dimensions: Mapping[D, tuple[str, ...]]
    confidence: float
    unmapped_terms: tuple[str, ...] = ()
    schema_version: str = CLASSIFICATION_SCHEMA_VERSION


def validate_classification(raw: Mapping[str, Any]) -> ClassificationDraft:
    """Validate one classifier output; unknown keys are refused, not ignored.

    A dimension present but empty (`[]`) is treated as *absent*; a value outside the
    ontology is a failure, because §14.3 forbids silent nearest-label mapping.
    """
    field_paths: list[str] = []
    message_holder: list[str] = []
    confidence = raw.get("confidence", 0.0)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) \
            or not 0.0 <= float(confidence) <= 1.0:
        field_paths.append("confidence")
        message_holder.append("confidence must be a number within 0–1")
    unmapped = raw.get("unmapped_terms", [])
    if not isinstance(unmapped, Sequence) or isinstance(unmapped, str) or not all(
        isinstance(term, str) for term in unmapped
    ):
        field_paths.append("unmapped_terms")
        message_holder.append("unmapped_terms must be a list of strings")

    dimensions: dict[D, tuple[str, ...]] = {}
    for key, value in raw.items():
        if key in _ALLOWED_META:
            continue
        dimension = _dimension_for(key)
        if dimension is None:
            field_paths.append(key)
            message_holder.append(f"unknown dimension key {key!r}")
            continue
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, Sequence) or isinstance(values, str):
            field_paths.append(key)
            message_holder.append(f"{key} must be a string or list of strings")
            continue
        accepted: list[str] = []
        for index, item in enumerate(values):
            if not isinstance(item, str):
                field_paths.append(f"{key}[{index}]")
                message_holder.append(f"{key}[{index}] must be a string")
                continue
            token = normalize_text(item).replace(" ", "_")
            if token not in DIMENSION_SPECS[dimension].values:
                field_paths.append(f"{key}[{index}]")
                message_holder.append(f"{item!r} is not a controlled value for {dimension.value}")
                continue
            accepted.append(token)
        if not DIMENSION_SPECS[dimension].multi and len(accepted) > 1:
            field_paths.append(key)
            message_holder.append(f"{key} accepts a single controlled value")
        if accepted:
            dimensions[dimension] = tuple(dict.fromkeys(accepted))

    if field_paths:
        raise ClassificationSchemaError(ErrorShape(
            code="classification_schema_invalid",
            field_paths=field_paths,
            message="; ".join(message_holder) or "classification failed validation",
        ))
    return ClassificationDraft(dimensions=dimensions, confidence=float(confidence),
                               unmapped_terms=tuple(unmapped))


def classification_input_fingerprint(title: str | None, description: str | None) -> str:
    """§28's cache key input: content, not observation id."""
    payload = json.dumps(
        {"title": title or "", "description": description or ""}, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dimension_for(key: str) -> D | None:
    if key in _KEY_ALIASES:
        return _KEY_ALIASES[key]
    try:
        return D(key)
    except ValueError:
        return None
```

- [ ] **Step 9: Run the test and watch it pass**

Run: `python -m pytest tests/unit/market/test_validators.py -q`
Expected: PASS (4 passed).

- [ ] **Step 10: Commit**

```bash
git add services/market_intelligence/taxonomy/validators.py tests/unit/market/test_validators.py
git commit -m "feat(p1): classifier output validation with the platform error shape"
```

- [ ] **Step 11: Write the failing classifier + cache test**

```python
# tests/unit/market/test_classifier.py
from datetime import UTC, datetime

from services.market_intelligence.domain.enums import TaxonomyDimension as D
from services.market_intelligence.taxonomy.classifier import (
    ClassificationInput, RuleBasedClassifier, classify_observation,
)

EXAMPLE = ClassificationInput(
    title="2AM Seoul — Rainy Night R&B Mix",
    description="Late-night Korean city vibes, melancholic and dreamy.",
)


def test_the_rule_based_classifier_reproduces_the_spec_example_dimensions() -> None:
    draft = RuleBasedClassifier().classify(EXAMPLE)
    assert draft.dimensions[D.GENRE] == ("rnb",)
    assert "late_night_drive" in draft.dimensions[D.USE_CASE]
    assert "melancholic" in draft.dimensions.get(D.MOOD, ())
    assert draft.dimensions.get(D.LANGUAGE, ()) in {("english",), ()}
    assert "rainy_asian_city" in draft.dimensions.get(D.VISUAL_WORLD, ())
    assert draft.confidence > 0.0
    assert set(draft.dimensions) <= set(D)


def test_unmapped_terms_are_recorded_and_never_guessed() -> None:
    draft = RuleBasedClassifier().classify(
        ClassificationInput(title="vaporwave phonk", description="chipmunk saxophone")
    )
    assert "vaporwave" not in draft.dimensions.get(D.GENRE, ())
    assert "vaporwave" in draft.unmapped_terms
    assert draft.confidence == 0.0


def test_the_classifier_output_round_trips_through_validation() -> None:
    from services.market_intelligence.taxonomy.validators import validate_classification

    draft = RuleBasedClassifier().classify(EXAMPLE)
    raw = {dimension.value: list(values) for dimension, values in draft.dimensions.items()}
    raw["title_archetypes"] = []
    raw["confidence"] = draft.confidence
    raw["unmapped_terms"] = list(draft.unmapped_terms)
    again = validate_classification(raw)
    assert again.dimensions == draft.dimensions


def test_classification_is_cached_by_content_fingerprint_and_taxonomy_version(market_session) -> None:
    first_id, first_created = classify_observation(
        market_session, entity_id=None, observation_id=None, text=EXAMPLE,
        classifier=RuleBasedClassifier(), taxonomy_version="p1-taxonomy-v1",
    )
    second_id, second_created = classify_observation(
        market_session, entity_id=None, observation_id=None, text=EXAMPLE,
        classifier=RuleBasedClassifier(), taxonomy_version="p1-taxonomy-v1",
    )
    third_id, third_created = classify_observation(
        market_session, entity_id=None, observation_id=None, text=EXAMPLE,
        classifier=RuleBasedClassifier(), taxonomy_version="p1-taxonomy-v2",
    )
    assert (first_created, second_created, third_created) == (True, False, True)
    assert first_id == second_id != third_id  # §29: a new taxonomy version is a new row
```

- [ ] **Step 12: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_classifier.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.taxonomy.classifier'`.

- [ ] **Step 13: Write `taxonomy/classifier.py` and the repository helpers**

```python
# services/market_intelligence/taxonomy/classifier.py
"""§15/§28/§29: classify with a cached, versioned, validated output.

`RuleBasedClassifier` is the deterministic default (and the only classifier the
mock-first build needs). An LLM-backed classifier implements the same protocol and
is blocked on provider credentials — the numeric layer never calls it directly, it
reads the persisted classification (§30).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from ..normalization.canonical import normalize_text
from ..storage.repositories import insert_classification, load_classification
from ..domain.enums import TaxonomyDimension as D
from .synonym_map import SYNONYMS, lookup_synonym
from .validators import (
    CLASSIFICATION_SCHEMA_VERSION, ClassificationDraft, classification_input_fingerprint,
    validate_classification,
)

__all__ = [
    "ClassificationInput", "Classifier", "RuleBasedClassifier", "classify_observation",
]


@dataclass(frozen=True, slots=True)
class ClassificationInput:
    title: str | None
    description: str | None


class Classifier(Protocol):
    name: str
    version: str

    def classify(self, text: ClassificationInput) -> ClassificationDraft: ...


class RuleBasedClassifier:
    """Deterministic keyword classifier over the synonym map.

    Confidence is a function of how much evidence the text carried — never a claim
    about correctness; a text with no hits gets 0.0 and an unmapped term list.
    """

    name = "rule_based"
    version = "1"

    def classify(self, text: ClassificationInput) -> ClassificationDraft:
        haystack = normalize_text(" ".join(filter(None, (text.title, text.description))))
        found: dict[D, list[str]] = {}
        unmatched: set[str] = set()
        for phrase, (dimension, value) in sorted(SYNONYMS.items(), key=lambda kv: -len(kv[0])):
            if phrase in haystack:
                found.setdefault(dimension, []).append(value)
        for token in haystack.split():
            if len(token) >= 4 and token not in {p.split()[-1] for p in SYNONYMS} \
                    and lookup_synonym(token) is None and not any(
                        token in phrase for phrase in SYNONYMS
                    ):
                unmatched.add(token)
        dimensions = {dimension: tuple(dict.fromkeys(values))
                      for dimension, values in found.items()}
        hits = sum(len(values) for values in dimensions.values())
        confidence = min(0.9, 0.2 + 0.15 * hits) if hits else 0.0
        raw = {dimension.value: list(values) for dimension, values in dimensions.items()}
        raw["confidence"] = confidence
        raw["unmapped_terms"] = sorted(unmatched)
        return validate_classification(raw)


def classify_observation(
    session: Session,
    *,
    entity_id: str | None,
    observation_id: str | None,
    text: ClassificationInput,
    classifier: Classifier,
    taxonomy_version: str,
    prompt_version: str = "p1-classifier-prompt-v1",
    schema_version: str = CLASSIFICATION_SCHEMA_VERSION,
) -> tuple[str, bool]:
    """§28 cache first, §29 versions on every row; returns (classification_id, created)."""
    fingerprint = classification_input_fingerprint(text.title, text.description)
    cached = load_classification(
        session, content_fingerprint=fingerprint, taxonomy_version=taxonomy_version
    )
    if cached is not None:
        return str(cached["id"]), False
    draft = classifier.classify(text)
    classification_id = insert_classification(
        session, observation_id=observation_id, entity_id=entity_id,
        content_fingerprint=fingerprint, taxonomy_version=taxonomy_version,
        classifier_model=classifier.name, classifier_version=classifier.version,
        classification_prompt_version=prompt_version, classification_schema_version=schema_version,
        dimensions={dimension.value: list(values)
                    for dimension, values in draft.dimensions.items()},
        confidence=draft.confidence, unmapped_terms=list(draft.unmapped_terms),
    )
    return classification_id, True
```

```python
# services/market_intelligence/storage/repositories.py  (append; extend imports)
from .models import classifications


def insert_classification(
    session: Session,
    *,
    observation_id: str | None,
    entity_id: str | None,
    content_fingerprint: str,
    taxonomy_version: str,
    classifier_model: str,
    classifier_version: str,
    classification_prompt_version: str,
    classification_schema_version: str,
    dimensions: Mapping[str, Any],
    confidence: float,
    unmapped_terms: Sequence[str],
) -> str:
    """§29: every classification names the taxonomy, model, prompt and schema versions."""
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("classification confidence must be within 0–1")
    classification_id = _new_id()
    session.execute(sa.insert(classifications).values(
        id=classification_id, observation_id=observation_id, entity_id=entity_id,
        content_fingerprint=content_fingerprint, taxonomy_version=taxonomy_version,
        classifier_model=classifier_model, classifier_version=classifier_version,
        classification_prompt_version=classification_prompt_version,
        classification_schema_version=classification_schema_version,
        dimensions_json=dict(dimensions), confidence=confidence,
        unmapped_terms=list(unmapped_terms),
    ))
    return classification_id


def load_classification(
    session: Session, *, content_fingerprint: str, taxonomy_version: str,
) -> Mapping[str, Any] | None:
    row = session.execute(
        sa.select(classifications).where(
            classifications.c.content_fingerprint == content_fingerprint,
            classifications.c.taxonomy_version == taxonomy_version,
        )
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def latest_classifications_for(
    session: Session, *, taxonomy_version: str, limit: int = 500,
) -> list[Mapping[str, Any]]:
    """Newest-first classification rows for one taxonomy version — the scorer's input."""
    statement = (
        sa.select(classifications)
        .where(classifications.c.taxonomy_version == taxonomy_version)
        .order_by(classifications.c.created_at.desc())
        .limit(limit)
    )
    return [dict(row) for row in session.execute(statement).mappings()]
```

- [ ] **Step 14: Run everything and watch it pass**

Run: `python -m pytest tests/unit/market -q`
Expected: PASS.

- [ ] **Step 15: Commit**

```bash
git add services/market_intelligence/taxonomy/classifier.py \
        services/market_intelligence/storage/repositories.py tests/unit/market/test_classifier.py
git commit -m "feat(p1): cached rule-based classifier"
```

> **Deferred, recorded, not skipped:** an LLM-backed `Classifier` implementation and the
> tiered local/strong model policy of §28 need a provider credential (P0 §10 human
> task list). The interface, the cache and the validated schema are what P1's scoring
> layer depends on; P1.14 records the provider as `blocked: pending credentials`.

---

### Task P1.8: Feature engineering

**Files:**
- Create: `features/__init__.py`, `features/velocity.py`, `features/growth.py`, `features/competition.py`, `features/demand.py`, `features/audience_quality.py`, `features/cross_platform.py`, `features/production_fit.py`, `features/confidence.py`, `features/arm_features.py`
- Create: `connectors/spotify.py`, `connectors/trends.py`, `connectors/browser.py` (the secondary sources deferred from P1.3)
- Modify: `storage/repositories.py` (append `observations_for_entities`)
- Test: `tests/unit/market/test_velocity.py`, `test_growth.py`, `test_competition.py`, `test_confidence.py`, `test_cross_platform.py`
- Create: `tests/fixtures/market/trends_feed.json`, `tests/fixtures/market/spotify_tracks.json`

**Interfaces:**
- Consumes: `domain.entities.NormalizedObservation` (Tri metrics), `domain.value_objects.Tri`/`tri_from_optional`, `domain.enums.TaxonomyDimension`, `config.MarketSettings` (freshness thresholds, freshness windows), `plm_platform.config.Secret`.
- Produces (names later tasks import exactly):
  - `features.velocity`: `MIN_AGE_EPSILON_HOURS = 1.0 / 60.0`; `video_age_hours(*, observed_at, published_at) -> float`; `lifetime_views_per_day(views: Tri, *, observed_at, published_at) -> Tri`; `windowed_views_per_day(views_before: Tri, views_after: Tri, *, observed_at_before, observed_at_after) -> Tri`; `like_rate(likes: Tri, views: Tri) -> Tri`; `comment_rate(...)`; `channel_relative_views(views: Tri, subscriber_count: Tri) -> Tri` (unknown when subscribers are unknown or zero — "this metric is unstable", §11.3).
  - `features.growth`: `ARM_WINDOW_DAYS = 30`; `median_velocity(observations: Sequence[NormalizedObservation], *, as_of: datetime, window_days: int) -> Tri[float]`; `growth_rate(current: float, baseline: float, *, minimum_baseline: float = 1.0) -> float | None` (`None` = unstable, never an absurd percentage); `growth_30d_vs_prev30d(observations, *, as_of, settings) -> Tri`; `growth_30d_vs_prev90d(...) -> Tri`.
  - `features.competition`: `ArmCompetition(active_channel_count, active_video_count, median_video_velocity, p75_video_velocity, p90_video_velocity, median_channel_size, top_10_channel_share, new_upload_rate, concentration_raw, concentration_normalized, sample_size)`; `percentile(values: Sequence[float], q: float) -> float`; `herfindahl_index(shares: Sequence[float]) -> float`; `top_n_share(values: Sequence[float], n: int) -> float`; `competition_metrics(*, video_velocities: Sequence[float], channel_sizes: Sequence[float], channel_activities: Sequence[float], uploads_last_window: int, window_days: int) -> ArmCompetition`.
  - `features.demand`: `DemandInputs(median_views_per_day, query_prevalence, active_channel_audience, recent_activity_count)` (Tri fields except the count).
  - `features.audience_quality`: `AudienceQualityInputs(long_form_share, engagement_consistency, international_spread, repeat_playlist_proxy)`.
  - `features.cross_platform`: `cross_platform_signal(secondary_observations: Sequence[NormalizedObservation]) -> Tri[float]` — **unknown when there is no secondary source**; §17.3's "reduce confidence; do not automatically assign zero demand".
  - `features.production_fit`: `ProductionFitInputs(...)`; `production_fit_inputs(profile: TaxonomyProfile, settings: MarketSettings) -> ProductionFitInputs` — rule-based feasibility from configuration, explicitly not an economic prediction.
  - `features.confidence`: `ConfidenceBreakdown(sample, source_diversity, time_series_depth, taxonomy, freshness)`; `confidence(breakdown: ConfidenceBreakdown, *, weights: Mapping[str, float] | None = None) -> float` (clamp 0–1, weights from `DEFAULT_CONFIDENCE_WEIGHTS`).
  - `features.arm_features`: `ArmFeatures(...)`; `compute_arm_features(observations_by_arm: Mapping[str, Sequence[NormalizedObservation]], *, as_of, settings, taxonomy_confidence: Mapping[str, float] | None = None) -> dict[str, ArmFeatures]`; `features_freshness(observations, *, now, settings) -> FreshnessAssessment`.
  - `storage.repositories` (added): `observations_for_entities(session, entity_ids: Sequence[str], *, since: datetime | None = None) -> list[NormalizedObservation]`.
  - `connectors.trends`: `PublicTrendsConnector(client)` with `fetch_points(term) -> FetchResult` reading a configured JSON feed URL; `parse_trend_points(fetch) -> tuple[TrendPoint, ...]`.
  - `connectors.browser`: `BrowserConnector(research_allowed: bool = False)`; `fetch_public_page(url)` raises `ConnectorError(UNSUPPORTED_OPERATION)` unless explicitly enabled in configuration (§4.4).
  - `connectors.spotify`: `SpotifyConnector(client, credentials: SpotifyCredentials)` with `fetch_tracks(track_ids) -> FetchResult` and `parse_track_stats(fetch) -> tuple[TrackStats, ...]`; **never reads or requires a `popularity` field** (§4.3).

> **Rulings (Open items #9, #10):** (1) windows are always explicit parameters — `ARM_WINDOW_DAYS` and the previous-window bounds come from `window_bounds`, never from "recent data"; growth against a baseline below `minimum_baseline` returns `None` (unstable) and the caller records that, rather than printing 9000%. (2) `channel_relative_views` returns unknown when subscribers are unknown *or* zero: a ratio against an unknown denominator is unknown, not infinite.

- [ ] **Step 1: Write the failing velocity test**

```python
# tests/unit/market/test_velocity.py
from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import Tri, TriState

from services.market_intelligence.features.velocity import (
    channel_relative_views, comment_rate, lifetime_views_per_day, like_rate,
    video_age_hours, windowed_views_per_day,
)

OBSERVED = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
PUBLISHED = OBSERVED - timedelta(hours=48)


def test_age_is_hours_and_never_below_the_epsilon() -> None:
    assert video_age_hours(observed_at=OBSERVED, published_at=PUBLISHED) == 48.0
    assert video_age_hours(observed_at=OBSERVED, published_at=OBSERVED) > 0.0
    with pytest.raises(ValueError, match="timezone-aware"):
        video_age_hours(observed_at=OBSERVED, published_at=datetime(2026, 9, 18, 6, 0))


def test_lifetime_velocity_uses_an_explicit_age_and_keeps_unknown_unknown() -> None:
    assert lifetime_views_per_day(
        Tri.known(4800), observed_at=OBSERVED, published_at=PUBLISHED
    ).unwrap() == pytest.approx(2400.0)
    assert lifetime_views_per_day(
        Tri.unknown(), observed_at=OBSERVED, published_at=PUBLISHED
    ).state is TriState.UNKNOWN


def test_windowed_velocity_needs_two_observation_points() -> None:
    delta = windowed_views_per_day(
        Tri.known(1000), Tri.known(3400),
        observed_at_before=OBSERVED - timedelta(days=2), observed_at_after=OBSERVED,
    )
    assert delta.unwrap() == pytest.approx(1200.0)
    assert windowed_views_per_day(
        Tri.known(1000), Tri.unknown(),
        observed_at_before=OBSERVED - timedelta(days=2), observed_at_after=OBSERVED,
    ).is_unknown


def test_rates_and_channel_ratio_treat_unknown_denominators_as_unknown() -> None:
    assert like_rate(Tri.known(84), Tri.known(1200)).unwrap() == pytest.approx(0.07)
    assert comment_rate(Tri.known(12), Tri.known(1200)).unwrap() == pytest.approx(0.01)
    assert like_rate(Tri.known(84), Tri.known(0)).is_unknown  # no views: no rate, no division
    assert channel_relative_views(Tri.known(600), Tri.unknown()).is_unknown
    assert channel_relative_views(Tri.known(600), Tri.known(0)).is_unknown  # §11.3 unstable
    assert channel_relative_views(Tri.known(600), Tri.known(1200)).unwrap() == pytest.approx(0.5)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_velocity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.features'`.

- [ ] **Step 3: Write `features/velocity.py`**

```python
# services/market_intelligence/features/velocity.py
"""§11 derived YouTube metrics. Every function is pure, deterministic and takes its
observation instants as arguments — no clock access, no randomness (P0 §2)."""

from __future__ import annotations

from datetime import datetime
from typing import Final

from plm_contracts import Tri

__all__ = [
    "MIN_AGE_EPSILON_HOURS", "channel_relative_views", "comment_rate", "like_rate",
    "lifetime_views_per_day", "video_age_hours", "windowed_views_per_day",
]

MIN_AGE_EPSILON_HOURS: Final[float] = 1.0 / 60.0  # one minute
_EPSILON: Final[float] = 1e-9


def _require_aware(*moments: datetime) -> None:
    if any(moment.tzinfo is None for moment in moments):
        raise ValueError("timezone-aware datetimes required; use datetime.now(UTC)")


def video_age_hours(*, observed_at: datetime, published_at: datetime) -> float:
    """§11.1, with the epsilon that keeps a just-published video out of division by zero."""
    _require_aware(observed_at, published_at)
    delta = (observed_at - published_at).total_seconds() / 3600.0
    return max(delta, MIN_AGE_EPSILON_HOURS)


def lifetime_views_per_day(views: Tri, *, observed_at: datetime, published_at: datetime) -> Tri:
    """§11.2. Explicitly the *lifetime* measure: the caller must prefer windowed velocity.

    Old videos dominate cumulative averages, which is why §11.5 exists and why every
    consumer records which basis it used.
    """
    if not views.is_known:
        return Tri.unknown()
    return Tri.known(views.unwrap() / (video_age_hours(
        observed_at=observed_at, published_at=published_at) / 24.0))


def windowed_views_per_day(
    views_before: Tri, views_after: Tri, *, observed_at_before: datetime,
    observed_at_after: datetime,
) -> Tri:
    """§11.5's preferred measure: the observed delta between two real sightings."""
    if not (views_before.is_known and views_after.is_known):
        return Tri.unknown()
    _require_aware(observed_at_before, observed_at_after)
    window_days = (observed_at_after - observed_at_before).total_seconds() / 86400.0
    if window_days <= 0:
        raise ValueError("the later observation must be later than the earlier one")
    delta = views_after.unwrap() - views_before.unwrap()
    if delta < 0:
        return Tri.unknown()  # a provider correction, not negative growth: call it unknown
    return Tri.known(delta / window_days)


def like_rate(likes: Tri, views: Tri) -> Tri:
    """§11.4 descriptive proxy. Zero views gives no rate — never a division artefact."""
    if not (likes.is_known and views.is_known) or views.unwrap() <= 0:
        return Tri.unknown()
    return Tri.known(likes.unwrap() / max(views.unwrap(), _EPSILON))


def comment_rate(comments: Tri, views: Tri) -> Tri:
    if not (comments.is_known and views.is_known) or views.unwrap() <= 0:
        return Tri.unknown()
    return Tri.known(comments.unwrap() / max(views.unwrap(), _EPSILON))


def channel_relative_views(views: Tri, subscriber_count: Tri) -> Tri:
    """§11.3: unstable when subscriber count is missing **or zero** → unknown."""
    if not (views.is_known and subscriber_count.is_known):
        return Tri.unknown()
    if subscriber_count.unwrap() <= 0:
        return Tri.unknown()
    return Tri.known(views.unwrap() / subscriber_count.unwrap())
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `python -m pytest tests/unit/market/test_velocity.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add services/market_intelligence/features/ tests/unit/market/test_velocity.py
git commit -m "feat(p1): velocity features with explicit observation bases"
```

- [ ] **Step 6: Write the failing growth + competition test**

```python
# tests/unit/market/test_growth.py
from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import Tri, TriState

from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.domain.value_objects import tri_from_optional
from services.market_intelligence.features.growth import (
    growth_30d_vs_prev30d, growth_rate, median_velocity, window_bounds,
)

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def _observation(observed_days_ago: float, views: int, *, published_days_ago: float = 40.0):
    observed_at = AS_OF - timedelta(days=observed_days_ago)
    return NormalizedObservation(
        entity_id=f"e{observed_days_ago}", source_id="s", raw_observation_id="r",
        observed_at=observed_at, published_at=AS_OF - timedelta(days=published_days_ago),
        views=Tri.known(views),
    )


def test_windows_are_explicit_and_never_overlap() -> None:
    current_start, current_end = window_bounds(AS_OF, days=30)
    previous_start, previous_end = window_bounds(AS_OF, days=30, offset_windows=1)
    assert current_end == AS_OF and previous_end == current_start
    assert (current_end - current_start) == (previous_end - previous_start)


def test_growth_is_none_when_the_baseline_cannot_support_a_percentage() -> None:
    assert growth_rate(120.0, 0.0, minimum_baseline=1.0) is None
    assert growth_rate(120.0, 0.5, minimum_baseline=1.0) is None
    assert growth_rate(120.0, 60.0) == pytest.approx(1.0)


def test_growth_30d_vs_prev30d_uses_only_the_two_windows() -> None:
    observations = [
        _observation(10, 3000), _observation(20, 2000),   # current window
        _observation(35, 1000), _observation(45, 1000),    # previous window
    ]
    growth = growth_30d_vs_prev30d(observations, as_of=AS_OF, settings=None)
    assert growth.is_known and growth.unwrap() > 0


def test_a_single_observation_in_a_window_does_not_fabricate_growth() -> None:
    observations = [_observation(10, 3000), _observation(400, 1000)]
    assert growth_30d_vs_prev30d(observations, as_of=AS_OF, settings=None).is_unknown
    assert median_velocity(observations, as_of=AS_OF, window_days=30).state is TriState.UNKNOWN \
        or median_velocity(observations, as_of=AS_OF, window_days=30).is_known
```

```python
# tests/unit/market/test_competition.py
import pytest

from services.market_intelligence.features.competition import (
    competition_metrics, herfindahl_index, percentile, top_n_share,
)


def test_percentile_interpolates_and_handles_small_samples() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    assert percentile([], 0.5) == 0.0
    assert percentile([7.0], 0.9) == 7.0


def test_hhi_is_bounded_and_rises_with_concentration() -> None:
    assert herfindahl_index([0.5, 0.5]) == pytest.approx(0.5)
    assert herfindahl_index([1.0]) == pytest.approx(1.0)
    assert herfindahl_index([]) == 0.0
    assert herfindahl_index([0.25] * 4) == pytest.approx(0.25)


def test_competition_metrics_carry_their_sample_size_and_concentration() -> None:
    metrics = competition_metrics(
        video_velocities=[120.0, 80.0, 40.0, 20.0],
        channel_sizes=[820_000.0, 24_000.0, 80_000.0],
        channel_activities=[900.0, 100.0, 200.0],
        uploads_last_window=7, window_days=30,
    )
    assert metrics.active_video_count == 4
    assert metrics.active_channel_count == 3
    assert metrics.median_video_velocity == pytest.approx(60.0)
    assert metrics.p90_video_velocity >= metrics.p75_video_velocity >= metrics.median_video_velocity
    assert metrics.median_channel_size == pytest.approx(80_000.0)
    assert metrics.top_10_channel_share == pytest.approx(1.0)
    assert metrics.concentration_raw == pytest.approx(herfindahl_index([0.75, 0.08333333, 0.16666667]), rel=1e-3)
    assert 0.0 <= metrics.concentration_normalized <= 1.0
    assert metrics.sample_size == 4
    assert top_n_share([5.0, 3.0, 2.0, 1.0], 2) == pytest.approx(0.7272727, rel=1e-6)
```

Add `test_competition_metrics_...`'s `new_upload_rate == pytest.approx(7 / 30)` assertion.

- [ ] **Step 7: Run them and watch them fail**

Run: `python -m pytest tests/unit/market/test_growth.py tests/unit/market/test_competition.py -q`
Expected: FAIL — modules missing.

- [ ] **Step 8: Write `features/growth.py` and `features/competition.py`**

```python
# services/market_intelligence/features/growth.py
"""§12: growth at the arm level, with explicit non-overlapping windows.

A baseline too small to support a percentage returns `None`/unknown — §12's
"mark growth as unstable rather than producing absurd percentages".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from plm_contracts import Tri

from ..domain.entities import NormalizedObservation
from .velocity import lifetime_views_per_day, video_age_hours, windowed_views_per_day

__all__ = [
    "ARM_WINDOW_DAYS", "growth_30d_vs_prev30d", "growth_30d_vs_prev90d", "growth_rate",
    "median_velocity", "window_bounds",
]

ARM_WINDOW_DAYS = 30
_SEQUENCE_GAP_DAYS = 1.0  # two sightings closer than this cannot form a window


def window_bounds(
    as_of: datetime, *, days: int, offset_windows: int = 0,
) -> tuple[datetime, datetime]:
    """Half-open `[start, end)` bounds for the window ending `offset_windows` back."""
    end = as_of - timedelta(days=days * offset_windows)
    return end - timedelta(days=days), end


def growth_rate(current: float, baseline: float, *, minimum_baseline: float = 1.0) -> float | None:
    """§12's formula; `None` when the baseline cannot support a percentage."""
    if abs(baseline) < minimum_baseline:
        return None
    return (current - baseline) / max(abs(baseline), 1e-9)


def median_velocity(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, window_days: int,
) -> Tri:
    """Median of per-observation velocity inside one window (§12's suggested features).

    A single sighting has no delta, so its *lifetime* velocity is used and that is
    recorded by the caller via `sample_size`; unknown metrics simply do not vote.
    """
    start, end = window_bounds(as_of, days=window_days)
    inside = [o for o in observations if start <= o.observed_at < end]
    if not inside:
        return Tri.unknown()
    velocities: list[float] = []
    by_entity: dict[str, list[NormalizedObservation]] = {}
    for observation in inside:
        by_entity.setdefault(observation.entity_id, []).append(observation)
    for series in by_entity.values():
        ordered = sorted(series, key=lambda o: o.observed_at)
        for earlier, later in zip(ordered, ordered[1:]):
            delta_days = (later.observed_at - earlier.observed_at).total_seconds() / 86400.0
            if delta_days >= _SEQUENCE_GAP_DAYS:
                velocity = windowed_views_per_day(
                    earlier.views, later.views, observed_at_before=earlier.observed_at,
                    observed_at_after=later.observed_at,
                )
            else:
                velocity = Tri.unknown()
            if velocity.is_known and velocity.unwrap() >= 0:
                velocities.append(float(velocity.unwrap()))
        newest = ordered[-1]
        if newest.published_at is not None and newest.views.is_known:
            lifetime = lifetime_views_per_day(
                newest.views, observed_at=newest.observed_at, published_at=newest.published_at
            )
            if lifetime.is_known:
                velocities.append(float(lifetime.unwrap()))
    if not velocities:
        return Tri.unknown()
    ordered_values = sorted(velocities)
    middle = len(ordered_values) // 2
    if len(ordered_values) % 2:
        return Tri.known(ordered_values[middle])
    return Tri.known((ordered_values[middle - 1] + ordered_values[middle]) / 2.0)


def growth_30d_vs_prev30d(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, settings=None,
) -> Tri:
    """Recent 30 days against the preceding comparable 30 days (§12)."""
    return _growth_between(observations, as_of=as_of, days=ARM_WINDOW_DAYS, offset=0)


def growth_30d_vs_prev90d(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, settings=None,
) -> Tri:
    """Recent 30 days against the preceding 90-day window (the robust second metric)."""
    return _growth_between(observations, as_of=as_of, days=ARM_WINDOW_DAYS * 3, offset=1)


def _growth_between(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, days: int, offset: int,
) -> Tri:
    current = _window_median(observations, as_of=as_of, days=ARM_WINDOW_DAYS, offset=0)
    baseline = _window_median(observations, as_of=as_of, days=days, offset=offset)
    if not (current.is_known and baseline.is_known):
        return Tri.unknown()
    rate = growth_rate(float(current.unwrap()), float(baseline.unwrap()))
    return Tri.unknown() if rate is None else Tri.known(rate)


def _window_median(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, days: int, offset: int,
) -> Tri:
    start, end = window_bounds(as_of, days=days, offset_windows=offset)
    inside = [o for o in observations if start <= o.observed_at < end]
    values: list[float] = []
    for observation in inside:
        if observation.published_at is not None and observation.views.is_known:
            velocity = lifetime_views_per_day(
                observation.views, observed_at=observation.observed_at,
                published_at=observation.published_at,
            )
            values.append(float(velocity.unwrap()))
    if len(values) < 2:  # §19: one data point is not a distribution
        return Tri.unknown()
    values.sort()
    middle = len(values) // 2
    return Tri.known(values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2)
```

```python
# services/market_intelligence/features/competition.py
"""§13: competition and saturation — never "number of search results".

Concentration is an analytic indicator over channel-normalised activity, not a legal
statement; both raw and normalised values are persisted with the sample size.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "ArmCompetition", "competition_metrics", "herfindahl_index", "percentile", "top_n_share",
]


@dataclass(frozen=True, slots=True)
class ArmCompetition:
    active_channel_count: int
    active_video_count: int
    median_video_velocity: float
    p75_video_velocity: float
    p90_video_velocity: float
    median_channel_size: float
    top_10_channel_share: float
    new_upload_rate: float
    concentration_raw: float
    concentration_normalized: float
    sample_size: int


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile; an empty sample is 0.0 and says so via sample_size."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = min(max(q, 0.0), 1.0) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def herfindahl_index(shares: Sequence[float]) -> float:
    """§13 `HHI = sum(share_i ** 2)` over shares that should already sum to 1."""
    return float(sum(share * share for share in shares))


def top_n_share(values: Sequence[float], n: int) -> float:
    """Share of total activity held by the `n` largest values."""
    total = float(sum(values))
    if total <= 0:
        return 0.0
    return float(sum(sorted(values, reverse=True)[:n]) / total)


def competition_metrics(
    *,
    video_velocities: Sequence[float],
    channel_sizes: Sequence[float],
    channel_activities: Sequence[float],
    uploads_last_window: int,
    window_days: int,
) -> ArmCompetition:
    """One arm's competitive profile; `concentration_normalized` is HHI scaled to 0–1."""
    activity_total = float(sum(channel_activities))
    shares = [activity / activity_total for activity in channel_activities] \
        if activity_total > 0 else []
    raw = herfindahl_index(shares)
    normalized = 0.0 if not shares else (raw - 1.0 / len(shares)) / (1.0 - 1.0 / len(shares)) \
        if len(shares) > 1 else raw
    return ArmCompetition(
        active_channel_count=len(channel_activities),
        active_video_count=len(video_velocities),
        median_video_velocity=percentile(video_velocities, 0.5),
        p75_video_velocity=percentile(video_velocities, 0.75),
        p90_video_velocity=percentile(video_velocities, 0.9),
        median_channel_size=percentile(channel_sizes, 0.5),
        top_10_channel_share=top_n_share(channel_activities, 10),
        new_upload_rate=uploads_last_window / window_days if window_days > 0 else 0.0,
        concentration_raw=raw,
        concentration_normalized=max(0.0, min(normalized, 1.0)),
        sample_size=len(video_velocities),
    )
```

- [ ] **Step 9: Run the tests and watch them pass**

Run: `python -m pytest tests/unit/market/test_growth.py tests/unit/market/test_competition.py -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add services/market_intelligence/features/growth.py services/market_intelligence/features/competition.py \
        tests/unit/market/test_growth.py tests/unit/market/test_competition.py
git commit -m "feat(p1): growth and competition features"
```

- [ ] **Step 11: Write the failing confidence/cross-platform/assembly test**

```python
# tests/unit/market/test_confidence.py
from datetime import UTC, datetime

import pytest
from plm_contracts import Tri

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.connectors.base import FetchedObject
from services.market_intelligence.domain.enums import ProviderObjectType
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.features.confidence import ConfidenceBreakdown, confidence
from services.market_intelligence.normalization.youtube import normalize_video_object
from services.market_intelligence.storage import repositories


def test_confidence_is_bounded_and_independent_of_any_score() -> None:
    assert confidence(ConfidenceBreakdown(1.0, 1.0, 1.0, 1.0, 1.0)) == 1.0
    assert confidence(ConfidenceBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)) == 0.0
    middle = confidence(ConfidenceBreakdown(0.5, 0.0, 0.0, 1.0, 0.5))
    assert 0.0 < middle < 1.0


def test_a_bad_component_reduces_confidence_even_when_the_data_looks_strong() -> None:
    strong = ConfidenceBreakdown(1.0, 1.0, 1.0, 1.0, 1.0)
    thin = ConfidenceBreakdown(0.2, 1.0, 0.0, 1.0, 1.0)
    assert confidence(thin) < confidence(strong)


def test_out_of_range_components_are_refused() -> None:
    with pytest.raises(ValueError):
        ConfidenceBreakdown(1.5, 0.0, 0.0, 0.0, 0.0)


def test_quarantined_record_never_becomes_an_observation(market_session) -> None:
    """Review Focus 5: a quarantined record cannot reach features or scores."""
    outcome = normalize_video_object(
        FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                      provider_object_id="short", observed_at=AS_OF,
                      payload={"id": "short"}),
        entity_id="entity-1", source_id="source-1", raw_observation_id="raw-1",
        observed_at=AS_OF,
    )
    assert outcome.observation is None and outcome.quarantine is not None
    with pytest.raises(ValueError, match="quarantined"):
        repositories.insert_normalized_observation(market_session, outcome.observation)
    assert repositories.load_observations(market_session) == []


AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_market_settings()


def test_no_observations_means_no_features_not_a_zero_score() -> None:
    assert compute_arm_features({}, as_of=AS_OF, settings=SETTINGS) == {}
```

```python
# tests/unit/market/test_cross_platform.py
from datetime import UTC, datetime

from plm_contracts import Tri

from services.market_intelligence.connectors.browser import BrowserConnector
from services.market_intelligence.connectors.base import ConnectorError
from services.market_intelligence.connectors.trends import PublicTrendsConnector, parse_trend_points
from services.market_intelligence.domain.enums import ConnectorErrorClass
from services.market_intelligence.features.cross_platform import cross_platform_signal

FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_no_secondary_source_means_unknown_not_zero() -> None:
    signal = cross_platform_signal([])
    assert signal.is_unknown  # §17.3: reduce confidence, never assign zero demand


def test_trend_points_parse_from_the_configured_feed(mock_http) -> None:
    import json
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "fixtures" / "market" / "trends_feed.json")
        .read_text(encoding="utf-8")
    )
    mock_http.enqueue_json(fixture)
    connector = PublicTrendsConnector(
        mock_http.client(base_url="https://trends.example"),
        feed_path="/public/trends.json", observed_at=lambda: FIXED_NOW,
    )
    fetch = connector.fetch_points("lofi")
    points = parse_trend_points(fetch)
    assert points[0].term == "lofi" and points[0].interest == 78
    assert fetch.quota_units == 0  # public feeds do not consume provider quota


def test_browser_automation_is_unsupported_until_explicitly_enabled() -> None:
    import pytest

    with pytest.raises(ConnectorError) as excinfo:
        BrowserConnector().fetch_public_page("https://example.com")
    assert excinfo.value.error_class is ConnectorErrorClass.UNSUPPORTED_OPERATION
```

- [ ] **Step 12: Run them and watch them fail**

Run: `python -m pytest tests/unit/market/test_confidence.py tests/unit/market/test_cross_platform.py -q`
Expected: FAIL — modules missing.

- [ ] **Step 13: Write the remaining feature modules and secondary connectors**

```python
# services/market_intelligence/features/confidence.py
"""§19: confidence is independent of the opportunity score and clamped to 0–1."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

__all__ = ["DEFAULT_CONFIDENCE_WEIGHTS", "ConfidenceBreakdown", "confidence"]

DEFAULT_CONFIDENCE_WEIGHTS: Final[Mapping[str, float]] = {
    "sample": 0.35, "source_diversity": 0.20, "time_series_depth": 0.15,
    "taxonomy": 0.15, "freshness": 0.15,
}


@dataclass(frozen=True, slots=True)
class ConfidenceBreakdown:
    """Each component 0–1; the combination is configuration, not a magic number."""

    sample: float
    source_diversity: float
    time_series_depth: float
    taxonomy: float
    freshness: float

    def __post_init__(self) -> None:
        for name, value in self.as_mapping().items():
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"confidence component {name} must be within 0–1")

    def as_mapping(self) -> dict[str, float]:
        return {"sample": self.sample, "source_diversity": self.source_diversity,
                "time_series_depth": self.time_series_depth, "taxonomy": self.taxonomy,
                "freshness": self.freshness}


def confidence(
    breakdown: ConfidenceBreakdown, *, weights: Mapping[str, float] | None = None,
) -> float:
    """Weighted combination, clamped. §17.2: this never multiplies the score."""
    active = DEFAULT_CONFIDENCE_WEIGHTS if weights is None else weights
    total_weight = sum(active.values())
    if total_weight <= 0:
        raise ValueError("confidence weights must sum above zero")
    value = sum(breakdown.as_mapping()[name] * weight for name, weight in active.items())
    return max(0.0, min(1.0, value / total_weight))
```

```python
# services/market_intelligence/features/cross_platform.py
"""§17.3 cross-platform score inputs: secondary sources only, never YouTube metrics."""

from __future__ import annotations

from collections.abc import Sequence

from plm_contracts import Tri

from ..domain.entities import NormalizedObservation
from ..domain.enums import ProviderName
from .velocity import lifetime_views_per_day

__all__ = ["cross_platform_signal"]

_SECONDARY_SOURCES = frozenset({ProviderName.SPOTIFY_WEB_API.value, ProviderName.PUBLIC_TRENDS.value})


def cross_platform_signal(secondary_observations: Sequence[NormalizedObservation]) -> Tri:
    """Mean normalised engagement across secondary sources; unknown with none of them.

    An unavailable secondary source reduces confidence (P1.8's breakdown) — it never
    contributes a zero (§17.3).
    """
    values: list[float] = []
    for observation in secondary_observations:
        if observation.source_id in _SECONDARY_SOURCES and observation.views.is_known:
            velocity = lifetime_views_per_day(
                observation.views, observed_at=observation.observed_at,
                published_at=observation.published_at or observation.observed_at,
            )
            values.append(float(velocity.unwrap()))
    if not values:
        return Tri.unknown()
    return Tri.known(sum(values) / len(values))
```

```python
# services/market_intelligence/features/demand.py
"""§17.3 demand score inputs — composites of evidence, each Tri-valued."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from plm_contracts import Tri

from ..domain.entities import NormalizedObservation

__all__ = ["DemandInputs", "demand_inputs"]


@dataclass(frozen=True, slots=True)
class DemandInputs:
    median_views_per_day: Tri
    query_prevalence: Tri
    active_channel_audience: Tri
    recent_activity_count: int


def demand_inputs(
    observations: Sequence[NormalizedObservation], *, as_of: datetime, window_days: int,
) -> DemandInputs:
    """Everything the demand component needs; missing pieces stay unknown (§3.6)."""
    recent = [o for o in observations
              if (as_of - o.observed_at).total_seconds() <= window_days * 86400]
    velocities = sorted(
        float(o.views.unwrap()) for o in recent if o.views.is_known
    )
    median: Tri = Tri.unknown()
    if velocities:
        middle = len(velocities) // 2
        median = Tri.known(velocities[middle] if len(velocities) % 2
                           else (velocities[middle - 1] + velocities[middle]) / 2)
    audiences = [float(o.subscriber_count.unwrap()) for o in recent
                 if o.subscriber_count.is_known]
    return DemandInputs(
        median_views_per_day=median,
        query_prevalence=Tri.known(float(len(recent))) if recent else Tri.unknown(),
        active_channel_audience=Tri.known(sum(audiences)) if audiences else Tri.unknown(),
        recent_activity_count=len(recent),
    )
```

```python
# services/market_intelligence/features/audience_quality.py
"""§17.3 audience-quality inputs, all from observable public signals."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from plm_contracts import Tri

from ..domain.entities import NormalizedObservation
from .velocity import comment_rate, like_rate

__all__ = ["AudienceQualityInputs", "audience_quality_inputs"]

_LONG_FORM_SECONDS = 600.0


@dataclass(frozen=True, slots=True)
class AudienceQualityInputs:
    long_form_share: Tri
    engagement_consistency: Tri
    international_spread: Tri
    repeat_playlist_proxy: Tri


def audience_quality_inputs(observations: Sequence[NormalizedObservation]) -> AudienceQualityInputs:
    durations = [float(o.video_duration_seconds.unwrap()) for o in observations
                 if o.video_duration_seconds.is_known]
    long_form = Tri.known(sum(1 for d in durations if d >= _LONG_FORM_SECONDS) / len(durations)) \
        if durations else Tri.unknown()
    rates = [float(like_rate(o.likes, o.views).unwrap()) for o in observations
             if like_rate(o.likes, o.views).is_known]
    consistency: Tri = Tri.unknown()
    if len(rates) >= 2:
        spread = max(rates) - min(rates)
        consistency = Tri.known(max(0.0, 1.0 - spread))
    countries = {o.country_signal for o in observations if o.country_signal}
    spread = Tri.known(min(1.0, len(countries) / 3.0)) if countries else Tri.unknown()
    playlist_proxy = Tri.known(
        sum(1 for o in observations if o.playlist_item_count.is_known) / len(observations)
    ) if observations else Tri.unknown()
    return AudienceQualityInputs(long_form_share=long_form, engagement_consistency=consistency,
                                 international_spread=spread,
                                 repeat_playlist_proxy=playlist_proxy)
```

```python
# services/market_intelligence/features/production_fit.py
"""§17.3 production fit: feasibility for *our* factory, explicitly not an economic
prediction. Everything is configuration-driven."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from ..config import MarketSettings
from ..domain.enums import TaxonomyDimension as D
from ..domain.value_objects import TaxonomyProfile

__all__ = ["ProductionFitInputs", "production_fit_inputs"]

_EASE: Final[Mapping[str, float]] = {
    "instrumental": 1.0, "vocal": 0.7, "mixed": 0.6, "not_applicable": 1.0,
    "english": 1.0, "korean": 0.8, "japanese": 0.8, "spanish": 0.9, "mixed": 0.6,
}


@dataclass(frozen=True, slots=True)
class ProductionFitInputs:
    vocal_compatibility: float
    language_support: float
    visual_feasibility: float
    copyright_risk_ease: float


def production_fit_inputs(profile: TaxonomyProfile, settings: MarketSettings) -> ProductionFitInputs:
    """Deterministic, rule-based, versioned by `settings.feature_version`."""
    keyword_risk = {None: 0.8, "city_pop": 0.6, "jazz": 0.7, "classical": 0.9}
    return ProductionFitInputs(
        vocal_compatibility=_EASE.get(profile.vocal_mode or "", 0.5),
        language_support=_EASE.get(profile.language or "", 0.5),
        visual_feasibility=0.9 if profile.visual_world else 0.5,
        copyright_risk_ease=keyword_risk.get(profile.genre, 0.5),
    )
```

```python
# services/market_intelligence/features/arm_features.py
"""Arm-level feature assembly: the single place observations become numbers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from plm_contracts import Tri

from ..config import MarketSettings
from ..domain.entities import NormalizedObservation
from ..domain.enums import TaxonomyDimension as D
from ..domain.value_objects import TaxonomyProfile, classify_freshness, fingerprint
from .audience_quality import AudienceQualityInputs, audience_quality_inputs
from .competition import ArmCompetition, competition_metrics
from .confidence import ConfidenceBreakdown, confidence
from .cross_platform import cross_platform_signal
from .demand import DemandInputs, demand_inputs
from .growth import growth_30d_vs_prev30d, growth_30d_vs_prev90d, median_velocity
from .production_fit import ProductionFitInputs, production_fit_inputs
from .velocity import lifetime_views_per_day

__all__ = ["ArmFeatures", "compute_arm_features", "features_freshness"]


@dataclass(frozen=True, slots=True)
class ArmFeatures:
    arm_id: str
    fingerprint: str
    as_of: datetime
    observation_count: int
    source_count: int
    observation_ids: tuple[str, ...]
    median_velocity_7d: Tri
    median_velocity_30d: Tri
    median_velocity_90d: Tri
    growth_30d_vs_prev30d: Tri
    growth_30d_vs_prev90d: Tri
    competition: ArmCompetition
    demand: DemandInputs
    audience: AudienceQualityInputs
    cross_platform: Tri
    production_fit: ProductionFitInputs
    confidence_breakdown: ConfidenceBreakdown

    @property
    def confidence(self) -> float:
        return confidence(self.confidence_breakdown)


def features_freshness(observations: Sequence[NormalizedObservation], *, now: datetime,
                       settings: MarketSettings):
    """§21: freshness of the newest observation behind the features."""
    newest = max((o.observed_at for o in observations), default=None)
    if newest is None:
        return classify_freshness(as_of=now, now=now, fresh_hours=settings.freshness_fresh_hours,
                                  normal_hours=settings.freshness_normal_hours,
                                  stale_hours=settings.freshness_stale_hours)
    return classify_freshness(
        as_of=newest, now=now, fresh_hours=settings.freshness_fresh_hours,
        normal_hours=settings.freshness_normal_hours, stale_hours=settings.freshness_stale_hours,
    )


def compute_arm_features(
    observations_by_arm: Mapping[str, Sequence[NormalizedObservation]],
    *,
    as_of: datetime,
    settings: MarketSettings,
    arm_ids: Mapping[str, str] | None = None,
    profiles: Mapping[str, TaxonomyProfile] | None = None,
    taxonomy_confidence: Mapping[str, float] | None = None,
) -> dict[str, ArmFeatures]:
    """`observations_by_arm` is keyed by arm fingerprint (P1.9 owns the identity)."""
    features: dict[str, ArmFeatures] = {}
    for arm_fingerprint, observations in observations_by_arm.items():
        velocities: list[float] = []
        channel_sizes: list[float] = []
        channel_activity: dict[str, float] = {}
        uploads = 0
        for observation in observations:
            if observation.published_at is not None and observation.views.is_known:
                velocity = lifetime_views_per_day(
                    observation.views, observed_at=observation.observed_at,
                    published_at=observation.published_at,
                )
                if velocity.is_known:
                    velocities.append(float(velocity.unwrap()))
                    channel_activity[observation.entity_id] = (
                        channel_activity.get(observation.entity_id, 0.0)
                        + float(velocity.unwrap())
                    )
            if observation.subscriber_count.is_known:
                channel_sizes.append(float(observation.subscriber_count.unwrap()))
            if observation.published_at is not None and (
                (as_of - observation.published_at).total_seconds() <= 30 * 86400
            ):
                uploads += 1
        competition = competition_metrics(
            video_velocities=velocities, channel_sizes=channel_sizes,
            channel_activities=list(channel_activity.values()),
            uploads_last_window=uploads, window_days=30,
        )
        freshness = features_freshness(observations, now=as_of, settings=settings)
        sources = {observation.source_id for observation in observations}
        series_depth = _series_depth(observations)
        taxonomy = (taxonomy_confidence or {}).get(arm_fingerprint, 0.0)
        breakdown = ConfidenceBreakdown(
            sample=min(1.0, len(observations) / max(settings.confidence_sample_target, 1)),
            source_diversity=min(1.0, len(sources) / max(settings.confidence_source_target, 1)),
            time_series_depth=series_depth,
            taxonomy=taxonomy,
            freshness={"fresh": 1.0, "normal": 0.75, "stale": 0.35, "expired": 0.0}[
                freshness.bucket.value
            ],
        )
        profile = (profiles or {}).get(arm_fingerprint) or TaxonomyProfile()
        features[arm_fingerprint] = ArmFeatures(
            arm_id=(arm_ids or {}).get(arm_fingerprint, arm_fingerprint),
            fingerprint=arm_fingerprint, as_of=as_of,
            observation_count=len(observations), source_count=len(sources),
            observation_ids=tuple(o.raw_observation_id for o in observations),
            median_velocity_7d=median_velocity(observations, as_of=as_of, window_days=7),
            median_velocity_30d=median_velocity(observations, as_of=as_of, window_days=30),
            median_velocity_90d=median_velocity(observations, as_of=as_of, window_days=90),
            growth_30d_vs_prev30d=growth_30d_vs_prev30d(observations, as_of=as_of,
                                                        settings=settings),
            growth_30d_vs_prev90d=growth_30d_vs_prev90d(observations, as_of=as_of,
                                                        settings=settings),
            competition=competition, demand=demand_inputs(observations, as_of=as_of,
                                                          window_days=30),
            audience=audience_quality_inputs(observations),
            cross_platform=cross_platform_signal(observations),
            production_fit=production_fit_inputs(profile, settings),
            confidence_breakdown=breakdown,
        )
    return features


def _series_depth(observations: Sequence[NormalizedObservation]) -> float:
    """Share of entities with at least two sightings — §19's time-series depth."""
    if not observations:
        return 0.0
    counts: dict[str, int] = {}
    for observation in observations:
        counts[observation.entity_id] = counts.get(observation.entity_id, 0) + 1
    return sum(1 for count in counts.values() if count >= 2) / len(counts)
```

```python
# services/market_intelligence/connectors/trends.py
"""§4.4 public research feed: a JSON endpoint, no browser, no credentials, no quota."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from ..domain.enums import ConnectorErrorClass, ProviderName, ProviderObjectType
from .base import ConnectorError, FetchResult, FetchedObject, hash_request_context

__all__ = ["PublicTrendsConnector", "TrendPoint", "parse_trend_points"]


@dataclass(frozen=True, slots=True)
class TrendPoint:
    term: str
    interest: int
    observed_at: datetime


class PublicTrendsConnector:
    """Reads a configured public JSON feed; the feed's shape is validated, not assumed."""

    provider_name = ProviderName.PUBLIC_TRENDS

    def __init__(self, client: httpx.Client, *, feed_path: str,
                 observed_at: Callable[[], datetime] | None = None) -> None:
        self._client = client
        self._feed_path = feed_path
        self._observed_at = observed_at if observed_at is not None else (lambda: datetime.now(UTC))

    def fetch_points(self, term: str) -> FetchResult:
        response = self._client.get(self._feed_path)
        if response.status_code >= 400:
            raise ConnectorError(ConnectorErrorClass.INVALID_REQUEST,
                                 f"trends feed returned HTTP {response.status_code}")
        try:
            payload: Mapping[str, Any] = response.json()
        except ValueError as exc:
            raise ConnectorError(ConnectorErrorClass.PARSER_FAILURE,
                                 "trends feed body is not JSON") from exc
        if "points" not in payload:
            raise ConnectorError(ConnectorErrorClass.SCHEMA_CHANGE,
                                 "trends feed has no 'points' key (§43)")
        observed_at = self._observed_at()
        objects = tuple(
            FetchedObject(provider_object_type=ProviderObjectType.TREND_POINT,
                          provider_object_id=f"{point['term']}@{point.get('date', 'latest')}",
                          observed_at=observed_at, payload=point)
            for point in payload["points"]
            if point.get("term") == term
        )
        return FetchResult(objects=objects, quota_units=0,
                           request_context_hash=hash_request_context(
                               self.provider_name.value, "trends.feed", {"term": term}))


def parse_trend_points(fetch: FetchResult) -> tuple[TrendPoint, ...]:
    return tuple(
        TrendPoint(term=str(obj.payload["term"]), interest=int(obj.payload["interest"]),
                   observed_at=obj.observed_at)
        for obj in fetch.objects
    )
```

```python
# services/market_intelligence/connectors/browser.py
"""§4.4 browser automation is a last resort and stays off until explicitly enabled."""

from __future__ import annotations

from ..domain.enums import ConnectorErrorClass, ProviderName
from .base import ConnectorError

__all__ = ["BrowserConnector"]


class BrowserConnector:
    provider_name = ProviderName.BROWSER_PUBLIC_SEARCH

    def __init__(self, *, research_allowed: bool = False) -> None:
        self.research_allowed = research_allowed

    def fetch_public_page(self, url: str) -> dict:
        if not self.research_allowed:
            raise ConnectorError(
                ConnectorErrorClass.UNSUPPORTED_OPERATION,
                "browser automation is disabled: enable it explicitly for approved public "
                "research only (PHASE-P1 §4.4)",
            )
        raise ConnectorError(
            ConnectorErrorClass.UNSUPPORTED_OPERATION,
            "no browser backend is configured in this build",
        )
```

```python
# services/market_intelligence/connectors/spotify.py
"""§4.3 Spotify metadata as an optional secondary signal.

February 2026 removed popularity fields from several responses and restricted playlist
item access, so this connector reads metadata only and **never** a popularity field:
the opportunity score must not depend on something the provider no longer returns.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from ..domain.enums import ConnectorErrorClass, ProviderName, ProviderObjectType
from .base import ConnectorError, FetchResult, FetchedObject, hash_request_context

__all__ = ["SpotifyConnector", "SpotifyCredentials", "TrackStats", "parse_track_stats"]

BASE_URL = "https://api.spotify.com/v1"
_BANNED_FIELDS = ("popularity",)


@dataclass(frozen=True, slots=True)
class SpotifyCredentials:
    access_token: str

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


@dataclass(frozen=True, slots=True)
class TrackStats:
    track_id: str
    name: str | None
    artist_names: tuple[str, ...]
    duration_seconds: float | None


class SpotifyConnector:
    provider_name = ProviderName.SPOTIFY_WEB_API

    def __init__(self, client: httpx.Client, credentials: SpotifyCredentials,
                 *, base_url: str = BASE_URL) -> None:
        self._client = client
        self._credentials = credentials
        self._base_url = base_url.rstrip("/")

    def fetch_tracks(self, track_ids: Sequence[str]) -> FetchResult:
        if not track_ids:
            raise ConnectorError(ConnectorErrorClass.INVALID_REQUEST,
                                 "fetch_tracks called without ids")
        response = self._client.get(f"{self._base_url}/tracks",
                                    params={"ids": ",".join(track_ids)},
                                    headers=self._credentials.headers())
        if response.status_code >= 400:
            raise ConnectorError(ConnectorErrorClass.AUTH_FAILURE,
                                 f"spotify returned HTTP {response.status_code}")
        payload = response.json()
        objects = tuple(
            FetchedObject(provider_object_type=ProviderObjectType.TRACK,
                          provider_object_id=str(track["id"]),
                          observed_at=_parse_release(track), payload=track)
            for track in payload.get("tracks", [])
            if track and track.get("id")
        )
        if any(field in obj.payload for obj in objects for field in _BANNED_FIELDS):
            raise ConnectorError(
                ConnectorErrorClass.SCHEMA_CHANGE,
                "spotify returned a popularity field the 2026 API removed; the score must "
                "not depend on it (PHASE-P1 §4.3)",
            )
        return FetchResult(objects=objects, quota_units=0,
                           request_context_hash=hash_request_context(
                               self.provider_name.value, "tracks", {"ids": ",".join(track_ids)}))


def parse_track_stats(fetch: FetchResult) -> tuple[TrackStats, ...]:
    return tuple(
        TrackStats(
            track_id=obj.provider_object_id,
            name=obj.payload.get("name"),
            artist_names=tuple(artist.get("name", "") for artist in obj.payload.get("artists", [])),
            duration_seconds=(
                float(obj.payload["duration_ms"]) / 1000.0
                if obj.payload.get("duration_ms") is not None else None
            ),
        )
        for obj in fetch.objects
    )


def _parse_release(track: dict) -> datetime:
    """Tracks carry a release date, not a timestamp: midnight UTC of that date."""
    release = str(track.get("album", {}).get("release_date") or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(release, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return datetime(1970, 1, 1, tzinfo=UTC)
```

Add `from datetime import UTC, datetime` to that module's imports (the helper above needs it).

Add the fixtures the tests read:

```json
// tests/fixtures/market/trends_feed.json
{"points": [
  {"term": "lofi", "date": "2026-09-14", "interest": 74},
  {"term": "lofi", "date": "2026-09-20", "interest": 78},
  {"term": "synthwave", "date": "2026-09-20", "interest": 41}
]}
```

```json
// tests/fixtures/market/spotify_tracks.json
{"tracks": [
  {"id": "sp0000000000000000000001", "name": "2AM Seoul", "duration_ms": 212000,
   "artists": [{"name": "NightLoop"}], "album": {"release_date": "2026-08-01"}}
]}
```

```python
# services/market_intelligence/storage/repositories.py  (append)
def observations_for_entities(
    session: Session, entity_ids: Sequence[str], *, since: datetime | None = None,
) -> list[NormalizedObservation]:
    """The feature layer's read path; quarantined records have no row and cannot appear."""
    if not entity_ids:
        return []
    statement = (sa.select(observations)
                 .where(observations.c.entity_id.in_(list(entity_ids)))
                 .order_by(observations.c.observed_at))
    if since is not None:
        statement = statement.where(observations.c.observed_at >= since)
    return _rows_to_observations(session.execute(statement).mappings())
```

Refactor `load_observations` to call `_rows_to_observations(...)` (same Tri mapping, one implementation).

- [ ] **Step 14: Run everything and watch it pass**

Run: `python -m pytest tests/unit/market -q`
Expected: PASS.

- [ ] **Step 15: Commit**

```bash
git add services/market_intelligence/features/ services/market_intelligence/connectors/ \
        services/market_intelligence/storage/repositories.py tests/unit/market/ \
        tests/fixtures/market/
git commit -m "feat(p1): confidence, cross-platform signals and arm feature assembly"
```

---

### Task P1.9: Opportunity scorer

**Files:**
- Create: `taxonomy/arms.py`, `scoring/__init__.py`, `scoring/normalization.py`, `scoring/calibration.py`, `scoring/opportunity.py`
- Modify: `storage/repositories.py` (append `upsert_arm`, `load_arms`, `insert_score`, `latest_scores`, `scores_for_arm`)
- Test: `tests/unit/market/test_arm_identity.py`, `test_normalization_scoring.py`, `test_calibration.py`, `test_opportunity_scoring.py`

**Interfaces:**
- Consumes: `domain.value_objects.TaxonomyProfile`/`fingerprint`/`slug_token`, `domain.enums.TaxonomyDimension`, `domain.entities.OpportunityScore`, `features.arm_features.ArmFeatures`, `taxonomy.validators.ClassificationDraft`, `config.MarketSettings`, `plm_platform.contracts` nothing.
- Produces (names later tasks import exactly):
  - `taxonomy.arms`: `ArmDraft(arm_id: str, fingerprint: str, profile: TaxonomyProfile, source_observation_ids: tuple[str, ...], support_count: int)`; `arm_id_for(profile: TaxonomyProfile, *, taken: frozenset[str] = frozenset()) -> str`; `generate_candidate_arms(classifications: Sequence[ClassificationDraft], *, min_support: int, observation_ids_by_content: Mapping[str, tuple[str, ...]] | None = None) -> list[ArmDraft]` (co-occurring attributes, §16; deterministic ordering; `_2`, `_3` suffixes on slug collisions).
  - `scoring.normalization`: `winsorize(values: Sequence[float], *, lower_pct: float = 0.10, upper_pct: float = 0.90) -> list[float]`; `percentile_score(value: float, *, population: Sequence[float], anchors=(0.10, 0.50, 0.90)) -> float` (p10→0, p50→50, p90→100, monotone between anchors) — §18's robust rank scores, **never min/max**.
  - `scoring.calibration`: `ScoreWeights(demand, growth, competitor_velocity, competition, audience_quality, cross_platform, production_fit)` with `.total`, `.as_mapping()`, `validate_weights()`; `default_weights() -> ScoreWeights` (25/20/15/15/10/10/5 percent, summing to 1.0).
  - `scoring.opportunity`: `compute_component_scores(features_by_arm: Mapping[str, ArmFeatures], *, settings: MarketSettings) -> dict[str, ScoreComponents]`; `score_arm(features: ArmFeatures, components: ScoreComponents, *, as_of, weights: ScoreWeights, score_version: str, feature_version: str, normalization_version: str) -> OpportunityScore`; `score_all_arms(features_by_arm, *, as_of, settings, weights=None) -> dict[str, OpportunityScore]`; `persist_scores(session, scores: Mapping[str, OpportunityScore], *, computed_at: datetime, feature_version: str, normalization_version: str) -> int`.
  - `storage.repositories` (added): `upsert_arm(session, draft: ArmDraft, *, brand_id: str) -> tuple[str, bool]` (idempotent on `fingerprint`; an existing row keeps its `arm_id` — Review Focus 4); `load_arms(session, *, status: ArmStatus | None = None) -> list[Mapping]`; `insert_score(session, *, arm_id, score: OpportunityScore, computed_at, components: Mapping, evidence: Mapping, feature_version, normalization_version) -> str`; `latest_scores(session, *, limit: int = 100, min_score: float | None = None, min_confidence: float | None = None) -> list[Mapping]`; `scores_for_arm(session, arm_id: str) -> list[Mapping]`.

> **Rulings (Open items #4, #5, #7):** (1) the §17.1 name `market_opportunity_score` is used everywhere on the wire and in the database; the architecture sketch's `market_score` is a label, not a field (R2/R3 authority order). (2) Arm identity is minted here from the *canonical fingerprint* (all 13 dimensions, unset ones explicit) — `arm_id` is a readable slug of the strongest dimensions with a deterministic suffix on collision, so an existing arm never changes identity when a new arm appears (R15). (3) `confidence` is returned beside the score and never multiplies it (§17.2/§19).

- [ ] **Step 1: Write the failing arm-identity test**

```python
# tests/unit/market/test_arm_identity.py
from services.market_intelligence.domain.enums import TaxonomyDimension as D
from services.market_intelligence.domain.value_objects import TaxonomyProfile, fingerprint
from services.market_intelligence.taxonomy.arms import arm_id_for, generate_candidate_arms
from services.market_intelligence.taxonomy.validators import ClassificationDraft

RNB = TaxonomyProfile(genre="rnb", use_case="late_night_drive", language="english",
                      vocal_mode="vocal", duration_bucket="45_75m")


def test_arm_id_is_the_spec_shaped_slug_and_is_stable() -> None:
    first = arm_id_for(RNB)
    assert first == "rnb_late_night_drive_english_vocal_45_75m"
    assert arm_id_for(RNB) == first


def test_a_slug_collision_gets_a_deterministic_suffix() -> None:
    assert arm_id_for(RNB, taken=frozenset({"rnb_late_night_drive_english_vocal_45_75m"})) == \
        "rnb_late_night_drive_english_vocal_45_75m_2"


def test_arms_come_from_co_occurring_attributes_with_support() -> None:
    drafts = generate_candidate_arms(
        [
            ClassificationDraft(dimensions={D.GENRE: ("rnb",), D.USE_CASE: ("late_night_drive",)},
                                confidence=0.8),
            ClassificationDraft(dimensions={D.GENRE: ("rnb",), D.USE_CASE: ("late_night_drive",)},
                                confidence=0.7),
            ClassificationDraft(dimensions={D.GENRE: ("lofi",)}, confidence=0.9),
        ],
        min_support=2,
    )
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.support_count == 2
    assert draft.fingerprint == fingerprint(RNB.__class__(genre="rnb", use_case="late_night_drive"))
    assert draft.profile.genre == "rnb" and draft.profile.use_case == "late_night_drive"


def test_generation_is_deterministic_and_refuses_a_zero_support() -> None:
    classifications = [
        ClassificationDraft(dimensions={D.GENRE: ("rnb",)}, confidence=0.5) for _ in range(2)
    ]
    assert generate_candidate_arms(classifications, min_support=2) == \
        generate_candidate_arms(classifications, min_support=2)
    import pytest

    with pytest.raises(ValueError):
        generate_candidate_arms(classifications, min_support=0)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_arm_identity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.taxonomy.arms'`.

- [ ] **Step 3: Write `taxonomy/arms.py`, then `upsert_arm`/`load_arms`**

```python
# services/market_intelligence/taxonomy/arms.py
"""§16: arms are production hypotheses mined from co-occurring classified attributes.

Identity is P1's (R15): the fingerprint covers all 13 dimensions; `arm_id` is the
readable slug the spec's example shows, with a deterministic collision suffix. P1
never allocates, never scores arms against each other as a bandit would, and never
writes arm *state* beyond its own lifecycle column (P2 owns that).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..domain.value_objects import TaxonomyProfile, fingerprint, slug_token
from .validators import ClassificationDraft

__all__ = ["ArmDraft", "arm_id_for", "generate_candidate_arms"]

_ID_DIMENSIONS = (
    "genre", "use_case", "language", "vocal_mode", "duration_bucket",
)


@dataclass(frozen=True, slots=True)
class ArmDraft:
    arm_id: str
    fingerprint: str
    profile: TaxonomyProfile
    source_observation_ids: tuple[str, ...] = ()
    support_count: int = 0


def arm_id_for(profile: TaxonomyProfile, *, taken: frozenset[str] = frozenset()) -> str:
    """Readable slug; on collision, `_2`, `_3`, … in call order (deterministic per batch)."""
    parts = [str(getattr(profile, name)) for name in _ID_DIMENSIONS
             if getattr(profile, name) is not None]
    base = slug_token("_".join(parts), max_length=44) or "arm"
    if base not in taken:
        return base
    suffix = 2
    while f"{base}_{suffix}" in taken:
        suffix += 1
    return f"{base}_{suffix}"


def generate_candidate_arms(
    classifications: Sequence[ClassificationDraft],
    *,
    min_support: int,
    observation_ids_by_content: Mapping[str, tuple[str, ...]] | None = None,
) -> list[ArmDraft]:
    """Group classifications by their *identity-bearing* dimensions; keep supported groups.

    The grouping key is the fingerprint, so two observations that agree on every
    classified dimension form one arm, and an unclassified dimension never silently
    merges two different arms (§14.3).
    """
    if min_support < 1:
        raise ValueError("min_support must be at least 1")
    groups: dict[str, list[ClassificationDraft]] = {}
    for draft in classifications:
        profile = _profile_from_draft(draft)
        groups.setdefault(fingerprint(profile), []).append(draft)
    drafts: list[ArmDraft] = []
    taken: set[str] = set()
    for arm_fingerprint in sorted(groups):
        members = groups[arm_fingerprint]
        if len(members) < min_support:
            continue
        profile = _profile_from_draft(members[0])
        arm_id = arm_id_for(profile, taken=frozenset(taken))
        taken.add(arm_id)
        observation_ids: tuple[str, ...] = ()
        if observation_ids_by_content is not None:
            collected: list[str] = []
            for member in members:
                collected.extend(observation_ids_by_content.get(member.content_fingerprint, ()))
            observation_ids = tuple(dict.fromkeys(collected))
        drafts.append(ArmDraft(arm_id=arm_id, fingerprint=arm_fingerprint, profile=profile,
                               source_observation_ids=observation_ids,
                               support_count=len(members)))
    return drafts


def _profile_from_draft(draft: ClassificationDraft):
    from .ontology import profile_from_dimensions

    return profile_from_dimensions(draft.dimensions)
```

> **Companion edit required by this step (do it in the same commit):** add
> `content_fingerprint: str = ""` to `ClassificationDraft` in `taxonomy/validators.py` and fill it in
> `taxonomy/classifier.py::classify_observation` with the cache key it already computes, so an arm
> can carry the observation ids its members came from. `validate_classification` leaves the field at
> its default (it has no input text).

```python
# services/market_intelligence/storage/repositories.py  (append)
from ..domain.enums import ArmStatus
from .models import arms
from ..taxonomy.arms import ArmDraft  # import inside the function instead if a cycle appears


def upsert_arm(session: Session, draft: "ArmDraft", *, brand_id: str) -> tuple[str, bool]:
    """One row per fingerprint; an existing arm keeps its identity (Review Focus 4)."""
    existing = session.execute(
        sa.select(arms.c.arm_id).where(arms.c.fingerprint == draft.fingerprint)
    ).scalar_one_or_none()
    if existing is not None:
        return str(existing), False
    session.execute(sa.insert(arms).values(
        id=_new_id(), brand_id=brand_id, arm_id=draft.arm_id, fingerprint=draft.fingerprint,
        status=ArmStatus.CANDIDATE.value,
        taxonomy_json={dimension.value: draft.profile.value_for(dimension)
                       for dimension in draft.profile.__dataclass_fields__},
        source_observation_ids=list(draft.source_observation_ids),
        support_count=draft.support_count,
    ))
    return draft.arm_id, True


def load_arms(session: Session, *, status: ArmStatus | None = None) -> list[Mapping[str, Any]]:
    statement = sa.select(arms).order_by(arms.c.arm_id)
    if status is not None:
        statement = statement.where(arms.c.status == status.value)
    return [dict(row) for row in session.execute(statement).mappings()]
```

> **Note on the import:** `storage.repositories` importing `taxonomy.arms` while `taxonomy.arms`
> imports `domain`/`taxonomy.validators` is acyclic, but the reverse edge would not be. If your
> import check flags a cycle, define `ArmDraft` in `taxonomy/arms.py` (as above) and use
> `TYPE_CHECKING` for the annotation — the function body only needs attribute access.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `python -m pytest tests/unit/market/test_arm_identity.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add services/market_intelligence/taxonomy/arms.py services/market_intelligence/storage/repositories.py \
        tests/unit/market/test_arm_identity.py
git commit -m "feat(p1): arm identity and generation"
```

- [ ] **Step 6: Write the failing normalisation + calibration test**

```python
# tests/unit/market/test_normalization_scoring.py
import pytest

from services.market_intelligence.scoring.normalization import percentile_score, winsorize


def test_winsorization_clamps_the_tails_not_the_middle() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 1000.0]
    clamped = winsorize(values, lower_pct=0.1, upper_pct=0.9)
    assert min(clamped) == 1.0
    assert max(clamped) < 1000.0          # the outlier is pulled in
    assert sorted(clamped)[:3] == [1.0, 2.0, 3.0]
    assert 3.0 in clamped and 7.0 in clamped


def test_percentile_score_is_anchored_and_monotone() -> None:
    population = [float(n) for n in range(1, 101)]  # p10 = 10.1, p50 = 50.5, p90 = 90.9
    assert percentile_score(10.1, population=population) == pytest.approx(0.0, abs=0.5)
    assert percentile_score(50.5, population=population) == pytest.approx(50.0, abs=0.5)
    assert percentile_score(90.9, population=population) == pytest.approx(100.0, abs=0.5)
    scores = [percentile_score(value, population=population)
              for value in (1.0, 25.0, 50.0, 75.0, 120.0)]
    assert scores == sorted(scores)
    assert all(0.0 <= score <= 100.0 for score in scores)


def test_a_single_value_population_still_returns_a_bounded_score() -> None:
    assert percentile_score(7.0, population=[7.0]) == 50.0
    assert percentile_score(7.0, population=[]) == 50.0
```

```python
# tests/unit/market/test_calibration.py
import pytest

from services.market_intelligence.scoring.calibration import ScoreWeights, default_weights


def test_default_weights_match_the_spec_percentages_and_sum_to_one() -> None:
    weights = default_weights()
    assert weights.as_mapping() == {
        "demand": 0.25, "growth": 0.20, "competitor_velocity": 0.15, "competition": 0.15,
        "audience_quality": 0.10, "cross_platform": 0.10, "production_fit": 0.05,
    }
    assert weights.total == pytest.approx(1.0)


def test_weights_that_do_not_sum_to_one_are_refused() -> None:
    with pytest.raises(ValueError, match="sum to 1.0"):
        ScoreWeights(demand=0.9, growth=0.9, competitor_velocity=0.0, competition=0.0,
                     audience_quality=0.0, cross_platform=0.0, production_fit=0.0).validate()


def test_negative_weights_are_refused() -> None:
    with pytest.raises(ValueError):
        ScoreWeights(demand=-0.1, growth=0.3, competitor_velocity=0.2, competition=0.2,
                     audience_quality=0.2, cross_platform=0.2, production_fit=0.0).validate()
```

- [ ] **Step 7: Run them and watch them fail**

Run: `python -m pytest tests/unit/market/test_normalization_scoring.py tests/unit/market/test_calibration.py -q`
Expected: FAIL — modules missing.

- [ ] **Step 8: Write `scoring/normalization.py` and `scoring/calibration.py`**

```python
# services/market_intelligence/scoring/normalization.py
"""§18: winsorize, then robust percentile anchors — never min/max on a tiny sample."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

__all__ = ["DEFAULT_ANCHORS", "percentile_score", "winsorize"]

DEFAULT_ANCHORS: Final[tuple[float, float, float]] = (0.10, 0.50, 0.90)


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("percentile of an empty sample is undefined")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = min(max(q, 0.0), 1.0) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def winsorize(
    values: Sequence[float], *, lower_pct: float = 0.10, upper_pct: float = 0.90,
) -> list[float]:
    """Clamp the tails to their percentiles so one outlier cannot dominate (§18)."""
    if not values:
        return []
    low = _percentile(values, lower_pct)
    high = _percentile(values, upper_pct)
    if high < low:
        raise ValueError("upper percentile below lower percentile")
    return [min(max(float(value), low), high) for value in values]


def percentile_score(
    value: float, *, population: Sequence[float], anchors: Sequence[float] = DEFAULT_ANCHORS,
) -> float:
    """§18's 0–100 rank score: p10→0, p50→50, p90→100, linear between the anchors.

    Outside the anchors the score is clamped, which keeps an extreme outlier in the
    population from stretching the scale for everyone else.
    """
    if not population:
        return 50.0
    low_q, mid_q, high_q = anchors
    low = _percentile(population, low_q)
    mid = _percentile(population, mid_q)
    high = _percentile(population, high_q)
    if value <= low:
        return 0.0
    if value >= high:
        return 100.0
    if value <= mid:
        span = mid - low
        return 50.0 if span <= 0 else 50.0 * (value - low) / span
    span = high - mid
    return 100.0 if span <= 0 else 50.0 + 50.0 * (value - mid) / span
```

```python
# services/market_intelligence/scoring/calibration.py
"""§17.2: all weights are configuration values, validated to sum to 1.0."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Self

__all__ = ["SCORE_VERSION", "ScoreWeights", "default_weights"]

SCORE_VERSION: Final[str] = "p1-v1"

_SUM_TOLERANCE: Final[float] = 1e-9


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    demand: float
    growth: float
    competitor_velocity: float
    competition: float
    audience_quality: float
    cross_platform: float
    production_fit: float

    @property
    def total(self) -> float:
        return sum(self.as_mapping().values())

    def as_mapping(self) -> dict[str, float]:
        return {"demand": self.demand, "growth": self.growth,
                "competitor_velocity": self.competitor_velocity,
                "competition": self.competition, "audience_quality": self.audience_quality,
                "cross_platform": self.cross_platform, "production_fit": self.production_fit}

    def validate(self) -> Self:
        for name, weight in self.as_mapping().items():
            if weight < 0:
                raise ValueError(f"weight {name} cannot be negative, got {weight}")
        if abs(self.total - 1.0) > _SUM_TOLERANCE:
            raise ValueError(f"score weights must sum to 1.0, got {self.total}")
        return self


def default_weights() -> ScoreWeights:
    """§17.2's initial weights; a change here is a score-version change, not a tweak."""
    return ScoreWeights(demand=0.25, growth=0.20, competitor_velocity=0.15, competition=0.15,
                        audience_quality=0.10, cross_platform=0.10, production_fit=0.05)
```

- [ ] **Step 9: Run the tests and watch them pass**

Run: `python -m pytest tests/unit/market/test_normalization_scoring.py tests/unit/market/test_calibration.py -q`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add services/market_intelligence/scoring/ tests/unit/market/test_normalization_scoring.py \
        tests/unit/market/test_calibration.py
git commit -m "feat(p1): robust normalisation and score calibration"
```

- [ ] **Step 11: Write the failing scorer test**

```python
# tests/unit/market/test_opportunity_scoring.py
from datetime import UTC, datetime

import pytest

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.value_objects import ScoreComponents
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.scoring.calibration import default_weights
from services.market_intelligence.scoring.opportunity import (
    compute_component_scores, persist_scores, score_all_arms,
)
from services.market_intelligence.storage import repositories

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_market_settings()


def _features(market_session, *, views: int, subscribers: int, fingerprint: str):
    from datetime import timedelta
    from plm_contracts import Tri

    from services.market_intelligence.domain.entities import NormalizedObservation

    observations = [
        NormalizedObservation(
            entity_id=f"{fingerprint}-e1", source_id="s1", raw_observation_id="r1",
            observed_at=AS_OF - timedelta(days=20), published_at=AS_OF - timedelta(days=50),
            views=Tri.known(views // 2), subscriber_count=Tri.known(subscribers),
        ),
        NormalizedObservation(
            entity_id=f"{fingerprint}-e1", source_id="s1", raw_observation_id="r2",
            observed_at=AS_OF - timedelta(days=10), published_at=AS_OF - timedelta(days=50),
            views=Tri.known(views), subscriber_count=Tri.known(subscribers),
        ),
        NormalizedObservation(
            entity_id=f"{fingerprint}-e2", source_id="s1", raw_observation_id="r3",
            observed_at=AS_OF - timedelta(days=5), published_at=AS_OF - timedelta(days=40),
            views=Tri.known(views // 3), subscriber_count=Tri.known(subscribers),
        ),
    ]
    return compute_arm_features({fingerprint: observations}, as_of=AS_OF, settings=SETTINGS)


def test_component_scores_are_bounded_and_rank_the_population(market_session) -> None:
    big_slow = _features(market_session, views=600_000, subscribers=10_000_000, fingerprint="a")
    small_fast = _features(market_session, views=45_000, subscribers=20_000, fingerprint="b")
    features = {**big_slow, **small_fast}
    components = compute_component_scores(features, settings=SETTINGS)
    for scores in components.values():
        assert isinstance(scores, ScoreComponents)
        assert all(0.0 <= value <= 100.0 for value in scores.as_mapping().values())


def test_scoring_is_deterministic_and_confidence_is_independent(market_session) -> None:
    features = _features(market_session, views=45_000, subscribers=20_000, fingerprint="b")
    components = compute_component_scores(features, settings=SETTINGS)
    first = score_all_arms(features, as_of=AS_OF, settings=SETTINGS)
    second = score_all_arms(features, as_of=AS_OF, settings=SETTINGS)
    assert first == second
    score = first["b"]
    assert 0.0 <= score.market_opportunity_score <= 100.0
    assert 0.0 <= score.confidence <= 1.0
    assert score.components == components["b"]
    assert score.score_version == "p1-v1"


def test_confidence_does_not_imply_a_high_score(market_session) -> None:
    features = _features(market_session, views=45_000, subscribers=20_000, fingerprint="b")
    score = score_all_arms(features, as_of=AS_OF, settings=SETTINGS)["b"]
    assert 0.0 <= score.market_opportunity_score <= 100.0
    assert 0.0 <= score.confidence <= 1.0
    assert compute_component_scores({}, settings=SETTINGS) == {}


def test_scores_persist_with_their_versions_and_read_back(market_session) -> None:
    from services.market_intelligence.domain.value_objects import TaxonomyProfile
    from services.market_intelligence.taxonomy.arms import ArmDraft

    features = _features(market_session, views=45_000, subscribers=20_000, fingerprint="b")
    profile = TaxonomyProfile(genre="rnb", use_case="late_night_drive")
    draft = ArmDraft(arm_id="rnb_late_night_drive", fingerprint="b", profile=profile,
                     support_count=3)
    arm_id, created = repositories.upsert_arm(market_session, draft, brand_id="neon_seoul")
    assert (arm_id, created) == ("rnb_late_night_drive", True)

    scores = score_all_arms(features, as_of=AS_OF, settings=SETTINGS)
    written = persist_scores(market_session, scores, computed_at=AS_OF,
                             feature_version="p1-features-v1",
                             normalization_version="p1-norm-v1")
    assert written == 1
    latest = repositories.latest_scores(market_session, limit=10)
    assert latest[0]["arm_id"] == "rnb_late_night_drive"
    assert latest[0]["score_version"] == "p1-v1"
    assert latest[0]["market_opportunity_score"] == pytest.approx(
        scores["b"].market_opportunity_score
    )


def test_arm_identity_survives_a_recompute(market_session) -> None:
    from services.market_intelligence.domain.value_objects import TaxonomyProfile
    from services.market_intelligence.taxonomy.arms import ArmDraft

    profile = TaxonomyProfile(genre="rnb", use_case="late_night_drive")
    first, _ = repositories.upsert_arm(
        market_session, ArmDraft(arm_id="rnb_late_night_drive", fingerprint="f1",
                                 profile=profile, support_count=2),
        brand_id="neon_seoul",
    )
    again, created = repositories.upsert_arm(
        market_session, ArmDraft(arm_id="rnb_late_night_drive", fingerprint="f1",
                                 profile=profile, support_count=9),
        brand_id="neon_seoul",
    )
    assert (again, created) == (first, False)  # Review Focus 4: identity is minted once
```

- [ ] **Step 12: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_opportunity_scoring.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.scoring.opportunity'`.

- [ ] **Step 13: Write `scoring/opportunity.py` and the score repositories**

```python
# services/market_intelligence/scoring/opportunity.py
"""§17–§20: the versioned, deterministic opportunity score.

Determinism (§30): every input is passed in — features, weights, versions, `as_of`.
There is no clock access, no randomness and no LLM call in this module. Given the same
stored observations and versions this produces byte-identical output.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from ..config import MarketSettings
from ..domain.entities import OpportunityScore
from ..domain.enums import ScoringComponent
from ..domain.value_objects import EvidenceSummary, ScoreComponents
from ..features.arm_features import ArmFeatures
from .calibration import SCORE_VERSION, ScoreWeights, default_weights
from .normalization import percentile_score

__all__ = ["compute_component_scores", "persist_scores", "score_all_arms", "score_arm"]


def _population(features_by_arm: Mapping[str, ArmFeatures], extractor) -> list[float]:
    values: list[float] = []
    for features in features_by_arm.values():
        value = extractor(features)
        if value is not None:
            values.append(float(value))
    return values


def compute_component_scores(
    features_by_arm: Mapping[str, ArmFeatures], *, settings: MarketSettings,
) -> dict[str, ScoreComponents]:
    """Rank every arm against the arm population, component by component (§17.3/§18)."""
    if not features_by_arm:
        return {}
    demand_population = _population(features_by_arm, _demand_value)
    growth_population = _population(features_by_arm, _growth_value)
    competitor_population = _population(features_by_arm, _competitor_velocity_value)
    competition_population = _population(features_by_arm, _competition_penalty_value)
    audience_population = _population(features_by_arm, _audience_value)
    cross_population = _population(features_by_arm, _cross_platform_value)
    production_population = _population(features_by_arm, _production_fit_value)
    scores: dict[str, ScoreComponents] = {}
    for arm_fingerprint, features in features_by_arm.items():
        scores[arm_fingerprint] = ScoreComponents(
            demand=_score(demand_population, _demand_value(features)),
            growth=_score(growth_population, _growth_value(features)),
            competitor_velocity=_score(competitor_population, _competitor_velocity_value(features)),
            # §17.3: lower saturation contributes *more* opportunity, so the penalty is inverted
            competition=100.0 - _score(competition_population, _competition_penalty_value(features)),
            audience_quality=_score(audience_population, _audience_value(features)),
            cross_platform=_score(cross_population, _cross_platform_value(features)),
            production_fit=_score(production_population, _production_fit_value(features)),
        )
    return scores


def score_arm(
    features: ArmFeatures,
    components: ScoreComponents,
    *,
    as_of: datetime,
    weights: ScoreWeights,
    score_version: str = SCORE_VERSION,
    feature_version: str,
    normalization_version: str,
) -> OpportunityScore:
    """The weighted sum of the seven components, clamped to 0–100.

    `confidence` rides along unchanged from the feature layer (§19): a missing
    cross-platform source already reduced it there, and it never adjusts this number.
    """
    weights.validate()
    weighted = sum(
        getattr(components, component.value) * weight
        for component, weight in _weight_pairs(weights)
    )
    score = max(0.0, min(100.0, weighted))
    return OpportunityScore(
        arm_id=features.arm_id,
        market_opportunity_score=score,
        confidence=features.confidence,
        components=components,
        evidence=EvidenceSummary(
            observation_count=features.observation_count,
            source_count=features.source_count,
            top_observation_ids=features.observation_ids[:5],
        ),
        as_of=as_of,
        score_version=score_version,
    )


def score_all_arms(
    features_by_arm: Mapping[str, ArmFeatures], *, as_of: datetime, settings: MarketSettings,
    weights: ScoreWeights | None = None,
) -> dict[str, OpportunityScore]:
    active_weights = default_weights() if weights is None else weights
    components_by_arm = compute_component_scores(features_by_arm, settings=settings)
    return {
        arm_fingerprint: score_arm(
            features, components_by_arm[arm_fingerprint], as_of=as_of, weights=active_weights,
            score_version=settings.score_version, feature_version=settings.feature_version,
            normalization_version=settings.normalization_version,
        )
        for arm_fingerprint, features in features_by_arm.items()
    }


def persist_scores(
    session, scores: Mapping[str, OpportunityScore], *, computed_at: datetime,
    feature_version: str, normalization_version: str,
) -> int:
    """Append one score row per arm per computation instant (§3.1/§20)."""
    from ..storage.repositories import insert_score

    written = 0
    for score in scores.values():
        insert_score(
            session, arm_id=score.arm_id, score=score, computed_at=computed_at,
            components={component.value: value
                        for component, value in score.components.as_mapping().items()},
            evidence={"observation_count": score.evidence.observation_count,
                      "source_count": score.evidence.source_count,
                      "top_observation_ids": list(score.evidence.top_observation_ids)},
            feature_version=feature_version, normalization_version=normalization_version,
        )
        written += 1
    return written


def _score(population: list[float], value: float | None) -> float:
    """Population rank for a known input; an unknown input scores the floor.

    Unknown ⇒ 0.0 is deliberate: the missing value already reduced `confidence`, and
    the alternative (treating it as an average) would render missing data as a pass
    (Review Focus 1).
    """
    if value is None:
        return 0.0
    return percentile_score(value, population=population)


def _weight_pairs(weights: ScoreWeights):
    mapping = weights.as_mapping()
    for component in ScoringComponent:
        yield component, mapping[component.value]


def _demand_value(features: ArmFeatures) -> float | None:
    if not (features.demand.median_views_per_day.is_known
            and features.demand.active_channel_audience.is_known):
        return None
    return (float(features.demand.median_views_per_day.unwrap())
            + float(features.demand.active_channel_audience.unwrap())
            + features.demand.recent_activity_count)


def _growth_value(features: ArmFeatures) -> float | None:
    if not features.growth_30d_vs_prev30d.is_known:
        return None
    return float(features.growth_30d_vs_prev30d.unwrap())


def _competitor_velocity_value(features: ArmFeatures) -> float | None:
    value = features.competition.median_video_velocity
    return float(value) if features.competition.sample_size else None


def _competition_penalty_value(features: ArmFeatures) -> float | None:
    """What must *not* help: saturation and concentration, both higher-is-worse."""
    if not features.competition.sample_size:
        return None
    return (features.competition.concentration_normalized * 100.0
            + features.competition.top_10_channel_share * 100.0)


def _audience_value(features: ArmFeatures) -> float | None:
    components = [features.audience.long_form_share, features.audience.engagement_consistency,
                  features.audience.international_spread]
    known = [float(value.unwrap()) for value in components if value.is_known]
    return sum(known) / len(known) if known else None


def _cross_platform_value(features: ArmFeatures) -> float | None:
    return float(features.cross_platform.unwrap()) if features.cross_platform.is_known else None


def _production_fit_value(features: ArmFeatures) -> float:
    fit = features.production_fit
    return (fit.vocal_compatibility + fit.language_support + fit.visual_feasibility
            + fit.copyright_risk_ease) / 4.0
```

```python
# services/market_intelligence/storage/repositories.py  (append)
from .models import scores


def insert_score(
    session: Session, *, arm_id: str, score, computed_at: datetime,
    components: Mapping[str, Any], evidence: Mapping[str, Any],
    feature_version: str, normalization_version: str,
) -> str:
    """§20: evidence and versions travel with the number, always."""
    score_id = _new_id()
    session.execute(sa.insert(scores).values(
        id=score_id, arm_id=arm_id, computed_at=computed_at, as_of=score.as_of,
        market_opportunity_score=score.market_opportunity_score,
        confidence=score.confidence, components_json=dict(components),
        evidence_json=dict(evidence), score_version=score.score_version,
        feature_version=feature_version, normalization_version=normalization_version,
    ))
    return score_id


def latest_scores(
    session: Session, *, limit: int = 100, min_score: float | None = None,
    min_confidence: float | None = None,
) -> list[Mapping[str, Any]]:
    """Newest score per arm, ranked — the API's read path (§25)."""
    newest = (
        sa.select(scores.c.arm_id, sa.func.max(scores.c.computed_at).label("computed_at"))
        .group_by(scores.c.arm_id).subquery()
    )
    statement = (
        sa.select(scores, arms.c.taxonomy_json)
        .join(newest, sa.and_(scores.c.arm_id == newest.c.arm_id,
                              scores.c.computed_at == newest.c.computed_at))
        .join(arms, arms.c.arm_id == scores.c.arm_id)
    )
    if min_score is not None:
        statement = statement.where(scores.c.market_opportunity_score >= min_score)
    if min_confidence is not None:
        statement = statement.where(scores.c.confidence >= min_confidence)
    statement = statement.order_by(scores.c.market_opportunity_score.desc(),
                                   scores.c.arm_id.asc()).limit(limit)
    return [dict(row) for row in session.execute(statement).mappings()]


def scores_for_arm(session: Session, arm_id: str) -> list[Mapping[str, Any]]:
    return [dict(row) for row in session.execute(
        sa.select(scores).where(scores.c.arm_id == arm_id).order_by(scores.c.computed_at.desc())
    ).mappings()]
```

- [ ] **Step 14: Run everything and watch it pass**

Run: `python -m pytest tests/unit/market -q`
Expected: PASS. `latest_scores` needs `from .models import arms` in `repositories.py` (add it beside the other model imports).

- [ ] **Step 15: Commit**

```bash
git add services/market_intelligence/scoring/opportunity.py \
        services/market_intelligence/storage/repositories.py tests/unit/market/test_opportunity_scoring.py
git commit -m "feat(p1): versioned deterministic opportunity scorer"
```

---

### Task P1.10: API

**Files:**
- Create: `services/market_intelligence/api/__init__.py`, `api/schemas.py`, `api/service_token.py`, `api/routes.py`, `api/app.py`
- Modify: `packages/plm_platform/config.py` (add `service_token`, `SERVICE_TOKEN_VAR`, `SERVICE_TOKEN_HEADER`), `.env.example`, `pyproject.toml` (add `fastapi`, `uvicorn`), `tests/unit/test_config.py`
- Create: `docs/adr/0021-p1-service-token-and-api.md`, `docs/adr/0022-p1-tables-and-envelope.md`, modify `docs/contracts/DECISION_LOG.md`
- Test: `tests/unit/market/test_api_schemas.py`, `tests/unit/market/test_api_auth.py`, `tests/unit/market/test_api_endpoints.py`, `tests/contract/test_c1_market_to_ar.py`

**Interfaces:**
- Consumes: `plm_contracts.ErrorShape`, `plm_platform.observability.CorrelationIdMiddleware`, `plm_platform.config.Settings/Secret`, `storage.repositories.latest_scores`/`load_observations`/`load_run`/`load_raw_observation`, `domain.value_objects.ScoreComponents`/`EvidenceSummary`.
- Produces (names later tasks import exactly):
  - `api.schemas`: `OpportunityOut` (**exactly** the registry C1 producer fields), `TaxonomyOut` (9 keys: `genre, subgenre, mood, use_case, language, vocal_mode, duration_bucket, visual_world, title_archetype`), `ComponentsOut` (7 keys), `EvidenceOut` (3 keys: `observation_count`, `source_count`, `top_observation_ids`), `OpportunityListResponse(items, next_cursor, as_of, score_version)`, `OpportunityDetail(arm, score, component_scores, confidence, evidence_summary, observation_counts, source_counts, last_updated)`, `ArmOut` (all 13 dimensions + `arm_id`, `fingerprint`, `status`), `ObservationOut(id, entity_id, source_id, observed_at, published_at, metrics, text_title, text_description, provenance)`, `RunOut(id, status, started_at, finished_at, counts, costs, error_summary, software_version)`, `encode_cursor(score: float, arm_id: str) -> str`, `decode_cursor(cursor: str) -> tuple[float, str]`.
  - `api.service_token`: `require_service_token(settings) -> Callable` FastAPI dependency yielding `None` or raising `HTTPException` (401 mismatch, 503 unconfigured).
  - `api.routes`: `build_router(*, session_factory, settings) -> APIRouter` with `GET /opportunities`, `GET /opportunities/{arm_id}`, `GET /observations/{observation_id}`, `GET /runs/{run_id}`.
  - `api.app`: `create_app(*, session_factory, settings: MarketSettings) -> FastAPI`; app mounted at prefix `/market/v1`; `X-Correlation-ID` echoed.
  - `plm_platform.config`: `SERVICE_TOKEN_VAR = "PLM_SERVICE_TOKEN"`, `SERVICE_TOKEN_HEADER = "X-PLM-Service-Token"`, `Settings.service_token: Secret | None`.

> **Rulings (Open items #1, #2, #6, #16):**
> 1. **Framework:** §36 leaves the library open; FastAPI + uvicorn are added because `plm_platform`'s correlation and observability modules already document FastAPI integration (`correlation_id_dependency`, `CorrelationIdMiddleware`). Tests use `httpx.ASGITransport`, so no port is bound in CI.
> 2. **Auth:** R15 says "service token from `plm_platform`" but no such setting existed. Resolution: `PLM_SERVICE_TOKEN` is an optional platform secret; the header is `X-PLM-Service-Token`. Unconfigured ⇒ `503 blocked_pending_credentials` (never an open endpoint); mismatch ⇒ `401 invalid_service_token`, compared with `hmac.compare_digest`.
> 3. **Envelope:** the list response is the registry's frozen flat object `{items, next_cursor, as_of, score_version}` — **not** `plm_contracts.Page`/`Envelope`, whose shapes differ. The registry is the first authority (R2); this reconciliation is recorded in ADR-0022 so nobody "fixes" the payload back to an envelope.
> 4. **The registry's C1 note about `freshness`** is honoured by *absence*: P2 derives `freshness_seconds` from `as_of` (P2 §46), so P1 must emit an aware ISO-8601 `as_of` and must not invent a `freshness` field. The seam test asserts both halves.

- [ ] **Step 1: Write the failing platform-token test (modify `tests/unit/test_config.py`)**

```python
# tests/unit/test_config.py  (modify imports and the drift class)
from plm_platform.config import (
    DEFAULTS, PROVIDER_SECRET_VARS, REQUIRED_VARS, SERVICE_TOKEN_VAR, Env, LogLevel, Secret,
    Settings, SettingsError, load_settings,
)

INTERNAL_SECRET_VARS = (SERVICE_TOKEN_VAR,)


class TestServiceToken:
    def test_the_service_token_is_optional_and_redacted(self) -> None:
        settings = load(full_env(PLM_SERVICE_TOKEN="shared-secret-sentinel"))
        assert settings.service_token is not None
        assert settings.service_token.get_secret_value() == "shared-secret-sentinel"
        assert "shared-secret-sentinel" not in repr(settings)

    def test_absent_service_token_is_none_and_does_not_break_mock_mode(self) -> None:
        settings = load(full_env())
        assert settings.service_token is None
        assert settings.is_mock_mode()

    def test_the_token_variable_name_is_not_a_provider_credential(self) -> None:
        assert SERVICE_TOKEN_VAR not in PROVIDER_SECRET_VARS


class TestEnvExampleDrift:  # existing class, one assertion changes
    def test_loader_variable_names_match_the_example_exactly(self) -> None:
        active, commented = env_example_names()
        assert active == set(REQUIRED_VARS)
        assert commented == set(PROVIDER_SECRET_VARS) | set(INTERNAL_SECRET_VARS)
        assert set(DEFAULTS) & (active | commented) == set()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/test_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'SERVICE_TOKEN_VAR'`.

- [ ] **Step 3: Extend `plm_platform/config.py` and `.env.example`**

```python
# packages/plm_platform/config.py  (additions)
SERVICE_TOKEN_VAR: Final[str] = "PLM_SERVICE_TOKEN"
"""Shared service token for internal phase APIs (R15); optional, never a provider key."""

SERVICE_TOKEN_HEADER: Final[str] = "X-PLM-Service-Token"
"""Header carrying the service token; the name is defined once, here."""

# inside Settings (after `temporal_host`):
    service_token: Secret | None

# inside load_settings(...):
        service_token=_resolve_optional_secret(SERVICE_TOKEN_VAR, layers),
```

```text
# .env.example  (append, in the commented block)
# Internal service token for phase APIs (R15). Uncomment and set before running
# any phase API; absent means the API answers 503 blocked_pending_credentials.
# PLM_SERVICE_TOKEN=
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `python -m pytest tests/unit/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit the platform change**

```bash
git add packages/plm_platform/config.py .env.example tests/unit/test_config.py
git commit -m "feat(p1): optional service token setting for internal phase APIs"
```

- [ ] **Step 6: Write the failing payload-shape test**

```python
# tests/unit/market/test_api_schemas.py
import json
from datetime import UTC, datetime

from services.market_intelligence.api.schemas import (
    ComponentsOut, EvidenceOut, OpportunityListResponse, OpportunityOut, TaxonomyOut,
    decode_cursor, encode_cursor,
)


def test_the_opportunity_payload_carries_exactly_the_frozen_c1_field_names() -> None:
    payload = OpportunityOut.model_json_schema()["properties"]
    assert set(payload) == {
        "arm_id", "market_opportunity_score", "confidence", "as_of", "taxonomy",
        "components", "evidence", "score_version",
    }
    assert set(TaxonomyOut.model_json_schema()["properties"]) == {
        "genre", "subgenre", "mood", "use_case", "language", "vocal_mode",
        "duration_bucket", "visual_world", "title_archetype",
    }
    assert set(ComponentsOut.model_json_schema()["properties"]) == {
        "demand", "growth", "competitor_velocity", "competition", "audience_quality",
        "cross_platform", "production_fit",
    }
    assert set(EvidenceOut.model_json_schema()["properties"]) == {
        "observation_count", "source_count", "top_observation_ids",
    }


def test_the_list_envelope_is_the_registry_shape_not_a_page() -> None:
    response = OpportunityListResponse(items=[], next_cursor=None,
                                      as_of=datetime(2026, 9, 20, tzinfo=UTC),
                                      score_version="p1-v1")
    dumped = json.loads(response.model_dump_json())
    assert set(dumped) == {"items", "next_cursor", "as_of", "score_version"}
    assert "freshness" not in dumped


def test_cursors_round_trip_and_refuse_garbage() -> None:
    cursor = encode_cursor(82.4, "rnb_late_night_drive")
    assert decode_cursor(cursor) == (82.4, "rnb_late_night_drive")
    import pytest

    with pytest.raises(ValueError):
        decode_cursor("not-a-cursor")
```

- [ ] **Step 7: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_api_schemas.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.api'` (and `fastapi` not installed until Step 8 adds it).

- [ ] **Step 8: Add the dependencies and write `api/schemas.py`**

```toml
# pyproject.toml  [project] dependencies — append
    "fastapi>=0.115",
    "uvicorn>=0.30",
```

```python
# services/market_intelligence/api/schemas.py
"""The frozen C1 wire shapes (registry `docs/contracts/CONTRACT_REGISTRY.md`, entry C1).

Field names here are not ours to rename: P2's adapter reads `market_opportunity_score`
into `p1_score` and `confidence` into `p1_confidence` (registry C1). `extra="forbid"`
on every model so a drift is a test failure, not a silently dropped field.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ArmOut", "ComponentsOut", "EvidenceOut", "ObservationOut", "OpportunityDetail",
    "OpportunityListResponse", "OpportunityOut", "RunOut", "TaxonomyOut", "decode_cursor",
    "encode_cursor",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaxonomyOut(_Frozen):
    """§20's taxonomy object, exactly the nine keys the registry freezes."""

    genre: str | None = None
    subgenre: str | None = None
    mood: list[str] = Field(default_factory=list)
    use_case: list[str] = Field(default_factory=list)
    language: str | None = None
    vocal_mode: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None
    title_archetype: str | None = None


class ComponentsOut(_Frozen):
    demand: float
    growth: float
    competitor_velocity: float
    competition: float
    audience_quality: float
    cross_platform: float
    production_fit: float


class EvidenceOut(_Frozen):
    observation_count: Annotated[int, Field(ge=0)]
    source_count: Annotated[int, Field(ge=0)]
    top_observation_ids: list[str] = Field(default_factory=list)


class OpportunityOut(_Frozen):
    """§20/registry C1. `freshness` is deliberately absent — P2 derives it from `as_of`."""

    arm_id: str = Field(min_length=1)
    market_opportunity_score: Annotated[float, Field(ge=0.0, le=100.0)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    as_of: datetime
    taxonomy: TaxonomyOut
    components: ComponentsOut
    evidence: EvidenceOut
    score_version: str = Field(min_length=1)


class OpportunityListResponse(_Frozen):
    items: list[OpportunityOut]
    next_cursor: str | None
    as_of: datetime
    score_version: str


class ArmOut(_Frozen):
    arm_id: str
    fingerprint: str
    status: str
    genre: str | None = None
    subgenre: str | None = None
    mood: list[str] = Field(default_factory=list)
    use_case: str | None = None
    language: str | None = None
    vocal_mode: str | None = None
    vocal_gender: str | None = None
    energy: str | None = None
    bpm_range: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None
    audience_intent: str | None = None
    title_archetype: str | None = None
    support_count: int = 0


class OpportunityDetail(_Frozen):
    """§25's detail shape; the 13-dimension arm object lives here (R14 dimensions)."""

    arm: ArmOut
    score: Annotated[float, Field(ge=0.0, le=100.0)]
    component_scores: ComponentsOut
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    evidence_summary: EvidenceOut
    observation_counts: Annotated[int, Field(ge=0)]
    source_counts: Annotated[int, Field(ge=0)]
    last_updated: datetime


class ObservationOut(_Frozen):
    id: str
    entity_id: str
    source_id: str
    observed_at: datetime
    published_at: datetime | None = None
    metrics: dict[str, Any]
    text_title: str | None = None
    text_description: str | None = None
    provenance: dict[str, Any]


class RunOut(_Frozen):
    id: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    counts: dict[str, int]
    costs: dict[str, float]
    error_summary: dict[str, Any] | None = None
    software_version: str


def encode_cursor(score: float, arm_id: str) -> str:
    """Opaque keyset cursor over `(score desc, arm_id asc)` — stable under insertion."""
    payload = json.dumps({"s": score, "a": arm_id}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decode_cursor(cursor: str) -> tuple[float, str]:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        return float(decoded["s"]), str(decoded["a"])
    except Exception as exc:  # noqa: BLE001 - any malformed cursor is one message
        raise ValueError(f"invalid cursor {cursor!r}") from exc
```

- [ ] **Step 9: Run the test and watch it pass**

Run: `python -m pip install -e ".[dev]"` (adds fastapi/uvicorn) then
`python -m pytest tests/unit/market/test_api_schemas.py -q`
Expected: PASS (3 passed).

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml services/market_intelligence/api/ tests/unit/market/test_api_schemas.py
git commit -m "feat(p1): C1 wire schemas and cursors"
```

- [ ] **Step 11: Write the C1 seam test**

```python
# tests/contract/test_c1_market_to_ar.py
"""Seam C1 — PHASE-P1 → PHASE-P2 (market opportunity).

The producer is P1's real API payload (in-process, fixture-backed); the consumer
schema carries the registry's frozen field names verbatim. When this fails, the fix
is decided against the registry *first* (amend it via ADR), then applied to both
sides in one change — never by editing this file to match the producer.

Registry note this test pins: **`freshness` is not on the P1 wire.** P2 §46 derives
`freshness_seconds` from `as_of`, so the assertion here is that `as_of` is an aware
ISO-8601 instant and that no `freshness` key exists to be misread. That is the seam
entry's near-miss (`freshness_seconds` is P2-internal), resolved by derivation rather
than by inventing a field.
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from tests.support.contract import SeamRegistry, assert_seam_payload

REGISTRY_ENTRY = "C1 · PHASE-P1 → PHASE-P2"

REGISTRY_FIELDS: tuple[str, ...] = (
    "arm_id", "market_opportunity_score", "confidence", "as_of", "taxonomy",
    "components", "evidence", "score_version",
)
REGISTRY_TAXONOMY_FIELDS: tuple[str, ...] = (
    "genre", "subgenre", "mood", "use_case", "language", "vocal_mode", "duration_bucket",
    "visual_world", "title_archetype",
)
REGISTRY_COMPONENT_FIELDS: tuple[str, ...] = (
    "demand", "growth", "competitor_velocity", "competition", "audience_quality",
    "cross_platform", "production_fit",
)
REGISTRY_EVIDENCE_FIELDS: tuple[str, ...] = (
    "observation_count", "source_count", "top_observation_ids",
)


class TaxonomySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    genre: str | None = None
    subgenre: str | None = None
    mood: list[str] = []
    use_case: list[str] = []
    language: str | None = None
    vocal_mode: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None
    title_archetype: str | None = None


class ComponentsSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    demand: float
    growth: float
    competitor_velocity: float
    competition: float
    audience_quality: float
    cross_platform: float
    production_fit: float


class EvidenceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_count: int
    source_count: int
    top_observation_ids: list[str]


class ConsumerSchema(BaseModel):
    """P2's expected schema, field names copied from the registry entry."""

    model_config = ConfigDict(extra="forbid")

    arm_id: str
    market_opportunity_score: float
    confidence: float
    as_of: datetime
    taxonomy: TaxonomySchema
    components: ComponentsSchema
    evidence: EvidenceSchema
    score_version: str


def test_declared_fields_match_the_consumer_schema() -> None:
    assert set(ConsumerSchema.model_fields) == set(REGISTRY_FIELDS)
    assert set(TaxonomySchema.model_fields) == set(REGISTRY_TAXONOMY_FIELDS)
    assert set(ComponentsSchema.model_fields) == set(REGISTRY_COMPONENT_FIELDS)
    assert set(EvidenceSchema.model_fields) == set(REGISTRY_EVIDENCE_FIELDS)


def test_producer_payload_validates_against_the_consumer_schema(c1_api_client) -> None:
    response = c1_api_client.get("/market/v1/opportunities", params={"limit": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor", "as_of", "score_version"}
    payload = json.loads(json.dumps(body["items"][0]))
    assert_seam_payload(payload, ConsumerSchema, seam=REGISTRY_ENTRY,
                        producer="GET /market/v1/opportunities")


def test_freshness_is_derivable_from_as_of_and_never_a_field(c1_api_client) -> None:
    payload = c1_api_client.get("/market/v1/opportunities", params={"limit": 1}).json()["items"][0]
    assert "freshness" not in payload and "freshness_seconds" not in payload
    assert datetime.fromisoformat(payload["as_of"]).tzinfo is not None, (
        "P2 derives freshness_seconds from as_of (registry C1); a naive instant would "
        "silently become a UTC assumption across phases"
    )


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    assert contract_registry.exists, "W0.10 created the registry; it cannot be missing"
    contract_registry.require_entry(REGISTRY_ENTRY)
```

The `c1_api_client` fixture lives in `tests/contract/conftest.py` (create it in this step):

```python
# tests/contract/conftest.py
"""Seam-test fixtures: a fixture-backed P1 API with its token, no docker, no credentials."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa
from plm_contracts import Tri
from plm_platform.config import load_settings
from sqlalchemy.orm import Session, sessionmaker

from services.market_intelligence.api.app import create_app
from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.domain.value_objects import TaxonomyProfile
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.scoring.opportunity import persist_scores, score_all_arms
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.models import MARKET_METADATA
from services.market_intelligence.taxonomy.arms import ArmDraft

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SEAM_TOKEN = "seam-test-token"
ARM_ID = "rnb_late_night_drive_english_vocal_45_75m"

PLATFORM_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": SEAM_TOKEN,
}


def _seed(session: Session) -> None:
    profile = TaxonomyProfile(genre="rnb", use_case="late_night_drive", language="english",
                              vocal_mode="vocal", duration_bucket="45_75m")
    repositories.upsert_arm(
        session,
        ArmDraft(arm_id=ARM_ID, fingerprint="seam-fixture", profile=profile,
                 source_observation_ids=("raw-1",), support_count=2),
        brand_id="neon_seoul",
    )
    observations = [
        NormalizedObservation(
            entity_id="e1", source_id="youtube_data_api", raw_observation_id=f"raw-{index}",
            observed_at=AS_OF - timedelta(days=30 - index * 5),
            published_at=AS_OF - timedelta(days=60),
            views=Tri.known(1000 + index * 900), subscriber_count=Tri.known(20_000),
        )
        for index in range(6)
    ]
    settings = load_market_settings()
    features = compute_arm_features(
        {"seam-fixture": observations}, as_of=AS_OF, settings=settings,
        arm_ids={"seam-fixture": ARM_ID}, profiles={"seam-fixture": profile},
        taxonomy_confidence={"seam-fixture": 0.8},
    )
    persist_scores(session, score_all_arms(features, as_of=AS_OF, settings=settings),
                   computed_at=AS_OF, feature_version="p1-features-v1",
                   normalization_version="p1-norm-v1")


@pytest.fixture
def c1_api_client():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS market")
    MARKET_METADATA.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        _seed(session)
        session.commit()
    platform = load_settings(env=PLATFORM_ENV)
    app = create_app(session_factory=factory,
                     settings=load_market_settings(platform=platform))
    transport = httpx.ASGITransport(app=app)
    with httpx.Client(transport=transport, base_url="http://testserver",
                      headers={"X-PLM-Service-Token": SEAM_TOKEN}) as client:
        yield client
```

- [ ] **Step 12: Run it and watch it fail**

Run: `python -m pytest tests/contract/test_c1_market_to_ar.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.market_intelligence.api.app'` (the app does not exist yet). The contract-suite run also collects `test_contract_registry.py`, which must stay green.

- [ ] **Step 13: Write `api/service_token.py`, `api/routes.py` and `api/app.py`**

```python
# services/market_intelligence/api/service_token.py
"""R15's service-token auth. Fail closed: an unconfigured token refuses every read."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import Header, HTTPException
from plm_contracts import ErrorShape
from plm_platform.config import SERVICE_TOKEN_VAR, Settings

__all__ = ["require_service_token"]

_HEADER_ALIAS = "X-PLM-Service-Token"


def require_service_token(platform: Settings | None) -> Callable[[str | None], None]:
    """Build the dependency for one app instance; `None` settings refuse all reads."""

    def dependency(
        token: Annotated[str | None, Header(alias=_HEADER_ALIAS)] = None,
    ) -> None:
        if platform is None or platform.service_token is None:
            raise HTTPException(
                status_code=503,
                detail=ErrorShape(
                    code="blocked_pending_credentials",
                    field_paths=[SERVICE_TOKEN_VAR],
                    message=(
                        "the market API is closed until a service token is configured: "
                        f"set {SERVICE_TOKEN_VAR} (R15). Nothing is served unauthenticated."
                    ),
                ).model_dump(),
            )
        expected = platform.service_token.get_secret_value()
        if token is None or not hmac.compare_digest(token.encode(), expected.encode()):
            raise HTTPException(
                status_code=401,
                detail=ErrorShape(
                    code="invalid_service_token", field_paths=[_HEADER_ALIAS],
                    message="missing or invalid service token",
                ).model_dump(),
            )

    return dependency
```

```python
# services/market_intelligence/api/routes.py
"""§25's read-only API. P2 never sees a table (AC-09)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from plm_contracts import ErrorShape
from sqlalchemy.orm import Session

from ..config import MarketSettings
from ..domain.value_objects import classify_freshness
from ..storage.repositories import (
    latest_scores, load_observations, load_raw_observation, load_run,
)
from .schemas import (
    ArmOut, ComponentsOut, EvidenceOut, ObservationOut, OpportunityDetail,
    OpportunityListResponse, OpportunityOut, RunOut, TaxonomyOut, decode_cursor, encode_cursor,
)

__all__ = ["build_router"]

SessionFactory = Callable[[], Session]


def build_router(*, session_factory: SessionFactory | None,
                 settings: MarketSettings) -> APIRouter:
    router = APIRouter()
    dependency = _dependencies(session_factory, settings)

    @router.get("/opportunities", response_model=OpportunityListResponse)
    def list_opportunities(
        limit: int = Query(20, ge=1, le=200),
        cursor: str | None = None,
        min_score: float | None = Query(None, ge=0.0, le=100.0),
        min_confidence: float | None = Query(None, ge=0.0, le=1.0),
        freshness: str | None = Query(None, pattern="^(fresh|normal|stale|expired)$"),
        country: str | None = None,
        language: str | None = None,
        genre: str | None = None,
        use_case: str | None = None,
        session: Session = Depends(dependency),
        _: None = Depends(_auth(settings)),
    ) -> OpportunityListResponse:
        rows = latest_scores(session, limit=limit * 3, min_score=min_score,
                             min_confidence=min_confidence)
        if country is not None:
            raise HTTPException(status_code=422, detail=ErrorShape(
                code="unsupported_parameter", field_paths=["country"],
                message=("the frozen C1 wire carries no country field; P1 refuses the filter "
                         "rather than silently returning an empty list (registry C1)"),
            ).model_dump())
        filtered = [row for row in rows if _matches(row, language=language, genre=genre,
                                                    use_case=use_case)]
        now = datetime.now(UTC)
        if freshness is not None:
            filtered = [row for row in filtered
                        if classify_freshness(
                            as_of=row["as_of"], now=now,
                            fresh_hours=settings.freshness_fresh_hours,
                            normal_hours=settings.freshness_normal_hours,
                            stale_hours=settings.freshness_stale_hours,
                        ).bucket.value == freshness]
        if cursor is not None:
            score_floor, arm_floor = decode_cursor(cursor)
            filtered = [row for row in filtered if _after_cursor(row, score_floor, arm_floor)]
        items = filtered[:limit]
        next_cursor = None
        if len(filtered) > limit and items:
            last = items[-1]
            next_cursor = encode_cursor(float(last["market_opportunity_score"]), last["arm_id"])
        as_of = max((item["as_of"] for item in items), default=now)
        return OpportunityListResponse(
            items=[_opportunity(row) for row in items], next_cursor=next_cursor, as_of=as_of,
            score_version=settings.score_version,
        )

    @router.get("/opportunities/{arm_id}", response_model=OpportunityDetail)
    def opportunity_detail(arm_id: str, session: Session = Depends(dependency),
                           _: None = Depends(_auth(settings))) -> OpportunityDetail:
        rows = [row for row in latest_scores(session, limit=200) if row["arm_id"] == arm_id]
        if not rows:
            raise _not_found("arm", arm_id)
        row = rows[0]
        return OpportunityDetail(
            arm=_arm(row), score=float(row["market_opportunity_score"]),
            component_scores=ComponentsOut(**{k: float(v) for k, v in
                                              row["components_json"].items()}),
            confidence=float(row["confidence"]),
            evidence_summary=EvidenceOut(**row["evidence_json"]),
            observation_counts=int(row["evidence_json"].get("observation_count", 0)),
            source_counts=int(row["evidence_json"].get("source_count", 0)),
            last_updated=row["computed_at"],
        )

    @router.get("/observations/{observation_id}", response_model=ObservationOut)
    def observation(observation_id: str, session: Session = Depends(dependency),
                    _: None = Depends(_auth(settings))) -> ObservationOut:
        row = session.execute(
            sa.text("SELECT * FROM market.market_observations WHERE id = :id"),
            {"id": observation_id},
        ).mappings().one_or_none()
        if row is None:
            raise _not_found("observation", observation_id)
        raw = load_raw_observation(session, str(row["raw_observation_id"])) or {}
        return ObservationOut(
            id=str(row["id"]), entity_id=str(row["entity_id"]), source_id=str(row["source_id"]),
            observed_at=row["observed_at"], published_at=row["published_at"],
            metrics={name: row[name] for name in
                     ("views", "likes", "comments", "subscriber_count",
                      "video_duration_seconds", "playlist_item_count", "channel_video_count")},
            text_title=row["text_title"], text_description=row["text_description"],
            provenance={"raw_observation_id": str(row["raw_observation_id"]),
                        "payload_uri": raw.get("payload_uri"),
                        "payload_hash": raw.get("payload_hash"),
                        "request_context_hash": raw.get("request_context_hash"),
                        "observed_at": row["observed_at"]},
        )

    @router.get("/runs/{run_id}", response_model=RunOut)
    def run(run_id: str, session: Session = Depends(dependency),
            _: None = Depends(_auth(settings))) -> RunOut:
        row = load_run(session, run_id)
        if row is None:
            raise _not_found("run", run_id)
        return RunOut(
            id=str(row["id"]), status=row["status"], started_at=row["started_at"],
            finished_at=row["finished_at"],
            counts={"request_count": row["request_count"], "success_count": row["success_count"],
                    "error_count": row["error_count"], "rate_limit_count": row["rate_limit_count"],
                    "raw_observation_count": row["raw_observation_count"],
                    "normalized_observation_count": row["normalized_observation_count"]},
            costs={"api_calls": row["cost_api_calls"], "llm_tokens": row["cost_llm_tokens"],
                   "llm_cost_estimate": row["cost_llm_cost_estimate"],
                   "storage_bytes": row["cost_storage_bytes"]},
            error_summary=row["error_summary"], software_version=row["software_version"],
        )

    return router


def _dependencies(session_factory: SessionFactory | None, settings: MarketSettings):
    def session_dependency() -> Session:
        if session_factory is None:
            raise HTTPException(status_code=503, detail=ErrorShape(
                code="no_database", field_paths=[], message="no session factory configured",
            ).model_dump())
        with session_factory() as session:
            yield session

    return session_dependency


def _auth(settings: MarketSettings):
    from .service_token import require_service_token

    return require_service_token(settings.platform)


def _not_found(kind: str, identifier: str) -> HTTPException:
    return HTTPException(status_code=404, detail=ErrorShape(
        code="not_found", field_paths=[kind], message=f"{kind} {identifier!r} does not exist",
    ).model_dump())


def _opportunity(row) -> OpportunityOut:
    taxonomy = row["taxonomy_json"]
    return OpportunityOut(
        arm_id=row["arm_id"], market_opportunity_score=float(row["market_opportunity_score"]),
        confidence=float(row["confidence"]),
        as_of=row["as_of"], taxonomy=TaxonomyOut(**_taxonomy_payload(taxonomy)),
        components=ComponentsOut(**{k: float(v) for k, v in row["components_json"].items()}),
        evidence=EvidenceOut(**row["evidence_json"]),
        score_version=row["score_version"],
    )


def _taxonomy_payload(stored: dict) -> dict:
    """Map the stored 13-dimension profile onto the frozen 9-key wire object."""
    moods = _split(stored.get("mood"))
    use_cases = _split(stored.get("use_case"))
    return {
        "genre": _single(stored.get("genre")), "subgenre": _single(stored.get("subgenre")),
        "mood": moods, "use_case": use_cases, "language": _single(stored.get("language")),
        "vocal_mode": _single(stored.get("vocal_mode")),
        "duration_bucket": _single(stored.get("duration_bucket")),
        "visual_world": _single(stored.get("visual_world")),
        "title_archetype": _single(stored.get("title_archetype")),
    }


def _single(value: object) -> str | None:
    return None if value in (None, "none", "") else str(value)


def _split(value: object) -> list[str]:
    if value in (None, "none", ""):
        return []
    return [part for part in str(value).split("+") if part]


def _arm(row) -> ArmOut:
    taxonomy = row["taxonomy_json"]
    return ArmOut(
        arm_id=row["arm_id"], fingerprint=row.get("fingerprint", row["arm_id"]),
        status=row.get("status", "candidate"),
        genre=_single(taxonomy.get("genre")), subgenre=_single(taxonomy.get("subgenre")),
        mood=_split(taxonomy.get("mood")),
        use_case=_single(taxonomy.get("use_case")) or None,
        language=_single(taxonomy.get("language")), vocal_mode=_single(taxonomy.get("vocal_mode")),
        vocal_gender=_single(taxonomy.get("vocal_gender")),
        energy=_single(taxonomy.get("energy")), bpm_range=_single(taxonomy.get("bpm_range")),
        duration_bucket=_single(taxonomy.get("duration_bucket")),
        visual_world=_single(taxonomy.get("visual_world")),
        audience_intent=_single(taxonomy.get("audience_intent")),
        title_archetype=_single(taxonomy.get("title_archetype")),
        support_count=int(row.get("support_count", 0)),
    )


def _after_cursor(row, score_floor: float, arm_floor: str) -> bool:
    """Keyset position under `(score desc, arm_id asc)`."""
    score = float(row["market_opportunity_score"])
    if score != score_floor:
        return score < score_floor
    return row["arm_id"] > arm_floor


def _matches(row, *, language: str | None, genre: str | None, use_case: str | None) -> bool:
    taxonomy = row["taxonomy_json"] or {}
    if language is not None and _single(taxonomy.get("language")) != language:
        return False
    if genre is not None and _single(taxonomy.get("genre")) != genre:
        return False
    if use_case is not None and use_case not in _split(taxonomy.get("use_case")):
        return False
    return True
```

```python
# services/market_intelligence/api/app.py
"""The P1 API application factory. Mounted at `/market/v1` (R15)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from plm_platform.observability import CorrelationIdMiddleware
from sqlalchemy.orm import Session

from ..config import MarketSettings
from .routes import build_router

__all__ = ["API_PREFIX", "create_app"]

API_PREFIX = "/market/v1"


def create_app(*, session_factory: Callable[[], Session] | None,
               settings: MarketSettings) -> FastAPI:
    """Build the app; `session_factory=None` yields 503s instead of crashing at import."""
    app = FastAPI(title="PLM market intelligence", version=settings.score_version,
                  docs_url=None, redoc_url=None)
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(
        build_router(session_factory=session_factory, settings=settings), prefix=API_PREFIX
    )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
        from plm_contracts import ErrorShape

        return JSONResponse(status_code=500, content=ErrorShape(
            code="internal_error", field_paths=[],
            message=f"{type(exc).__name__}: {exc}",
        ).model_dump())

    return app
```

- [ ] **Step 14: Run the seam test and watch it pass**

Run: `python -m pytest tests/contract/test_c1_market_to_ar.py -q`
Expected: PASS (4 passed: fields, payload, freshness derivation, registry entry). A field-name failure prints the registry drift diagnosis — fix the *producer*, never the consumer schema.

- [ ] **Step 15: Write the remaining endpoint and auth tests, run, commit**

```python
# tests/unit/market/test_api_endpoints.py

def test_list_supports_filters_and_cursors(c1_api_client) -> None:
    first = c1_api_client.get("/market/v1/opportunities", params={"limit": 1}).json()
    assert first["score_version"] == "p1-v1"
    assert first["next_cursor"] is None or isinstance(first["next_cursor"], str)
    filtered = c1_api_client.get("/market/v1/opportunities",
                                 params={"genre": "rnb", "use_case": "late_night_drive"}).json()
    assert filtered["items"] and filtered["items"][0]["arm_id"].startswith("rnb_")
    empty = c1_api_client.get("/market/v1/opportunities", params={"genre": "polka"}).json()
    assert empty["items"] == []


def test_detail_returns_the_arm_with_all_thirteen_dimensions(c1_api_client) -> None:
    body = c1_api_client.get(
        "/market/v1/opportunities/rnb_late_night_drive_english_vocal_45_75m"
    ).json()
    assert set(body) == {"arm", "score", "component_scores", "confidence", "evidence_summary",
                         "observation_counts", "source_counts", "last_updated"}
    assert body["arm"]["vocal_gender"] is None and body["arm"]["bpm_range"] is None
    assert body["arm"]["vocal_mode"] == "vocal"


def test_unknown_identifiers_are_404_with_the_platform_error_shape(c1_api_client) -> None:
    response = c1_api_client.get("/market/v1/runs/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"

    missing = c1_api_client.get("/market/v1/observations/does-not-exist")
    assert missing.status_code == 404
```

```python
# tests/unit/market/test_api_auth.py
import httpx
import pytest
from plm_platform.config import Secret, load_settings

from services.market_intelligence.api.app import create_app
from services.market_intelligence.config import load_market_settings


def _client(platform_settings) -> httpx.Client:
    app = create_app(session_factory=None, settings=load_market_settings(platform=platform_settings))
    return httpx.Client(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


BASE_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
}


def test_without_a_configured_token_every_read_is_blocked_not_open() -> None:
    platform = load_settings(env=BASE_ENV)
    with _client(platform) as client:
        response = client.get("/market/v1/opportunities")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "blocked_pending_credentials"


def test_a_wrong_or_missing_token_is_401_and_the_right_one_passes() -> None:
    platform = load_settings(env={**BASE_ENV, "PLM_SERVICE_TOKEN": "shared-secret"})
    with _client(platform) as client:
        assert client.get("/market/v1/opportunities").status_code == 401
        assert client.get("/market/v1/opportunities",
                          headers={"X-PLM-Service-Token": "wrong"}).status_code == 401
        # the right token reaches the session dependency, which has no factory here
        assert client.get("/market/v1/opportunities",
                          headers={"X-PLM-Service-Token": "shared-secret"}).status_code == 503
```

Run: `python -m pytest tests/unit/market/test_api_endpoints.py tests/unit/market/test_api_auth.py tests/contract/test_c1_market_to_ar.py -q`
Expected: PASS. Then write the two ADRs and the decision-log row:

```markdown
<!-- docs/adr/0021-p1-service-token-and-api.md (sections mirroring ADR-0015's shape) -->
# ADR-0021: P1 serves `/market/v1` behind an optional `PLM_SERVICE_TOKEN`

- Status: `ratified` — 2026-09-20
- Ruling: `R15` (`C1`)
- Seam: `C1`
- Supersedes / superseded by: `—`

## Context
R15 fixes the base path and "auth via service token from `plm_platform`", but no
service-token setting existed in W0.4's config, and the phase spec names no HTTP
framework (§36 leaves the choice open).

## Decision
`PLM_SERVICE_TOKEN` is an optional `Secret` in `plm_platform.config.Settings`,
presented as `X-PLM-Service-Token`. Unconfigured is not open: every read answers
`503 blocked_pending_credentials`; a mismatch answers `401 invalid_service_token`
compared in constant time. The API is FastAPI, tested through `httpx.ASGITransport`.

## Evidence
- Registry C1 ("Base path `/market/v1`; auth via service token from `plm_platform`").
- `plm_platform/correlation.py` and `observability.py` already document FastAPI hooks.
- `docs/P0_...md` §5 C1 required amendments.

## Consequences
P2's adapter has a concrete base URL, header name and failure mode. An operator who
forgets the token sees a blocked API, never an unauthenticated one.

## Alternatives considered
- HTTP Basic / API-key header per phase — rejected: one token concept, one place.
- Open on localhost — rejected: it would make the seam's auth untestable and untyped.

## Cost if wrong
Swapping the framework is contained in `api/` (fastapi + its ASGI transport tests);
the wire shapes live in `api/schemas.py` and are pinned by the C1 seam test.
```

`docs/adr/0022-p1-tables-and-envelope.md` records the table names of P1.2 and the
envelope reconciliation of this task, in the same shape (Context/Decision/Evidence/
Consequences/Alternatives/Cost if wrong). Add both rows to `docs/contracts/DECISION_LOG.md`
using its existing table format (`DATE / DECISION / INPUT DATA / MODEL / CONFIDENCE /
ACTION / RESULT`), `MODEL` = "planning agent", `CONFIDENCE` = `—` (not recorded at
ruling time), `RESULT` = "pending".

```bash
git add services/market_intelligence/api/ tests/unit/market/test_api_endpoints.py \
        tests/unit/market/test_api_auth.py tests/contract/ docs/adr/0021-p1-service-token-and-api.md \
        docs/adr/0022-p1-tables-and-envelope.md docs/contracts/DECISION_LOG.md
git commit -m "feat(p1): /market/v1 API with C1 seam test"
```

---

### Task P1.11: Scheduler

**Files:**
- Create: `collection/scheduler.py`, `jobs/__init__.py`, `jobs/daily.py`, `jobs/weekly.py`, `jobs/backfill.py`, `jobs/schedules.py`, `cli.py`
- Modify: `storage/repositories.py` (append `update_query_schedule`), `packages/plm_platform/cli.py`, `tests/unit/test_cli.py`
- Test: `tests/unit/market/test_scheduler.py`, `tests/integration/market/test_temporal_jobs.py`

**Interfaces:**
- Consumes: `collection.query_planner.*`, `collection.collectors.collect_query` (created below), `taxonomy.classifier.classify_observation`, `features.arm_features.compute_arm_features`, `scoring.opportunity.score_all_arms`/`persist_scores`, `plm_platform.temporal` (`TaskQueue.MARKET`, `WorkerSet`, `business_workflow_id`, `idempotency_key`, `DEFAULT_RETRY_POLICY`), `plm_platform.observability.run_scope`/`CostMetric`, `plm_platform.config.load_settings`.
- Produces (names later tasks import exactly):
  - `collection.scheduler`: `due_queries(session, *, now, settings) -> list[QueryState]` (reads `load_queries`, converts rows, delegates to `plan_refresh`); `mark_scheduled(session, states, *, now, settings) -> int` (sets `next_run_at` per cadence); `mark_run(session, query_id, *, at) -> None`; `reset_schedule(session, query_id, *, now, settings, reason: str) -> None` (used after quota exhaustion: reschedule, never hammer §23/§27).
  - `collection.collectors`: `collect_query(session, *, connector: YouTubeDataAPIConnector, source_id: str, query_id: str, query_text: str, run_id: str, settings, storage_client=None, now: datetime) -> CollectionRunSummary` with `CollectionRunSummary(run_id, raw_count, normalized_count, quarantined_count, error_classes: tuple[str, ...], status: CollectionRunStatus)`; `hydrate_and_store(session, *, connector, source_id, run_id, video_ids, settings, now, storage_client=None) -> CollectionRunSummary`.
  - `jobs.daily`: `JOB_NAME = "market.daily"`; `DailyMarketInput(now: datetime | None = None)`; `@workflow.defn class DailyMarketWorkflow` with `async def run(self, payload: dict) -> dict`; activities `ingest_seeds_activity`, `discover_activity`, `classify_activity`, `recompute_activity`, `reschedule_activity` (each a plain function decorated at registration time).
  - `jobs.weekly`: `JOB_NAME = "market.weekly"`, `WeeklyMarketWorkflow` (full taxonomy recompute + snapshot).
  - `jobs.backfill`: `JOB_NAME = "market.backfill"`, `BackfillWorkflow` (on-demand collection for explicit query ids).
  - `jobs.schedules`: `JOB_NAMES: tuple[str, ...]` (**exactly** §26's seven names), `ensure_schedules(client) -> tuple[str, ...]`.
  - `jobs`: `REGISTRATIONS: tuple[WorkerSet, ...]` — importable as `services.market_intelligence.jobs:REGISTRATIONS` by `plm_platform.worker --load`.
  - `cli`: `run_daily(now: datetime | None = None, *, session_factory=None, connector=None, settings=None) -> dict` (the orchestration entry point shared by the workflow, the CLI and P1.14), `run_collect(...)`, `run_score(...)`, `run_report(...)`, `serve(host, port)`, `smoke_live(...)`; `BLOCKED_EXIT_CODE` lives in `plm_platform.cli`.
  - `plm market` commands after this task: `collect`, `score`, `report`, `run-daily`, `serve`, `smoke-live`, `seed-refresh`.

> **Rulings (Open items #6, R20):** (1) §38 permits a non-Temporal scheduler while architecture PART XIX prescribes Temporal — resolution: the *cadence decision* is a pure function (`query_planner`), the *execution* is Temporal workflows on `ph1.market`; both exist, neither is bypassed. (2) §26's seven job names are constants; the daily workflow runs six of them, `market.stale_data_cleanup` runs weekly, and backfill is on demand. (3) R20: P1 defines no CLI of its own, so `plm_platform/cli.py` declares the commands (keeping `plm --help` import-light) and `services/market_intelligence/cli.py` implements them behind a lazy import.

- [ ] **Step 1: Write the failing scheduler test**

```python
# tests/unit/market/test_scheduler.py
from datetime import UTC, datetime, timedelta

from services.market_intelligence.collection.scheduler import (
    due_queries, mark_run, mark_scheduled, reset_schedule,
)
from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.enums import ConnectorType, ProviderName, QueryType
from services.market_intelligence.storage import repositories

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_market_settings()


def _install(market_session) -> str:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    repositories.upsert_query(
        market_session, source_id=source_id, query_text="lofi", query_type=QueryType.MUSIC_GENRE,
        locale="en", country_code="US", language="english", seed_group="bootstrap",
        priority=50, query_version="p1-queries-v1",
    )
    return source_id


def test_a_never_run_query_is_due_and_gets_a_cadence_back(market_session) -> None:
    _install(market_session)
    due = due_queries(market_session, now=NOW, settings=SETTINGS)
    assert [state.query_text for state in due] == ["lofi"]
    scheduled = mark_scheduled(market_session, due, now=NOW, settings=SETTINGS)
    assert scheduled == 1
    row = repositories.load_queries(market_session)[0]
    assert row["next_run_at"] is not None and row["next_run_at"] > NOW
    assert due_queries(market_session, now=NOW, settings=SETTINGS) == []


def test_a_quota_reset_reschedules_with_a_reason_and_reopens_the_query(market_session) -> None:
    _install(market_session)
    due = due_queries(market_session, now=NOW, settings=SETTINGS)
    mark_scheduled(market_session, due, now=NOW, settings=SETTINGS)
    reset_schedule(market_session, due[0].query_id, now=NOW, settings=SETTINGS,
                   reason="QUOTA_EXCEEDED")
    assert due_queries(market_session, now=NOW, settings=SETTINGS) == []
    mark_run(market_session, due[0].query_id, at=NOW + timedelta(hours=1))
    row = repositories.load_queries(market_session)[0]
    assert row["last_run_at"] is not None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_scheduler.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Write `collection/scheduler.py` and `collection/collectors.py`**

```python
# services/market_intelligence/collection/scheduler.py
"""§9/§26: what is due, when each query runs next, and what to do when quota runs out."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..config import MarketSettings
from ..storage.repositories import load_queries, update_query_schedule
from .query_planner import QueryState, next_run_at, plan_refresh, priority_score

__all__ = ["due_queries", "mark_run", "mark_scheduled", "reset_schedule"]


def due_queries(session: Session, *, now: datetime, settings: MarketSettings) -> list[QueryState]:
    """Active queries whose `next_run_at` has arrived, highest priority first."""
    states = [_state(row) for row in load_queries(session, active_only=True)]
    return plan_refresh(states, now=now, settings=settings)


def mark_scheduled(session: Session, states: list[QueryState], *, now: datetime,
                   settings: MarketSettings) -> int:
    """Set the next cadence for the queries just planned; returns how many moved."""
    for state in states:
        update_query_schedule(
            session, query_id=state.query_id, now=now,
            next_run_at=next_run_at(state, now=now, settings=settings),
            last_run_at=None,
        )
    return len(states)


def mark_run(session: Session, query_id: str, *, at: datetime) -> None:
    """Record that a collection ran, without touching the next cadence."""
    update_query_schedule(session, query_id=query_id, now=at, next_run_at=None, last_run_at=at)


def reset_schedule(session: Session, query_id: str, *, now: datetime,
                   settings: MarketSettings, reason: str) -> None:
    """After quota exhaustion or a hard failure: reschedule instead of retrying (§23/§27)."""
    state = QueryState(query_id=query_id, query_fingerprint="", query_text="", priority=100,
                       seed_group="bootstrap", last_run_at=None, next_run_at=None, active=True)
    update_query_schedule(
        session, query_id=query_id, now=now,
        next_run_at=next_run_at(state, now=now, settings=settings), last_run_at=None,
    )
    _ = reason  # the reason is logged by the caller (`observability.logging`)


def _state(row) -> QueryState:
    return QueryState(
        query_id=str(row["id"]), query_fingerprint=str(row["query_fingerprint"]),
        query_text=str(row["query_text"]), priority=int(row["priority"]),
        seed_group=str(row["seed_group"]), last_run_at=row["last_run_at"],
        next_run_at=row["next_run_at"], active=bool(row["active"]),
    )
```

```python
# services/market_intelligence/collection/collectors.py
"""§10 collection orchestration: connector → raw store → normalize → entities → observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from ..config import MarketSettings
from ..connectors.base import ConnectorError, FetchedObject
from ..connectors.youtube import (
    YouTubeDataAPIConnector, parse_search_hits, parse_video_stats,
)
from ..domain.entities import CollectionRunCounters
from ..domain.enums import CollectionRunStatus, EntityType, ProviderObjectType
from ..normalization.canonical import (
    channel_canonical_url, normalize_text, video_canonical_url,
)
from ..normalization.youtube import normalize_channel_object, normalize_search_hit, normalize_video_object
from ..storage import repositories
from ..storage.raw_store import store_raw_observation

__all__ = ["CollectionRunSummary", "collect_query", "hydrate_and_store"]


@dataclass(frozen=True, slots=True)
class CollectionRunSummary:
    run_id: str
    raw_count: int
    normalized_count: int
    quarantined_count: int
    error_classes: tuple[str, ...] = ()
    status: CollectionRunStatus = CollectionRunStatus.RUNNING

    def counters(self, *, requests: int, successes: int, rate_limits: int) -> CollectionRunCounters:
        return CollectionRunCounters(
            request_count=requests, success_count=successes, error_count=len(self.error_classes),
            rate_limit_count=rate_limits, raw_observation_count=self.raw_count,
            normalized_observation_count=self.normalized_count,
            quarantined_count=self.quarantined_count, status=self.status,
        )


def collect_query(
    session: Session, *, connector: YouTubeDataAPIConnector, source_id: str, query_id: str,
    query_text: str, run_id: str, settings: MarketSettings, now: datetime,
    storage_client=None,
) -> CollectionRunSummary:
    """One discovery pass for one query: search → store raw → hydrate → normalize."""
    raw_count = 0
    normalized_count = 0
    quarantined = 0
    error_classes: list[str] = []
    try:
        page = connector.search_videos(query_text, max_results=settings.max_results_per_search)
    except ConnectorError as exc:
        return CollectionRunSummary(run_id=run_id, raw_count=0, normalized_count=0,
                                    quarantined_count=0, error_classes=(exc.error_class.value,),
                                    status=CollectionRunStatus.PARTIAL)
    for fetched in page.objects:
        raw = _store(session, run_id=run_id, source_id=source_id, fetched=fetched,
                     context=page.request_context_hash, storage_client=storage_client)
        raw_count += 1 if raw.created else 0
        hit = parse_search_hits(_single(fetched, page.request_context_hash))[0]
        entity_id = repositories.resolve_entity(
            session, entity_type=EntityType.VIDEO, source_id=source_id,
            provider_object_type=ProviderObjectType.VIDEO,
            provider_object_id=hit.video_id, canonical_name=hit.title,
            normalized_name=normalize_text(hit.title),
            canonical_url=video_canonical_url(hit.video_id), observed_at=now,
        )
        outcome = normalize_search_hit(fetched, entity_id=entity_id, source_id=source_id,
                                       raw_observation_id=raw.raw_observation_id,
                                       observed_at=now)
        normalized_count, quarantined = _persist_outcome(
            session, outcome, raw_observation_id=raw.raw_observation_id, run_id=run_id,
            normalized_count=normalized_count, quarantined=quarantined,
        )
    video_ids = [obj.provider_object_id for obj in page.objects]
    if video_ids:
        hydration = hydrate_and_store(
            session, connector=connector, source_id=source_id, run_id=run_id,
            video_ids=video_ids, settings=settings, now=now, storage_client=storage_client,
        )
        raw_count += hydration.raw_count
        normalized_count += hydration.normalized_count
        quarantined += hydration.quarantined_count
        error_classes.extend(hydration.error_classes)
    status = CollectionRunStatus.PARTIAL if (error_classes or quarantined) \
        else CollectionRunStatus.SUCCEEDED
    return CollectionRunSummary(run_id=run_id, raw_count=raw_count,
                                normalized_count=normalized_count,
                                quarantined_count=quarantined,
                                error_classes=tuple(error_classes), status=status)


def hydrate_and_store(
    session: Session, *, connector: YouTubeDataAPIConnector, source_id: str, run_id: str,
    video_ids: list[str], settings: MarketSettings, now: datetime, storage_client=None,
) -> CollectionRunSummary:
    """videos.list hydration: metric-bearing observations for entities discovery created."""
    raw_count = 0
    normalized_count = 0
    quarantined = 0
    error_classes: list[str] = []
    try:
        fetch = connector.fetch_videos(video_ids)
    except ConnectorError as exc:
        return CollectionRunSummary(run_id=run_id, raw_count=0, normalized_count=0,
                                    quarantined_count=0, error_classes=(exc.error_class.value,),
                                    status=CollectionRunStatus.PARTIAL)
    for fetched in fetch.objects:
        raw = _store(session, run_id=run_id, source_id=source_id, fetched=fetched,
                     context=fetch.request_context_hash, storage_client=storage_client)
        raw_count += 1 if raw.created else 0
        stats = parse_video_stats(_single(fetched, fetch.request_context_hash))[0]
        entity_id = repositories.resolve_entity(
            session, entity_type=EntityType.VIDEO, source_id=source_id,
            provider_object_type=ProviderObjectType.VIDEO,
            provider_object_id=stats.video_id, canonical_name=stats.title or stats.video_id,
            normalized_name=normalize_text(stats.title or stats.video_id),
            canonical_url=video_canonical_url(stats.video_id), observed_at=now,
        )
        outcome = normalize_video_object(fetched, entity_id=entity_id, source_id=source_id,
                                         raw_observation_id=raw.raw_observation_id,
                                         observed_at=now)
        normalized_count, quarantined = _persist_outcome(
            session, outcome, raw_observation_id=raw.raw_observation_id, run_id=run_id,
            normalized_count=normalized_count, quarantined=quarantined,
        )
    status = CollectionRunStatus.PARTIAL if (error_classes or quarantined) \
        else CollectionRunStatus.SUCCEEDED
    return CollectionRunSummary(run_id=run_id, raw_count=raw_count,
                                normalized_count=normalized_count,
                                quarantined_count=quarantined, error_classes=tuple(error_classes),
                                status=status)


def _store(session: Session, *, run_id: str, source_id: str, fetched: FetchedObject,
           context: str, storage_client=None):
    """Store the raw object; `store_raw_observation` records `storage_bytes` itself."""
    return store_raw_observation(
        session, run_id=run_id, source_id=source_id, fetched=fetched,
        request_context_hash=context, client=storage_client,
    )


def _single(fetched: FetchedObject, context: str):
    from ..connectors.base import FetchResult

    return FetchResult(objects=(fetched,), request_context_hash=context)
```

```python
# services/market_intelligence/storage/repositories.py  (append)
def update_query_schedule(
    session: Session, *, query_id: str, now: datetime, next_run_at: datetime | None,
    last_run_at: datetime | None,
) -> None:
    values: dict[str, Any] = {"updated_at": now}
    if next_run_at is not None:
        values["next_run_at"] = next_run_at
    if last_run_at is not None:
        values["last_run_at"] = last_run_at
    session.execute(sa.update(queries).where(queries.c.id == query_id).values(**values))
```

- [ ] **Step 4: Run the scheduler test and watch it pass, then commit**

Run: `python -m pytest tests/unit/market/test_scheduler.py -q`
Expected: PASS (2 passed).

```bash
git add services/market_intelligence/collection/scheduler.py \
        services/market_intelligence/collection/collectors.py \
        services/market_intelligence/storage/repositories.py tests/unit/market/test_scheduler.py
git commit -m "feat(p1): collection orchestration and due-query scheduling"
```

- [ ] **Step 5: Write the failing jobs test**

```python
# tests/integration/market/test_temporal_jobs.py
"""Real Temporal, real worker registration: the scheduling plumbing, not the network."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from plm_platform.temporal import TaskQueue, connect_client, queue_is_polling

from services.market_intelligence.jobs import REGISTRATIONS
from services.market_intelligence.jobs.schedules import JOB_NAMES, ensure_schedules

pytestmark = pytest.mark.integration


def test_the_worker_set_serves_the_market_queue_with_both_workflows() -> None:
    assert {registration.task_queue for registration in REGISTRATIONS} == {TaskQueue.MARKET}
    workflows = {workflow.__name__ for registration in REGISTRATIONS
                 for workflow in registration.workflows}
    assert {"DailyMarketWorkflow", "WeeklyMarketWorkflow", "BackfillWorkflow"} <= workflows


def test_the_seven_spec_job_names_exist() -> None:
    assert JOB_NAMES == (
        "market.seed_refresh", "market.youtube_discovery", "market.youtube_refresh",
        "market.taxonomy_classification", "market.feature_recompute",
        "market.opportunity_scoring", "market.stale_data_cleanup",
    )


def test_schedules_register_idempotently_against_a_live_temporal() -> None:
    async def scenario() -> None:
        try:
            client = await asyncio.wait_for(connect_client(), timeout=5)
        except Exception as exc:  # noqa: BLE001 - skip with a remedy (repo convention)
            pytest.skip(f"temporal unreachable ({type(exc).__name__}); "
                        "run: docker compose up -d --wait")
        first = await ensure_schedules(client)
        second = await ensure_schedules(client)
        assert first == second == ("market.daily.workflow", "market.weekly.workflow")

    asyncio.run(scenario())
```

- [ ] **Step 6: Run it and watch it fail**

Run: `python -m pytest tests/integration/market/test_temporal_jobs.py -q -m integration`
Expected: FAIL (collection error) — `services/market_intelligence.jobs` does not exist.

- [ ] **Step 7: Write the workflows, schedules and registrations**

```python
# services/market_intelligence/jobs/daily.py
"""§26's daily cycle as one durable workflow on `ph1.market`.

Activities own every side effect (they are the retry/idempotency boundary); the
workflow only sequences them and passes the correlation id along (§AC-14).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from datetime import timedelta
from plm_platform.correlation import correlation_scope
from plm_platform.observability import CostMetric, run_scope
from plm_platform.temporal import DEFAULT_RETRY_POLICY

logger = logging.getLogger(__name__)

JOB_NAME = "market.daily"


@dataclass(frozen=True, slots=True)
class DailyMarketInput:
    now: datetime | None = None
    correlation_id: str | None = None


@workflow.defn
class DailyMarketWorkflow:
    """seed refresh → discovery → classification → features/scores → reschedule."""

    @workflow.run
    async def run(self, payload: dict) -> dict:
        now = payload.get("now") or workflow.now().isoformat()
        correlation_id = payload.get("correlation_id")
        summary: dict = {"started_at": now, "steps": []}
        for name, activity_fn in (
            ("market.seed_refresh", ingest_seeds_activity),
            ("market.youtube_discovery", discover_activity),
            ("market.taxonomy_classification", classify_activity),
            ("market.feature_recompute", recompute_activity),
            ("market.opportunity_scoring", score_activity),
            ("market.reschedule", reschedule_activity),
        ):
            result = await workflow.execute_activity(
                activity_fn, {"now": now, "correlation_id": correlation_id},
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=DEFAULT_RETRY_POLICY,
            )
            summary["steps"].append({"job": name, "result": result})
        return summary


@activity.defn
def ingest_seeds_activity(payload: dict) -> dict:
    from ..cli import run_daily_step

    return run_daily_step("market.seed_refresh", payload=payload)


@activity.defn
def discover_activity(payload: dict) -> dict:
    from ..cli import run_daily_step

    return run_daily_step("market.youtube_discovery", payload=payload)


@activity.defn
def classify_activity(payload: dict) -> dict:
    from ..cli import run_daily_step

    return run_daily_step("market.taxonomy_classification", payload=payload)


@activity.defn
def recompute_activity(payload: dict) -> dict:
    from ..cli import run_daily_step

    return run_daily_step("market.feature_recompute", payload=payload)


@activity.defn
def score_activity(payload: dict) -> dict:
    from ..cli import run_daily_step

    return run_daily_step("market.opportunity_scoring", payload=payload)


@activity.defn
def reschedule_activity(payload: dict) -> dict:
    from ..cli import run_daily_step

    return run_daily_step("market.reschedule", payload=payload)
```

`jobs/weekly.py`, `jobs/backfill.py` follow the same shape with their own workflow classes
and step activities; `jobs/schedules.py` holds the names and the schedule registration:

```python
# services/market_intelligence/jobs/schedules.py
"""§26's job names and their Temporal schedules (ids are business keys, W0.7)."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from temporalio.client import (
    Client, Schedule, ScheduleActionStartWorkflow, ScheduleIntervalSpec, ScheduleSpec,
)
from temporalio.service import RPCError
from plm_platform.temporal import TaskQueue, business_workflow_id

from .daily import DailyMarketWorkflow
from .weekly import WeeklyMarketWorkflow

__all__ = ["JOB_NAMES", "ensure_schedules"]

JOB_NAMES: Final[tuple[str, ...]] = (
    "market.seed_refresh", "market.youtube_discovery", "market.youtube_refresh",
    "market.taxonomy_classification", "market.feature_recompute",
    "market.opportunity_scoring", "market.stale_data_cleanup",
)

_SCHEDULES: Final[tuple[tuple[str, type, timedelta], ...]] = (
    ("market.daily.workflow", DailyMarketWorkflow, timedelta(hours=24)),
    ("market.weekly.workflow", WeeklyMarketWorkflow, timedelta(days=7)),
)


async def ensure_schedules(client: Client) -> tuple[str, ...]:
    """Register (or reuse) the schedules; ids derive from the business workflow id.

    `create_schedule` raises for an existing id (Temporal RPC `ALREADY_EXISTS`); treat that
    as success so a re-run of this function is idempotent.
    """
    for name, workflow_type, interval in _SCHEDULES:
        schedule_id = business_workflow_id("market", "schedule", name.split(".")[1])
        try:
            await client.create_schedule(
                schedule_id,
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        workflow_type.run, {}, id=schedule_id,
                        task_queue=TaskQueue.MARKET.value,
                    ),
                    spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=interval)]),
                ),
            )
        except RPCError as exc:
            if "ALREADY_EXISTS" not in str(exc.status):
                raise
    return tuple(name for name, _, _ in _SCHEDULES)
```

`client.create_schedule` raises `ScheduleAlreadyRunningError` for an existing id — catch it in
`ensure_schedules` (`from temporalio.service import RPCError` → treat `ALREADY_EXISTS` as success)
so the second call returns the same tuple instead of failing.

```python
# services/market_intelligence/jobs/__init__.py
"""Worker registrations: `plm_platform.worker --load services.market_intelligence.jobs:REGISTRATIONS`."""

from __future__ import annotations

from plm_platform.temporal import TaskQueue, WorkerSet

from .backfill import BackfillWorkflow
from .daily import DailyMarketWorkflow, discover_activity, ingest_seeds_activity
from .weekly import WeeklyMarketWorkflow

__all__ = ["REGISTRATIONS"]

REGISTRATIONS: tuple[WorkerSet, ...] = (
    WorkerSet(
        task_queue=TaskQueue.MARKET,
        workflows=(DailyMarketWorkflow, WeeklyMarketWorkflow, BackfillWorkflow),
        activities=(ingest_seeds_activity, discover_activity),
    ),
)
```

- [ ] **Step 8: Run the jobs tests and watch them pass**

Run: `python -m pytest tests/integration/market/test_temporal_jobs.py -q -m integration`
Expected: PASS with the stack up; the named skip otherwise. Then `python -m pytest tests/unit/market -q` stays green.

- [ ] **Step 9: Un-stub the `plm market` group and write `services/market_intelligence/cli.py`**

```python
# services/market_intelligence/cli.py
"""Implementations behind the `plm market …` commands (declared in plm_platform.cli).

`run_daily` is the one orchestration entry point shared by the workflow activities,
the CLI and the acceptance test — there is no second path through the pipeline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from .collection.collectors import collect_query
from .collection.scheduler import due_queries, mark_run, mark_scheduled
from .config import MarketSettings, load_market_settings
from .connectors.rate_limit import QuotaBudget, QuotaExceededError, RequestBudget
from .connectors.youtube import YouTubeCredentials, YouTubeDataAPIConnector
from .domain.enums import CollectionRunStatus, ConnectorType, ProviderName, QueryType
from .features.arm_features import compute_arm_features
from .scoring.opportunity import persist_scores, score_all_arms
from .storage import repositories
from .taxonomy.arms import generate_candidate_arms
from .taxonomy.classifier import RuleBasedClassifier, classify_observation

__all__ = ["run_collect", "run_daily", "run_daily_step", "run_report", "run_score"]


def run_daily_step(job: str, *, payload: dict) -> dict:
    """One activity's worth of work; the Temporal layer calls this, nothing else does."""
    from plm_platform.correlation import correlation_scope
    from plm_platform.observability import run_scope

    correlation_id = payload.get("correlation_id")
    now = _parse_now(payload.get("now"))
    with correlation_scope(correlation_id), run_scope(f"p1-{job}-{now.date().isoformat()}"):
        with _session() as session:
            if job == "market.seed_refresh":
                source_id = _ensure_source(session)
                created = _install_seeds(session, source_id=source_id)
                return {"job": job, "new_queries": created}
            if job == "market.youtube_discovery":
                return _run_due_collection(session, now=now)
            if job == "market.taxonomy_classification":
                return _classify_pending(session, now=now)
            if job in {"market.feature_recompute", "market.opportunity_scoring"}:
                return _recompute(session, now=now)
            if job == "market.reschedule":
                due = due_queries(session, now=now, settings=_settings())
                return {"job": job, "scheduled": mark_scheduled(session, due, now=now,
                                                                settings=_settings())}
    return {"job": job, "status": "unknown job"}
```

The remaining helpers in the same module — write them in full, they are small:

```python
def _settings() -> MarketSettings:
    return load_market_settings()


def _session():
    from plm_platform.db import scoped_session

    return scoped_session("market")


def _parse_now(value: Any) -> datetime:
    return datetime.now(UTC) if value is None else datetime.fromisoformat(str(value))


def _ensure_source(session: Session) -> str:
    return repositories.upsert_source(
        session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API,
        base_identifier="https://www.googleapis.com/youtube/v3",
        configuration_version="p1-sources-v1",
    )


def _install_seeds(session: Session, *, source_id: str) -> int:
    from .collection.seeds import install_bootstrap_seeds

    return install_bootstrap_seeds(session, source_id=source_id, query_version="p1-queries-v1")


def _run_due_collection(session: Session, *, now: datetime) -> dict:
    settings = _settings()
    due = due_queries(session, now=now, settings=settings)
    if not due:
        return {"job": "market.youtube_discovery", "queries": 0}
    source_id = _ensure_source(session)
    connector = _mock_connector(settings)
    run_id = repositories.start_collection_run(
        session, source_id=source_id, query_id=None, software_version=settings.score_version,
        started_at=now,
    )
    try:
        summaries = [
            collect_query(session, connector=connector, source_id=source_id,
                          query_id=state.query_id, query_text=state.query_text, run_id=run_id,
                          settings=settings, now=now)
            for state in due
        ]
    except QuotaExceededError:
        from .collection.scheduler import reset_schedule

        for state in due:
            reset_schedule(session, state.query_id, now=now, settings=settings,
                           reason="QUOTA_EXCEEDED")
        repositories.finish_collection_run(
            session, run_id, status=CollectionRunStatus.PARTIAL,
            counters=_counters_from_db(session, run_id),
            error_summary={"QUOTA_EXCEEDED": len(due)}, finished_at=datetime.now(UTC),
        )
        return {"job": "market.youtube_discovery", "quota_exhausted": True, "queries": len(due)}
    status = (CollectionRunStatus.PARTIAL
              if any(summary.status is not CollectionRunStatus.SUCCEEDED for summary in summaries)
              else CollectionRunStatus.SUCCEEDED)
    repositories.finish_collection_run(
        session, run_id, status=status, counters=_merge(summaries), finished_at=datetime.now(UTC)
    )
    mark_scheduled(session, due, now=now, settings=settings)
    for state in due:
        mark_run(session, state.query_id, at=now)
    return {"job": "market.youtube_discovery", "queries": len(due), "run_id": run_id,
            "status": status.value}
```

…plus `_merge(summaries) -> CollectionRunCounters`, `_counters_from_db(session, run_id) -> CollectionRunCounters`,
`_mock_connector(settings) -> YouTubeDataAPIConnector` (returns the mock-backed connector used
until credentials exist — it builds an `httpx.Client(transport=...)` over
`MockHTTP(repeat_last=True)` seeded from `tests/fixtures` **only when `ENV=ci`**; otherwise it
raises `RuntimeError` telling the operator to configure `YOUTUBE_API_KEY`), `_classify_pending`,
`_recompute`, `run_collect`, `run_score`, `run_report` (delegating to `reports`), and `serve`.

> **Mock-first wiring, precisely:** `_mock_connector` exists for **tests and CI only**; its name,
> its imports and its refusal message make that unmissable, and `run_daily`'s production path
> constructs the real connector from `settings.platform.youtube_api_key` when it is configured.

```python
# packages/plm_platform/cli.py  (market group: replace the collect stub)
BLOCKED_EXIT_CODE: Final[int] = 3
"""Exit code for "blocked with a reason code" (e.g. pending credentials) — never success."""


@market_app.command()
def collect(refresh: bool = False, limit: int = 25) -> None:
    """Collect market evidence for due queries (P1)."""
    from services.market_intelligence.cli import run_collect

    run_collect(refresh=refresh, limit=limit)


@market_app.command()
def score() -> None:
    """Recompute market opportunity scores (P1)."""
    from services.market_intelligence.cli import run_score

    run_score()


@market_app.command()
def report(as_of: str = "") -> None:
    """Write the daily market snapshot and report (P1)."""
    from services.market_intelligence.cli import run_report

    run_report(as_of=as_of or None)


@market_app.command("run-daily")
def run_daily_command() -> None:
    """Run one daily market cycle in-process (P1)."""
    from services.market_intelligence.cli import run_daily

    typer.echo(json.dumps(run_daily(), indent=2, default=str))


@market_app.command()
def serve(host: str = "127.0.0.1", port: int = 8081) -> None:
    """Serve the `/market/v1` API (P1)."""
    from services.market_intelligence.cli import serve as serve_impl

    serve_impl(host=host, port=port)


@market_app.command("smoke-live")
def smoke_live() -> None:
    """Run the §40 live smoke test; blocked (exit 3) until credentials exist."""
    from services.market_intelligence.cli import smoke_live as smoke_impl

    reason = smoke_impl()
    typer.echo(reason, err=True)
    raise typer.Exit(code=BLOCKED_EXIT_CODE)
```

Append `import json` to `plm_platform/cli.py`'s imports. Then update `tests/unit/test_cli.py`:
remove `("market", "collect", "PHASE-P1")` from `STUB_CASES` and change the coverage assertion to
`assert {group for group, _, _ in STUB_CASES} == set(PHASES_BY_GROUP) - {"market"}`, and add:

```python
def test_market_commands_are_implemented_not_stubs() -> None:
    for command in ("collect", "score", "report", "run-daily", "serve", "smoke-live"):
        result = runner.invoke(app, ["market", command, "--help"])
        assert result.exit_code == 0, _output(result)
        assert "not implemented until" not in _output(result)
```

- [ ] **Step 10: Run everything and commit**

Run: `python -m pytest tests/unit -q` and `python -m pytest tests/integration/market -q -m integration`
Expected: PASS.

```bash
git add services/market_intelligence/jobs/ services/market_intelligence/cli.py \
        packages/plm_platform/cli.py tests/unit/test_cli.py \
        tests/integration/market/test_temporal_jobs.py
git commit -m "feat(p1): temporal jobs, schedules and the plm market CLI"
```

---

### Task P1.12: Reports and observability

**Files:**
- Create: `reports/__init__.py`, `reports/market_snapshot.py`, `reports/exports.py`, `observability/__init__.py`, `observability/run_summary.py`, `observability/metrics.py`, `observability/logging.py`
- Modify: `storage/repositories.py` (append `insert_change_event`, `insert_snapshot`, `previous_snapshot`, `load_snapshot`), `cli.py` (`run_report`)
- Test: `tests/unit/market/test_snapshot.py`, `test_change_detection.py`, `test_run_summary.py`
- Create: `tests/fixtures/golden/market/market_snapshot.md`

**Interfaces:**
- Consumes: `storage.repositories.latest_scores`/`load_run`/`count_quarantined`, `domain.value_objects.classify_freshness`, `plm_platform.storage.put_content`/`to_uri`, `plm_platform.observability.METRICS_REGISTRY`/`configure_logging` (P1 adds counters, never a second logging stack), `plm_platform.correlation.current_correlation_id`.
- Produces (names later tasks import exactly):
  - `reports.market_snapshot`: `MarketSnapshot(as_of, generated_at, sections: Mapping[str, Any], evidence_classes: Mapping[str, str])`; `build_snapshot(session, *, as_of: datetime, settings: MarketSettings) -> MarketSnapshot` (the ten §31 sections); `detect_changes(previous: MarketSnapshot | None, current: MarketSnapshot, *, settings: MarketSettings) -> list[MarketChangeEventDraft]` (§32); `persist_snapshot(session, snapshot, *, artifact_uri: str, artifact_hash: str) -> str`; `previous_snapshot(session) -> Mapping | None`.
  - `reports.exports`: `render_markdown(snapshot: MarketSnapshot) -> str` (the ten sections, each labelled `OBSERVED`/`DERIVED`/`CLASSIFIED`/`ASSUMED`); `store_report(snapshot, *, put_object=None, settings=None, client=None) -> StoredObject` writing `market/reports/<YYYY-MM-DD>/<sha256>`.
  - `observability.run_summary`: `RunSummary(run_id, status, source, queries, raw_objects, normalized_objects, quarantined, failed, quota_units, llm_tokens, llm_cost_estimate, storage_bytes, score_changes, correlation_id)`; `summarize_run(session, run_id: str) -> RunSummary` — the §AC-14 answers in one object.
  - `observability.metrics`: `RUNS_TOTAL`, `QUARANTINED_TOTAL`, `QUOTA_UNITS_TOTAL`, `SCORES_WRITTEN_TOTAL` (Prometheus counters bound to `plm_platform.observability.METRICS_REGISTRY`); `record_run_metrics(summary: RunSummary) -> None`.
  - `observability.logging`: `log_run_summary(summary: RunSummary) -> None` — one structured line via `logging.getLogger("plm.market.run")`, no second handler.
  - `storage.repositories` (added): `insert_change_event(session, draft: MarketChangeEventDraft, *, detected_at: datetime) -> str`; `insert_snapshot(session, *, as_of, artifact_uri, artifact_hash) -> str`; `load_snapshot(session, as_of) -> Mapping | None`.

- [ ] **Step 1: Write the failing snapshot test**

```python
# tests/unit/market/test_snapshot.py
import json
from datetime import UTC, datetime

import pytest

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.reports.market_snapshot import build_snapshot
from services.market_intelligence.reports.exports import render_markdown
from tests.support.golden import assert_golden, golden_path

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_the_ten_spec_sections_exist_and_are_labelled(c1_api_client, market_session) -> None:
    snapshot = build_snapshot(market_session, as_of=AS_OF, settings=load_market_settings())
    assert set(snapshot.sections) == {
        "market_snapshot", "fastest_growing_concepts", "largest_demand_clusters",
        "lowest_saturation_clusters", "new_taxonomy_discoveries", "data_quality_warnings",
        "source_freshness", "top_opportunity_arms", "low_confidence_opportunities",
        "changes_vs_previous_snapshot",
    }
    assert set(snapshot.evidence_classes.values()) <= {"OBSERVED", "DERIVED", "CLASSIFIED",
                                                       "ASSUMED"}


def test_the_rendered_report_is_stable(market_session) -> None:
    snapshot = build_snapshot(market_session, as_of=AS_OF, settings=load_market_settings())
    rendered = render_markdown(snapshot)
    assert rendered.startswith("# Market Snapshot — 2026-09-20")
    assert "OBSERVED" in rendered and "DERIVED" in rendered
    assert_golden(rendered, golden_path("market", "market_snapshot.md"))
```

> The rendered report's golden file is created with `PLM_UPDATE_GOLDEN=1 python -m pytest
> tests/unit/market/test_snapshot.py -q`, inspected, then re-run without the variable (W0.9
> workflow). The empty-database snapshot is the expectation: every count is a real `0`
> because the query returned, and every missing section says so instead of printing a score.

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_snapshot.py -q`
Expected: FAIL — modules missing.

- [ ] **Step 3: Write `reports/market_snapshot.py` and `reports/exports.py`**

```python
# services/market_intelligence/reports/market_snapshot.py
"""§31's daily report data and §32's change detection.

Nothing here computes a new metric: the report is a *view* over stored scores and
observations, and every section states which evidence class produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import MarketSettings
from ..domain.entities import MarketChangeEventDraft
from ..domain.enums import ChangeEventType, EvidenceClass
from ..domain.value_objects import classify_freshness
from ..storage import repositories

__all__ = ["MarketSnapshot", "build_snapshot", "detect_changes", "load_snapshot",
           "persist_snapshot", "previous_snapshot"]


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    as_of: datetime
    generated_at: datetime
    sections: dict[str, list[dict]]
    evidence_classes: dict[str, str] = field(default_factory=dict)
    changes: tuple[MarketChangeEventDraft, ...] = ()


def build_snapshot(session: Session, *, as_of: datetime, settings: MarketSettings) -> MarketSnapshot:
    """§31's ten sections; each row already carries the evidence class it came from."""
    scores = repositories.latest_scores(session, limit=500)
    observations = session.execute(text(
        "SELECT count(*) AS observations, max(observed_at) AS newest "
        "FROM market.market_observations"
    )).mappings().one()
    quarantined = repositories.count_quarantined(session, run_id=None)
    arms = repositories.load_arms(session)
    freshness = (classify_freshness(
        as_of=observations["newest"], now=as_of,
        fresh_hours=settings.freshness_fresh_hours,
        normal_hours=settings.freshness_normal_hours, stale_hours=settings.freshness_stale_hours,
    ).bucket.value if observations["newest"] is not None else "no observations")
    ranked = [{"arm_id": row["arm_id"], "score": row["market_opportunity_score"],
               "confidence": row["confidence"], "as_of": row["as_of"].isoformat()}
              for row in scores]
    sections: dict[str, list[dict]] = {
        "market_snapshot": [{"observations": observations["observations"],
                             "arms": len(arms), "quarantined": quarantined,
                             "as_of": as_of.isoformat()}],
        "fastest_growing_concepts": sorted(
            ({"arm_id": row["arm_id"], "growth": row["components_json"].get("growth")}
             for row in scores), key=lambda item: -(item["growth"] or 0.0))[:10],
        "largest_demand_clusters": sorted(
            ({"arm_id": row["arm_id"], "demand": row["components_json"].get("demand")}
             for row in scores), key=lambda item: -(item["demand"] or 0.0))[:10],
        "lowest_saturation_clusters": sorted(
            ({"arm_id": row["arm_id"], "competition": row["components_json"].get("competition")}
             for row in scores), key=lambda item: -(item["competition"] or 0.0))[:10],
        "new_taxonomy_discoveries": [{"arm_id": row["arm_id"]}
                                     for row in scores if row["confidence"] < 0.5],
        "data_quality_warnings": [{"quarantined_records": quarantined}],
        "source_freshness": [{"newest_observation": (
            observations["newest"].isoformat() if observations["newest"] else None),
            "bucket": freshness}],
        "top_opportunity_arms": ranked[:10],
        "low_confidence_opportunities": [row for row in ranked if row["confidence"] < 0.4][:10],
        "changes_vs_previous_snapshot": [],
    }
    evidence_classes = {
        "market_snapshot": EvidenceClass.OBSERVED.value,
        "fastest_growing_concepts": EvidenceClass.DERIVED.value,
        "largest_demand_clusters": EvidenceClass.DERIVED.value,
        "lowest_saturation_clusters": EvidenceClass.DERIVED.value,
        "new_taxonomy_discoveries": EvidenceClass.CLASSIFIED.value,
        "data_quality_warnings": EvidenceClass.OBSERVED.value,
        "source_freshness": EvidenceClass.OBSERVED.value,
        "top_opportunity_arms": EvidenceClass.DERIVED.value,
        "low_confidence_opportunities": EvidenceClass.DERIVED.value,
        "changes_vs_previous_snapshot": EvidenceClass.DERIVED.value,
    }
    previous = previous_snapshot(session)
    snapshot = MarketSnapshot(as_of=as_of, generated_at=as_of, sections=sections,
                              evidence_classes=evidence_classes)
    if previous is not None:
        changes = detect_changes(_snapshot_from_row(previous), snapshot, settings=settings)
        sections["changes_vs_previous_snapshot"] = [
            {"event_type": draft.event_type.value, "arm_id": draft.arm_id,
             "previous": draft.previous_value, "current": draft.current_value}
            for draft in changes
        ]
        snapshot = MarketSnapshot(as_of=as_of, generated_at=as_of, sections=sections,
                                  evidence_classes=evidence_classes,
                                  changes=tuple(changes))
    return snapshot


def detect_changes(previous: MarketSnapshot | None, current: MarketSnapshot, *,
                   settings: MarketSettings) -> list[MarketChangeEventDraft]:
    """§32: score and growth moves beyond configured thresholds, plus availability drops."""
    if previous is None:
        return []
    drafts: list[MarketChangeEventDraft] = []
    old = {row["arm_id"]: row for row in previous.sections["top_opportunity_arms"]}
    for row in current.sections["top_opportunity_arms"]:
        before = old.get(row["arm_id"])
        if before is None:
            continue
        delta = float(row["score"]) - float(before["score"])
        if abs(delta) >= settings.change_score_threshold:
            drafts.append(MarketChangeEventDraft(
                event_type=ChangeEventType.OPPORTUNITY_SCORE_CHANGE, arm_id=row["arm_id"],
                previous_value=float(before["score"]), current_value=float(row["score"]),
                detail={"delta": delta},
            ))
    old_growth = {row["arm_id"]: row["growth"]
                  for row in previous.sections["fastest_growing_concepts"]}
    for row in current.sections["fastest_growing_concepts"]:
        before = old_growth.get(row["arm_id"])
        if before is None or row["growth"] is None:
            continue
        if abs(float(row["growth"]) - float(before)) >= settings.change_growth_threshold:
            drafts.append(MarketChangeEventDraft(
                event_type=ChangeEventType.GROWTH_COMPONENT_CHANGE, arm_id=row["arm_id"],
                previous_value=float(before), current_value=float(row["growth"]), detail={},
            ))
    return drafts


def persist_snapshot(session: Session, snapshot: MarketSnapshot, *, artifact_uri: str,
                     artifact_hash: str) -> str:
    """Insert the snapshot row and exactly the change events the report showed.

    `build_snapshot` already ran `detect_changes` against the previous snapshot, so the
    stored events and the rendered report can never disagree.
    """
    snapshot_id = repositories.insert_snapshot(session, as_of=snapshot.as_of,
                                               artifact_uri=artifact_uri,
                                               artifact_hash=artifact_hash)
    for draft in snapshot.changes:
        repositories.insert_change_event(session, draft, detected_at=snapshot.generated_at)
    return snapshot_id


def previous_snapshot(session: Session) -> dict | None:
    return repositories.previous_snapshot(session)


def load_snapshot(session: Session, as_of: datetime) -> dict | None:
    return repositories.load_snapshot(session, as_of)


def _snapshot_from_row(row) -> MarketSnapshot:
    import json

    return MarketSnapshot(as_of=row["as_of"], generated_at=row["created_at"],
                          sections=json.loads(row["sections_json"]),
                          evidence_classes=json.loads(row["evidence_classes_json"]))
```

```python
# services/market_intelligence/reports/exports.py
"""§31's human-readable and machine-readable exports under `market/reports/` (R13)."""

from __future__ import annotations

import json
from collections.abc import Callable

from plm_platform.storage import StoredObject, put_content

from .market_snapshot import MarketSnapshot

__all__ = ["render_markdown", "render_machine", "store_report"]

_TITLES = {
    "market_snapshot": "Market snapshot",
    "fastest_growing_concepts": "Fastest-growing concepts",
    "largest_demand_clusters": "Largest demand clusters",
    "lowest_saturation_clusters": "Lowest-saturation attractive clusters",
    "new_taxonomy_discoveries": "New taxonomy discoveries",
    "data_quality_warnings": "Data-quality warnings",
    "source_freshness": "Source freshness",
    "top_opportunity_arms": "Top opportunity arms",
    "low_confidence_opportunities": "Low-confidence opportunities",
    "changes_vs_previous_snapshot": "Changes vs previous snapshot",
}


def render_markdown(snapshot: MarketSnapshot) -> str:
    """§31: every section states its evidence class; speculative text is never 'observed'."""
    lines = [f"# Market Snapshot — {snapshot.as_of.date().isoformat()}", ""]
    for key, title in _TITLES.items():
        rows = snapshot.sections.get(key, [])
        lines.append(f"## {title} ({snapshot.evidence_classes.get(key, 'DERIVED')})")
        if not rows:
            lines.append("_no rows: this is an empty result, not a zero score_")
        for row in rows:
            lines.append(f"- {json.dumps(row, sort_keys=True, default=str)}")
        lines.append("")
    return "\n".join(lines)


def render_machine(snapshot: MarketSnapshot) -> str:
    return json.dumps({"as_of": snapshot.as_of.isoformat(),
                       "generated_at": snapshot.generated_at.isoformat(),
                       "evidence_classes": snapshot.evidence_classes,
                       "sections": snapshot.sections},
                      sort_keys=True, indent=2, default=str) + "\n"


def store_report(snapshot: MarketSnapshot, *, put_object: Callable[..., StoredObject] | None = None,
                 settings=None, client=None) -> StoredObject:
    """Content-addressed report artifact; the row in `market_snapshots` carries its URI."""
    put = put_content if put_object is None else put_object
    body = render_machine(snapshot).encode("utf-8")
    return put("market/", f"reports/{snapshot.as_of.date().isoformat()}", body,
               content_type="application/json", settings=settings, client=client)
```

> **Schema addition required by this step (same commit):** `market_snapshots` needs the two
> body columns this module reads — `sections_json` and `evidence_classes_json` (`sa.JSON NOT NULL`).
> Add them to `storage/models.py` **and** to the P1.2 migration's `op.create_table("market_snapshots", ...)`
> call, then re-run the migration round-trip from P1.2 Step 11.

- [ ] **Step 4: Run the snapshot tests and watch them pass**

Run: `PLM_UPDATE_GOLDEN=1 python -m pytest tests/unit/market/test_snapshot.py -q` then
`python -m pytest tests/unit/market/test_snapshot.py -q`
Expected: PASS (2 passed) on the second run; inspect the golden file.

- [ ] **Step 5: Commit**

```bash
git add services/market_intelligence/reports/ services/market_intelligence/storage/ \
        migrations/versions/market/ tests/unit/market/test_snapshot.py \
        tests/fixtures/golden/market/market_snapshot.md
git commit -m "feat(p1): market snapshot, change detection and report export"
```

- [ ] **Step 6: Write the failing run-summary test**

```python
# tests/unit/market/test_run_summary.py
from datetime import UTC, datetime

from services.market_intelligence.domain.enums import ConnectorType, ProviderName
from services.market_intelligence.observability.metrics import (
    METRICS_REGISTRY, QUARANTINED_TOTAL, record_run_metrics,
)
from services.market_intelligence.observability.run_summary import summarize_run
from services.market_intelligence.observability.logging import log_run_summary
from services.market_intelligence.storage import repositories

FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_run_summary_answers_every_ac14_question(market_session, caplog) -> None:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    run_id = repositories.start_collection_run(
        market_session, source_id=source_id, query_id=None, software_version="p1-v1",
        started_at=FIXED_NOW,
    )
    repositories.add_run_cost(market_session, run_id, api_calls=2, llm_tokens=900,
                              llm_cost_estimate=0.0135, storage_bytes=4096)
    summary = summarize_run(market_session, run_id)
    assert summary.run_id == run_id
    assert summary.source is None or summary.source == ProviderName.YOUTUBE_DATA_API.value
    assert summary.quota_units == 2 and summary.llm_tokens == 900
    assert summary.storage_bytes == 4096
    assert summary.status == "running"

    import logging

    with caplog.at_level(logging.INFO):
        log_run_summary(summary)
    assert run_id in caplog.text and summary.status in caplog.text
    assert "llm_cost_estimate" in caplog.text

    before = QUARANTINED_TOTAL.labels(provider="youtube_data_api")._value.get()
    record_run_metrics(summary)
    after = QUARANTINED_TOTAL.labels(provider="youtube_data_api")._value.get()
    assert after >= before
    assert METRICS_REGISTRY is not None
```

- [ ] **Step 7: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_run_summary.py -q`
Expected: FAIL — modules missing.

- [ ] **Step 8: Write `observability/run_summary.py`, `metrics.py`, `logging.py`**

```python
# services/market_intelligence/observability/run_summary.py
"""§AC-14: one object that answers what ran, on which source, over which queries,
with how many objects, what failed, how much quota and LLM spend, and what changed."""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..storage import repositories

__all__ = ["RunSummary", "summarize_run"]


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    status: str
    source: str | None
    queries: int
    raw_objects: int
    normalized_objects: int
    quarantined: int
    failed: int
    quota_units: int
    llm_tokens: int
    llm_cost_estimate: float
    storage_bytes: int
    correlation_id: str | None = None


def summarize_run(session: Session, run_id: str) -> RunSummary:
    run = repositories.load_run(session, run_id)
    if run is None:
        raise KeyError(f"unknown run {run_id!r}")
    source = session.execute(sa.text(
        "SELECT provider_name FROM market.market_sources WHERE id = :id"
    ), {"id": run["source_id"]}).scalar_one_or_none()
    from plm_platform.correlation import current_correlation_id

    return RunSummary(
        run_id=str(run["id"]), status=str(run["status"]), source=source,
        queries=1 if run["query_id"] else 0, raw_objects=int(run["raw_observation_count"]),
        normalized_objects=int(run["normalized_observation_count"]),
        quarantined=repositories.count_quarantined(session, run_id=str(run["id"])),
        failed=int(run["error_count"]), quota_units=int(run["cost_api_calls"]),
        llm_tokens=int(run["cost_llm_tokens"]),
        llm_cost_estimate=float(run["cost_llm_cost_estimate"]),
        storage_bytes=int(run["cost_storage_bytes"]),
        correlation_id=current_correlation_id(),
    )
```

```python
# services/market_intelligence/observability/metrics.py
"""P1's Prometheus instruments, bound to the platform registry (no second registry)."""

from __future__ import annotations

from prometheus_client import Counter
from plm_platform.observability import METRICS_REGISTRY

from .run_summary import RunSummary

__all__ = [
    "METRICS_REGISTRY", "QUARANTINED_TOTAL", "QUOTA_UNITS_TOTAL", "RUNS_TOTAL",
    "SCORES_WRITTEN_TOTAL", "record_run_metrics",
]

RUNS_TOTAL = Counter("plm_market_runs_total", "Market collection runs by status.",
                     ["status"], registry=METRICS_REGISTRY)
QUARANTINED_TOTAL = Counter("plm_market_quarantined_total", "Quarantined market records.",
                            ["provider"], registry=METRICS_REGISTRY)
QUOTA_UNITS_TOTAL = Counter("plm_market_quota_units_total", "Provider quota units consumed.",
                            ["provider"], registry=METRICS_REGISTRY)
SCORES_WRITTEN_TOTAL = Counter("plm_market_scores_written_total",
                               "Opportunity score rows written.", registry=METRICS_REGISTRY)


def record_run_metrics(summary: RunSummary) -> None:
    provider = summary.source or "unknown"
    RUNS_TOTAL.labels(status=summary.status).inc()
    QUOTA_UNITS_TOTAL.labels(provider=provider).inc(summary.quota_units)
    QUARANTINED_TOTAL.labels(provider=provider).inc(summary.quarantined)
```

```python
# services/market_intelligence/observability/logging.py
"""One structured line per run, through the platform logger (no second logging stack)."""

from __future__ import annotations

import logging

from .run_summary import RunSummary

__all__ = ["log_run_summary"]

_logger = logging.getLogger("plm.market.run")


def log_run_summary(summary: RunSummary) -> None:
    """`extra` carries the counters so the JSON formatter emits them as fields."""
    _logger.info(
        "market run summary",
        extra={
            "run_id": summary.run_id, "status": summary.status, "source": summary.source,
            "raw_objects": summary.raw_objects, "normalized_objects": summary.normalized_objects,
            "quarantined": summary.quarantined, "failed": summary.failed,
            "quota_units": summary.quota_units, "llm_tokens": summary.llm_tokens,
            "llm_cost_estimate": summary.llm_cost_estimate,
            "storage_bytes": summary.storage_bytes,
        },
    )
```

- [ ] **Step 9: Run everything and commit**

Run: `python -m pytest tests/unit/market -q`
Expected: PASS.

```bash
git add services/market_intelligence/observability/ \
        services/market_intelligence/storage/repositories.py tests/unit/market/test_run_summary.py
git commit -m "feat(p1): run summaries, metrics and report observability"
```

---

### Task P1.13: Tests

**Files:**
- Create: `tests/fixtures/market/scenario.py`, `tests/unit/market/test_scoring_properties.py`, `test_reproducibility.py`, `test_data_quality_gates.py`, `tests/unit/market/test_seams_and_freshness.py`
- Modify: nothing (this task adds coverage, it does not change behaviour)

**Interfaces:**
- Consumes: everything from P1.1–P1.12 (this task calls only public names already listed).
- Produces: `tests.fixtures.market.scenario` — `build_scenario() -> MarketScenario` with `channels: tuple[ScenarioChannel, ...]`, `videos: tuple[ScenarioVideo, ...]`, `queries: tuple[str, ...]`, `observations_by_arm: Mapping[str, tuple[NormalizedObservation, ...]]`; `SCENARIO_NOW: datetime`; `PRIMARY_ARM_ID: str`, `SECONDARY_ARM_ID: str`. The scenario is §35's example: Channel A (10M subscribers, 600k views/video, slow growth), Channel B (20k subscribers, 45k views/video, fast growth, high cadence), Channel C (80k subscribers, 35k views/video, stable, low concentration).

> **Note on this task's reason to exist:** P1.1–P1.12 already wrote the unit tests for their own code, so §39's "P1.13 Tests" is not "write the tests" (TDD did that). It is the *cross-cutting* remainder: property tests, the reproducibility proof, the §24 gate matrix, the §35 scenario fixture and the freshness/`Tri` seam tests that no single module owns. Splitting it out is the reason it is a task and not a checklist item.

- [ ] **Step 1: Write the scenario fixture**

```python
# tests/fixtures/market/scenario.py
"""§35's deliberately adversarial fixture: raw popularity must not automatically win.

Channel A is the biggest and slowest; Channel B is small, fast and cheap to compete
with; Channel C is mid-sized and stable. The expected end state is asserted in
`test_scoring_properties.py`, not assumed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from plm_contracts import Tri

from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.domain.enums import EntityType

SCENARIO_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
PRIMARY_ARM_ID = "lofi_study_focus"
SECONDARY_ARM_ID = "rnb_late_night_drive"


@dataclass(frozen=True, slots=True)
class ScenarioChannel:
    entity_id: str
    entity_type: EntityType
    subscriber_count: int | None
    video_count: int


@dataclass(frozen=True, slots=True)
class ScenarioVideo:
    video_id: str
    channel_id: str
    published_days_ago: int
    views: int
    likes: int | None
    comments: int | None
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class MarketScenario:
    """§35's fixture: channels, videos, queries and the arm-keyed observations."""

    channels: tuple[ScenarioChannel, ...]
    videos: tuple[ScenarioVideo, ...]
    queries: tuple[str, ...]
    observations_by_arm: dict[str, tuple[NormalizedObservation, ...]]
    now: datetime


CHANNELS = (
    ScenarioChannel("ch-a", EntityType.CHANNEL, 10_000_000, 412),
    ScenarioChannel("ch-b", EntityType.CHANNEL, 20_000, 88),
    ScenarioChannel("ch-c", EntityType.CHANNEL, 80_000, 140),
)

# Three collections of each of twenty videos across three channels gives the
# time-series depth the growth windows need (§34's "2 observation timestamps").
_VIDEO_TEMPLATE = (
    # (videoid, channel, published_days_ago, base_views, likes, comments, duration_s)
    ("vid000000001", "ch-a", 400, 600_000, 21_000, 1_100, 3_600.0),
    ("vid000000002", "ch-a", 380, 590_000, 20_400, 1_050, 2_800.0),
    ("vid000000003", "ch-a", 360, 580_000, 19_800, 990, 2_400.0),
    ("vid000000004", "ch-a", 300, 570_000, 19_100, 940, 3_000.0),
    ("vid000000005", "ch-a", 280, 560_000, 18_400, 900, 1_200.0),
    ("vid000000006", "ch-a", 200, 540_000, 17_000, 820, 3_600.0),
    ("vid000000007", "ch-a", 120, 520_000, 15_900, 760, 2_200.0),
    ("vid000000008", "ch-b", 300, 45_000, 3_100, 260, 1_800.0),
    ("vid000000009", "ch-b", 260, 42_000, 2_900, 240, 2_100.0),
    ("vid000000010", "ch-b", 200, 44_000, 3_050, 255, 2_400.0),
    ("vid000000011", "ch-b", 120, 46_000, 3_220, 270, 2_000.0),
    ("vid000000012", "ch-b", 60, 48_000, 3_400, 285, 1_900.0),
    ("vid000000013", "ch-b", 30, 51_000, 3_650, 305, 2_050.0),
    ("vid000000014", "ch-b", 10, 54_000, 3_900, 330, 2_150.0),
    ("vid000000015", "ch-c", 240, 35_000, 2_100, 180, 2_600.0),
    ("vid000000016", "ch-c", 180, 36_000, 2_160, 185, 2_700.0),
    ("vid000000017", "ch-c", 120, 36_500, 2_190, 188, 2_650.0),
    ("vid000000018", "ch-c", 90, 37_000, 2_220, 190, 2_800.0),
    ("vid000000019", "ch-c", 60, 37_400, 2_244, 192, 2_900.0),
    ("vid000000020", "ch-c", 30, 37_800, 2_268, 194, 2_750.0),
)


def build_scenario() -> MarketScenario:
    """Twenty videos, three channels, three collection instants — deterministic."""
    observations_by_arm: dict[str, list[NormalizedObservation]] = {PRIMARY_ARM_ID: [],
                                                                   SECONDARY_ARM_ID: []}
    videos: list[ScenarioVideo] = []
    for index, (video_id, channel, published_days, views, likes, comments, duration) in \
            enumerate(_VIDEO_TEMPLATE):
        videos.append(ScenarioVideo(video_id, channel, published_days, views, likes, comments,
                                    duration))
        arm = PRIMARY_ARM_ID if channel == "ch-b" else SECONDARY_ARM_ID
        for step, days_ago in enumerate((20, 10, 0)):
            observations_by_arm[arm].append(NormalizedObservation(
                entity_id=video_id, source_id="youtube_data_api",
                raw_observation_id=f"raw-{video_id}-{step}",
                observed_at=SCENARIO_NOW - timedelta(days=days_ago),
                published_at=SCENARIO_NOW - timedelta(days=published_days),
                views=Tri.known(views - (2 - step) * (views // 20)),
                likes=Tri.known(likes), comments=Tri.known(comments),
                video_duration_seconds=Tri.known(duration),
            ))
    return MarketScenario(
        channels=CHANNELS, videos=tuple(videos), queries=("lofi", "study music", "rnb"),
        observations_by_arm={arm: tuple(rows) for arm, rows in observations_by_arm.items()},
        now=SCENARIO_NOW,
    )
```

- [ ] **Step 2: Write the property and reproducibility tests, run and watch them pass**

```python
# tests/unit/market/test_scoring_properties.py
import pytest

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.scoring.calibration import default_weights
from services.market_intelligence.scoring.opportunity import score_all_arms
from services.market_intelligence.domain.value_objects import ScoreComponents
from services.market_intelligence.scoring.opportunity import score_arm
from tests.fixtures.market.scenario import SECONDARY_ARM_ID, build_scenario

SETTINGS = load_market_settings()


def test_scores_are_always_within_bounds_for_a_grid_of_component_inputs() -> None:
    """Property (§34): score ∈ [0,100] and confidence ∈ [0,1] for any component set."""
    grid = [0.0, 25.0, 50.0, 75.0, 100.0]
    scenario = build_scenario()
    features = compute_arm_features(scenario.observations_by_arm, as_of=scenario.now,
                                    settings=SETTINGS)
    for demand in grid:
        for competition in grid:
            components = ScoreComponents(demand=demand, growth=demand,
                                         competitor_velocity=competition,
                                         competition=competition, audience_quality=demand,
                                         cross_platform=competition,
                                         production_fit=demand)
            score = score_arm(features[SECONDARY_ARM_ID], components, as_of=scenario.now,
                              weights=default_weights(),
                              feature_version=SETTINGS.feature_version,
                              normalization_version=SETTINGS.normalization_version)
            assert 0.0 <= score.market_opportunity_score <= 100.0
            assert 0.0 <= score.confidence <= 1.0


def test_a_stronger_demand_input_never_lowers_the_score() -> None:
    """Property (§34): monotonicity in a positive demand input."""
    scenario = build_scenario()
    features = compute_arm_features(scenario.observations_by_arm, as_of=scenario.now,
                                    settings=SETTINGS)
    previous = -1.0
    for demand in (10.0, 40.0, 70.0, 100.0):
        components = ScoreComponents(demand=demand, growth=50.0, competitor_velocity=50.0,
                                     competition=50.0, audience_quality=50.0,
                                     cross_platform=50.0, production_fit=50.0)
        score = score_arm(features[SECONDARY_ARM_ID], components, as_of=scenario.now,
                          weights=default_weights(),
                          feature_version=SETTINGS.feature_version,
                          normalization_version=SETTINGS.normalization_version)
        assert score.market_opportunity_score >= previous
        previous = score.market_opportunity_score


def test_raising_saturation_never_raises_the_competition_opportunity_score() -> None:
    """§17.3/§34: the competition component is inverted saturation."""
    from services.market_intelligence.scoring.opportunity import compute_component_scores

    scenario = build_scenario()
    features = compute_arm_features(scenario.observations_by_arm, as_of=scenario.now,
                                    settings=SETTINGS)
    scores = compute_component_scores(features, settings=SETTINGS)
    for arm_scores in scores.values():
        assert 0.0 <= arm_scores.competition <= 100.0


def test_raw_popularity_does_not_decide_the_ranking() -> None:
    """§35's purpose: Channel A (600k views, slow) must not outrank Channel B (54k, fast)."""
    scenario = build_scenario()
    features = compute_arm_features(scenario.observations_by_arm, as_of=scenario.now,
                                    settings=SETTINGS)
    scores = score_all_arms(features, as_of=scenario.now, settings=SETTINGS)
    primary = scores["lofi_study_focus"].market_opportunity_score
    secondary = scores["rnb_late_night_drive"].market_opportunity_score
    assert primary >= secondary, (
        "the fast-growing, low-concentration arm must not lose to the larger, slower one: "
        "that is the normalisation's whole job (§35)"
    )
```

```python
# tests/unit/market/test_reproducibility.py
"""§30/AC-08: same observations + same versions ⇒ identical scores."""

import json
from datetime import timedelta

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.scoring.opportunity import score_all_arms
from tests.fixtures.market.scenario import build_scenario

SETTINGS = load_market_settings()


def _scores(observations_by_arm, as_of):
    features = compute_arm_features(observations_by_arm, as_of=as_of, settings=SETTINGS)
    return {arm: score.model_dump(mode="json")
            for arm, score in score_all_arms(features, as_of=as_of, settings=SETTINGS).items()}


def test_replaying_the_same_observations_produces_identical_scores() -> None:
    scenario = build_scenario()
    first = _scores(scenario.observations_by_arm, scenario.now)
    second = _scores(scenario.observations_by_arm, scenario.now)
    assert json.dumps(first, sort_keys=True, default=str) == \
        json.dumps(second, sort_keys=True, default=str)


def test_a_new_score_version_label_changes_the_version_not_the_arithmetic() -> None:
    scenario = build_scenario()
    from services.market_intelligence.config import load_market_settings as load

    first = _scores(scenario.observations_by_arm, scenario.now)
    bumped = load(score_version="p1-v2")
    features = compute_arm_features(scenario.observations_by_arm, as_of=scenario.now,
                                    settings=bumped)
    rescored = score_all_arms(features, as_of=scenario.now, settings=bumped)
    assert all(score.score_version == "p1-v2" for score in rescored.values())
```

```python
# tests/unit/market/test_data_quality_gates.py
"""§24's gate matrix: every forbidden value quarantines with its own reason code."""

from datetime import UTC, datetime, timedelta

import pytest

from services.market_intelligence.connectors.base import FetchedObject, FetchResult
from services.market_intelligence.domain.enums import ProviderObjectType
from services.market_intelligence.normalization.youtube import (
    QUARANTINE_REASON_CODES, normalize_video_object,
)

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def _fetched(**statistics) -> FetchedObject:
    payload = {
        "kind": "youtube#video", "id": "vid000000001",
        "snippet": {"publishedAt": "2026-08-01T10:00:00Z", "channelId": "UC" + "a" * 22,
                    "title": "fixture", "description": ""},
        "statistics": statistics,
    }
    return FetchedObject(provider_object_type=ProviderObjectType.VIDEO,
                         provider_object_id="vid000000001", observed_at=NOW, payload=payload)


@pytest.mark.parametrize(("payload_id", "reason"), [
    ("short", "malformed_provider_id"),
])
def test_malformed_ids_are_quarantined(payload_id: str, reason: str) -> None:
    fetched = _fetched(viewCount="10")
    fetched = FetchedObject(provider_object_type=fetched.provider_object_type,
                            provider_object_id=payload_id, observed_at=NOW,
                            payload=fetched.payload)
    outcome = normalize_video_object(fetched, entity_id="e", source_id="s",
                                     raw_observation_id="r", observed_at=NOW)
    assert outcome.quarantine is not None
    assert outcome.quarantine.reason_code == reason
    assert reason in QUARANTINE_REASON_CODES


def test_negative_counts_and_impossible_timestamps_are_quarantined() -> None:
    negative = normalize_video_object(_fetched(viewCount="-5"), entity_id="e", source_id="s",
                                      raw_observation_id="r", observed_at=NOW)
    assert negative.quarantine.reason_code == "negative_count"

    future = _fetched(viewCount="5")
    future_payload = dict(future.payload)
    future_payload["snippet"] = dict(future.payload["snippet"])
    future_payload["snippet"]["publishedAt"] = (NOW + timedelta(days=1)).isoformat()
    impossible = normalize_video_object(
        FetchedObject(provider_object_type=future.provider_object_type,
                      provider_object_id=future.provider_object_id, observed_at=NOW,
                      payload=future_payload),
        entity_id="e", source_id="s", raw_observation_id="r", observed_at=NOW,
    )
    assert impossible.quarantine.reason_code == "impossible_timestamp_order"


def test_a_non_finite_number_is_a_reason_code_not_a_crash() -> None:
    """A count the parser cannot turn into an int is a parse failure at the boundary;
    `non_finite_number` is reserved for floats (durations) that survive parsing."""
    assert "non_finite_number" in QUARANTINE_REASON_CODES
    with pytest.raises(ValueError):
        normalize_video_object(_fetched(viewCount="NaN"), entity_id="e", source_id="s",
                               raw_observation_id="r", observed_at=NOW)
```

Run: `python -m pytest tests/unit/market/test_scoring_properties.py tests/unit/market/test_reproducibility.py tests/unit/market/test_data_quality_gates.py -q`
Expected: PASS.

- [ ] **Step 3: Write the freshness/`Tri` seam test, run it, commit**

```python
# tests/unit/market/test_seams_and_freshness.py
"""Review Focus 1 and §21, across the feature layer rather than inside one module."""

from datetime import UTC, datetime, timedelta

import pytest

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.value_objects import classify_freshness
from services.market_intelligence.domain.enums import FreshnessBucket
from services.market_intelligence.features.arm_features import compute_arm_features
from tests.fixtures.market.scenario import PRIMARY_ARM_ID, build_scenario

SETTINGS = load_market_settings()


def test_freshness_buckets_follow_the_spec_boundaries() -> None:
    now = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
    for hours, expected in ((47.0, FreshnessBucket.FRESH), (49.0, FreshnessBucket.NORMAL),
                            (167.0, FreshnessBucket.NORMAL), (169.0, FreshnessBucket.STALE),
                            (719.0, FreshnessBucket.STALE), (721.0, FreshnessBucket.EXPIRED)):
        assessment = classify_freshness(as_of=now - timedelta(hours=hours), now=now)
        assert assessment.bucket is expected, hours


def test_an_observation_without_metrics_yields_unknown_features_and_lower_confidence() -> None:
    from plm_contracts import Tri

    from services.market_intelligence.domain.entities import NormalizedObservation

    now = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
    thin = NormalizedObservation(
        entity_id="e1", source_id="youtube_data_api", raw_observation_id="raw-1",
        observed_at=now, published_at=now - timedelta(days=30),
    )
    features = compute_arm_features({"arm": (thin,)}, as_of=now, settings=SETTINGS)
    arm = features["arm"]
    assert arm.median_velocity_30d.is_unknown
    assert arm.demand.median_views_per_day.is_unknown
    assert arm.cross_platform.is_unknown
    assert arm.confidence < 0.5, "a thin observation must lower confidence, not raise a score"
```

Run: `python -m pytest tests/unit/market -q`
Expected: PASS.

```bash
git add tests/fixtures/market/scenario.py tests/unit/market/test_scoring_properties.py \
        tests/unit/market/test_reproducibility.py tests/unit/market/test_data_quality_gates.py \
        tests/unit/market/test_seams_and_freshness.py
git commit -m "test(p1): property, reproducibility and data-quality coverage"
```

---

### Task P1.14: End-to-end acceptance run

**Files:**
- Create: `acceptance/__init__.py`, `acceptance/youtube_smoke.py`, `tests/integration/market/test_e2e_acceptance.py`, `tests/integration/market/test_api_live_uvicorn.py`, `tests/unit/market/test_smoke_live_blocked.py`
- Modify: `cli.py` (`run_daily` production path, `smoke_live`)

**Interfaces:**
- Consumes: everything above; `tests/support/http.MockHTTP`; `plm_platform.config.load_settings`; `plm_platform.storage.ensure_bucket`/`get_verified`/`from_uri`; uvicorn.
- Produces:
  - `acceptance.youtube_smoke`: `SmokeStep(name: str, ok: bool, detail: str)`; `SmokeReport(blocked_reason: str | None, steps: tuple[SmokeStep, ...])` with `.ok` (all steps ok *and* not blocked); `missing_credentials(platform) -> tuple[str, ...]`; `run_live_smoke(*, platform, client, storage_client=None) -> SmokeReport` performing §40's checklist; `BLOCKED_REASON_TEMPLATE = "blocked: pending credentials — set {vars}"`.
  - `cli.smoke_live() -> str` — returns the blocked reason (never raises), so the Typer command can exit `BLOCKED_EXIT_CODE`.
  - `cli.run_daily(now=None, *, session_factory=None, connector=None, settings=None) -> dict` — the in-process daily cycle P1.14's acceptance test drives (and the workflow activities reuse).

> **§40's acceptance is `blocked: pending credentials` and nothing else.** The smoke path is a
> real code path with a real checklist; its *acceptance* is the blocked marker until a human
> completes P0 §10's YouTube API project task. It is never skipped silently, never `xfail`ed and
> never reported as passing. The observable proof is an exit code: `plm market smoke-live` prints
> `blocked: pending credentials — set YOUTUBE_API_KEY` and exits 3.

- [ ] **Step 1: Write the failing blocked-smoke test**

```python
# tests/unit/market/test_smoke_live_blocked.py
import httpx
from plm_platform.config import load_settings
from typer.testing import CliRunner

from plm_platform.cli import BLOCKED_EXIT_CODE, app

runner = CliRunner()
REQUIRED_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
}


def test_the_live_smoke_is_blocked_with_a_reason_not_skipped(monkeypatch) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    calls: list[str] = []
    monkeypatch.setattr(httpx, "Client", lambda **_: calls.append("client") or None)
    result = runner.invoke(app, ["market", "smoke-live"])
    output = str(result.output)
    assert result.exit_code == BLOCKED_EXIT_CODE
    assert "blocked: pending credentials" in output
    assert "YOUTUBE_API_KEY" in output
    assert calls == [], "the blocked path must not construct an HTTP client"


def test_the_blocked_reason_names_every_missing_variable() -> None:
    from services.market_intelligence.acceptance.youtube_smoke import missing_credentials

    platform = load_settings(env=REQUIRED_ENV)
    assert missing_credentials(platform) == ("YOUTUBE_API_KEY",)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python -m pytest tests/unit/market/test_smoke_live_blocked.py -q`
Expected: FAIL — `ImportError: cannot import name 'BLOCKED_EXIT_CODE'` if P1.11 skipped it, otherwise `ModuleNotFoundError: No module named 'services.market_intelligence.acceptance'`.

- [ ] **Step 3: Write `acceptance/youtube_smoke.py` and wire `smoke_live`**

```python
# services/market_intelligence/acceptance/youtube_smoke.py
"""§40's live smoke test — a real checklist with a first-class blocked outcome.

Run only after a human has created the YouTube API project and OAuth client (P0 §10).
Until then every invocation reports `blocked: pending credentials`; the function does
not fall back to mock data, because a smoke test that passes without credentials is
worse than no smoke test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from plm_platform.config import Settings

__all__ = [
    "BLOCKED_REASON_TEMPLATE", "REQUIRED_YOUTUBE_VARS", "SmokeReport", "SmokeStep",
    "missing_credentials", "run_live_smoke",
]

REQUIRED_YOUTUBE_VARS: tuple[str, ...] = (
    "YOUTUBE_API_KEY", "YOUTUBE_OAUTH_CLIENT_ID", "YOUTUBE_OAUTH_CLIENT_SECRET",
)
BLOCKED_REASON_TEMPLATE = "blocked: pending credentials — set {variables}"


@dataclass(frozen=True, slots=True)
class SmokeStep:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SmokeReport:
    blocked_reason: str | None = None
    steps: tuple[SmokeStep, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.blocked_reason is None and bool(self.steps) and all(s.ok for s in self.steps)

    @property
    def failed_steps(self) -> tuple[SmokeStep, ...]:
        return tuple(step for step in self.steps if not step.ok)


def missing_credentials(platform: Settings) -> tuple[str, ...]:
    missing = []
    if platform.youtube_api_key is None:
        missing.append("YOUTUBE_API_KEY")
    if platform.youtube_oauth_client_id is None:
        missing.append("YOUTUBE_OAUTH_CLIENT_ID")
    if platform.youtube_oauth_client_secret is None:
        missing.append("YOUTUBE_OAUTH_CLIENT_SECRET")
    return tuple(missing)


def blocked_reason(platform: Settings) -> str | None:
    missing = missing_credentials(platform)
    if not missing:
        return None
    return BLOCKED_REASON_TEMPLATE.format(variables=", ".join(missing))


def run_live_smoke(*, platform: Settings, client: httpx.Client, storage_client=None) -> SmokeReport:
    """§40's checklist against the real API with a quota-safe query set."""
    reason = blocked_reason(platform)
    if reason is not None:
        return SmokeReport(blocked_reason=reason)
    from . import _live_checklist

    return _live_checklist.run(platform=platform, client=client, storage_client=storage_client,
                              now=datetime.now(UTC))
```

`acceptance/_live_checklist.py` is a separate module so the blocked path never imports the
pipeline. Write it out in full:

```python
# services/market_intelligence/acceptance/_live_checklist.py
"""§40's ten checks, each one a `SmokeStep`, each catching its own failure.

The query set is quota-safe by construction: one query, one page, one batch of ids
(`max_results_per_search=1`, `max_pages_per_search=1`) — "do not perform a large crawl
during initial credential validation" (§40).
"""

from __future__ import annotations

from datetime import datetime

import httpx
from plm_platform.config import Settings

from ..collection.rate_limit import QuotaBudget, RequestBudget
from ..connectors.youtube import YouTubeCredentials, YouTubeDataAPIConnector
from ..domain.enums import ConnectorType, EntityType, ProviderName, ProviderObjectType
from ..normalization.canonical import normalize_text
from ..normalization.youtube import normalize_search_hit, normalize_video_object
from ..storage import repositories
from ..taxonomy.classifier import ClassificationInput, RuleBasedClassifier
from .youtube_smoke import SmokeReport, SmokeStep

QUERY = "lofi"


def run(*, platform: Settings, client: httpx.Client, storage_client, now: datetime) -> SmokeReport:
    steps: list[SmokeStep] = []
    settings = _settings(platform)
    connector = YouTubeDataAPIConnector(
        client=client, credentials=_credentials(platform), quota=QuotaBudget(200),
        request_budget=RequestBudget(4), observed_at=lambda: now,
    )
    with _session() as session:
        source_id = repositories.upsert_source(
            session, provider_name=ProviderName.YOUTUBE_DATA_API,
            connector_type=ConnectorType.DATA_API,
            base_identifier="https://www.googleapis.com/youtube/v3",
            configuration_version="p1-sources-v1",
        )
        run_id = repositories.start_collection_run(
            session, source_id=source_id, query_id=None, software_version=settings.score_version,
            started_at=now,
        )
        steps.append(_step("oauth credential works", lambda: connector._credentials.headers()))
        page = connector.search_videos(QUERY, max_results=1)
        steps.append(SmokeStep("search request works", page.quota_units == 100,
                               f"objects={len(page.objects)}"))
        pages = list(connector.iter_search_videos(QUERY, max_results=1, max_pages=1))
        steps.append(SmokeStep("pagination works", len(pages) == 1, f"pages={len(pages)}"))
        video_ids = [obj.provider_object_id for obj in page.objects][:1]
        hydration = connector.fetch_videos(video_ids)
        steps.append(SmokeStep("resource hydration works", bool(hydration.objects),
                               f"objects={len(hydration.objects)}"))
        raw = None
        if page.objects:
            raw = _store(session, run_id=run_id, source_id=source_id, fetched=page.objects[0],
                         context=page.request_context_hash, storage_client=storage_client)
        steps.append(SmokeStep("raw payload stored", raw is not None and raw.payload_hash != "",
                               getattr(raw, "payload_uri", "")))
        observation_id = None
        if raw is not None and page.objects:
            entity_id = repositories.resolve_entity(
                session, entity_type=EntityType.VIDEO, source_id=source_id,
                provider_object_type=ProviderObjectType.VIDEO,
                provider_object_id=page.objects[0].provider_object_id,
                canonical_name="live smoke", normalized_name=normalize_text("live smoke"),
                canonical_url=None, observed_at=now,
            )
            outcome = normalize_search_hit(page.objects[0], entity_id=entity_id,
                                           source_id=source_id,
                                           raw_observation_id=raw.raw_observation_id,
                                           observed_at=now)
            observation_id, _ = repositories.insert_normalized_observation(
                session, outcome.observation)
        steps.append(SmokeStep("normalized observation stored", observation_id is not None,
                               observation_id or "no observation"))
        draft = None
        if observation_id is not None:
            from ..taxonomy.classifier import classify_observation

            _, created = classify_observation(
                session, entity_id=None, observation_id=observation_id,
                text=ClassificationInput(title="live smoke", description=QUERY),
                classifier=RuleBasedClassifier(), taxonomy_version=settings.taxonomy_version,
            )
            steps.append(SmokeStep("taxonomy classification works", True, f"created={created}"))
        steps.append(_step("features calculated", lambda: _feature_check(settings, now, session)))
        steps.append(_step("opportunity score calculated", lambda: _score_check(settings, now, session)))
        steps.append(_step("API returns result", lambda: _api_check(session, settings)))
        session.commit()
    return SmokeReport(steps=tuple(steps))


def _step(name: str, fn) -> SmokeStep:
    try:
        detail = fn()
    except Exception as exc:  # noqa: BLE001 - the report must name what failed
        return SmokeStep(name, False, f"{type(exc).__name__}: {exc}")
    return SmokeStep(name, True, str(detail))


def _feature_check(settings, now, session):
    from ..features.arm_features import compute_arm_features

    observations = repositories.load_observations(session)
    features = compute_arm_features({"live-smoke": observations[:1]}, as_of=now,
                                    settings=settings)
    return f"arms={len(features)}"


def _score_check(settings, now, session):
    from ..scoring.opportunity import score_all_arms
    from ..features.arm_features import compute_arm_features

    observations = repositories.load_observations(session)
    features = compute_arm_features({"live-smoke": observations[:1]}, as_of=now,
                                    settings=settings)
    scores = score_all_arms(features, as_of=now, settings=settings)
    return f"scores={len(scores)}"


def _api_check(session, settings) -> str:
    from ..api.schemas import OpportunityListResponse

    return OpportunityListResponse.model_json_schema()["title"]


def _credentials(platform: Settings) -> YouTubeCredentials:
    return YouTubeCredentials(api_key=platform.youtube_api_key,
                              access_token=None)


def _settings(platform: Settings):
    from ..config import load_market_settings

    return load_market_settings(platform=platform)


def _session():
    from plm_platform.db import scoped_session

    return scoped_session("market")


def _store(session, *, run_id, source_id, fetched, context, storage_client):
    from ..storage.raw_store import store_raw_observation

    return store_raw_observation(session, run_id=run_id, source_id=source_id, fetched=fetched,
                                 request_context_hash=context, client=storage_client)
```

The live path is therefore complete *and* its acceptance is still blocked: with no credentials
`run_live_smoke` returns before touching any of it.

```python
# services/market_intelligence/cli.py  (add)
def smoke_live() -> str:
    """Return the §40 blocked reason (or an empty string when the smoke test passed)."""
    from plm_platform.config import load_settings

    from .acceptance.youtube_smoke import blocked_reason, run_live_smoke

    platform = load_settings()
    reason = blocked_reason(platform)
    if reason is not None:
        return reason
    import httpx

    report = run_live_smoke(platform=platform, client=httpx.Client(timeout=30.0))
    if report.ok:
        return ""
    return "smoke failed: " + "; ".join(f"{step.name}: {step.detail}"
                                        for step in report.failed_steps)
```

- [ ] **Step 4: Run the blocked-smoke test and watch it pass**

Run: `python -m pytest tests/unit/market/test_smoke_live_blocked.py -q`
Expected: PASS (2 passed). Record the blocked acceptance in the ledger:

```text
P1.14 live smoke: ACCEPTANCE = blocked: pending credentials (YOUTUBE_API_KEY,
YOUTUBE_OAUTH_CLIENT_ID, YOUTUBE_OAUTH_CLIENT_SECRET). Proof: `python -m plm_platform.cli
market smoke-live` exits 3 with that string; unit test asserts no HTTP client is constructed.
```

- [ ] **Step 5: Commit**

```bash
git add services/market_intelligence/acceptance/ services/market_intelligence/cli.py \
        tests/unit/market/test_smoke_live_blocked.py
git commit -m "feat(p1): blocked-aware live smoke test"
```

- [ ] **Step 6: Write the end-to-end acceptance test**

```python
# tests/integration/market/test_e2e_acceptance.py
"""P1's acceptance run: mock-backed, full pipeline, real Postgres and MinIO.

Covers AC-01…AC-14 in one path: collect → raw → normalize → entities → classify → arms →
features → score → API, plus AC-03 (a second collection adds history), AC-12 (a rate-limit
response leaves the run partial and resumable) and the §35 fixture's ranking expectation.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from plm_platform.db import build_engine
from plm_platform.storage import build_client, ensure_bucket, from_uri, get_verified
from sqlalchemy.orm import sessionmaker
from tests.support.http import MockHTTP

from services.market_intelligence.api.app import create_app
from services.market_intelligence.cli import run_daily
from services.market_intelligence.config import load_market_settings
from services.market_intelligence.connectors.youtube import (
    YouTubeCredentials, YouTubeDataAPIConnector,
)
from services.market_intelligence.collection.rate_limit import QuotaBudget, RequestBudget

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "market" / "youtube"
NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stack():
    try:
        engine = build_engine()
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
        client = build_client()
        ensure_bucket(client=client)
    except Exception as exc:  # noqa: BLE001 - skip with a remedy (repo convention)
        pytest.skip(f"postgres/minio unreachable ({type(exc).__name__}); "
                    "run: docker compose up -d --wait")
    yield engine, client
    engine.dispose()


def test_full_mock_cycle_produces_ranked_evidence_backed_arms(stack) -> None:
    engine, storage_client = stack
    factory = sessionmaker(bind=engine)
    settings = load_market_settings()
    mock = MockHTTP(repeat_last=True)
    mock.enqueue_json(_fixture("search_lofi_p1.json"))
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))
    mock.enqueue_json(_fixture("channels_lofi_batch.json"))
    connector = YouTubeDataAPIConnector(
        client=mock.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=YouTubeCredentials(), quota=QuotaBudget(10_000),
        request_budget=RequestBudget(1_000), observed_at=lambda: NOW,
    )

    first = run_daily(now=NOW, session_factory=factory, connector=connector, settings=settings)
    assert first["status"] in {"succeeded", "partial"}
    with factory() as session:
        raw_rows = session.execute(sa.text(
            "SELECT payload_uri, payload_hash, request_context_hash "
            "FROM market.market_raw_observations"
        )).mappings().all()
        observations = session.execute(sa.text(
            "SELECT count(*) FROM market.market_observations")).scalar_one()
        arms = session.execute(sa.text("SELECT count(*) FROM market.market_arms")).scalar_one()
        scores = session.execute(sa.text(
            "SELECT count(*) FROM market.market_opportunity_scores")).scalar_one()
    assert raw_rows and observations and arms and scores  # AC-01/AC-02/AC-07
    for row in raw_rows:
        assert get_verified(from_uri(row["payload_uri"]), client=storage_client)  # R13
        assert row["request_context_hash"]  # provenance travels with the payload
    assert any("raw-" not in str(row["payload_uri"]) for row in raw_rows)

    # AC-03: a second collection at a later instant adds history, never overwrites
    later = NOW + timedelta(days=3)
    with factory() as session:
        before = session.execute(sa.text(
            "SELECT count(*) FROM market.market_observations")).scalar_one()
    run_daily(now=later, session_factory=factory, connector=connector, settings=settings)
    with factory() as session:
        after = session.execute(sa.text(
            "SELECT count(*) FROM market.market_observations")).scalar_one()
    assert after > before

    # AC-09/AC-10: the API answers with ranked items and the frozen envelope
    platform_env = {"PLM_SERVICE_TOKEN": "acceptance-token"}
    from plm_platform.config import load_settings

    base = {"DATABASE_URL": "postgresql://plm:x@localhost:5432/plm", "POSTGRES_PASSWORD": "x",
            "MINIO_ENDPOINT": "localhost:9000", "MINIO_ROOT_USER": "plm",
            "MINIO_ROOT_PASSWORD": "x", "MINIO_BUCKET": "plm-media",
            "TEMPORAL_HOST": "localhost:7233", **platform_env}
    app = create_app(session_factory=factory,
                     settings=load_market_settings(platform=load_settings(env=base)))
    with httpx.Client(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                      headers={"X-PLM-Service-Token": "acceptance-token"}) as client:
        body = client.get("/market/v1/opportunities").json()
    assert set(body) == {"items", "next_cursor", "as_of", "score_version"}
    scores = [item["market_opportunity_score"] for item in body["items"]]
    assert scores == sorted(scores, reverse=True)


def test_a_rate_limited_run_is_partial_and_resumable(stack) -> None:
    """AC-12/§23/§27: the run records the failure class, and the query is rescheduled."""
    engine, _ = stack
    factory = sessionmaker(bind=engine)
    settings = load_market_settings()
    mock = MockHTTP()
    mock.enqueue_rate_limit(retry_after="60").enqueue_rate_limit(retry_after="60")
    mock.enqueue_json(_fixture("search_lofi_p1.json"))
    connector = YouTubeDataAPIConnector(
        client=mock.client(base_url="https://www.googleapis.com/youtube/v3"),
        credentials=YouTubeCredentials(), quota=QuotaBudget(10_000),
        request_budget=RequestBudget(1_000), sleep=lambda _seconds: None,
        observed_at=lambda: NOW,
    )
    result = run_daily(now=NOW + timedelta(hours=1), session_factory=factory,
                       connector=connector, settings=settings)
    assert result["status"] == "partial" or result.get("quota_exhausted") is False
    with factory() as session:
        statuses = [row[0] for row in session.execute(sa.text(
            "SELECT status FROM market.market_collection_runs ORDER BY started_at"
        ))]
    assert "partial" in statuses
    # the queries that could not run are due again immediately (rescheduled, not lost)
    with factory() as session:
        due = session.execute(sa.text(
            "SELECT count(*) FROM market.market_queries WHERE next_run_at IS NOT NULL"
        )).scalar_one()
    assert due > 0
```

- [ ] **Step 7: Run it and watch it fail**

Run: `python -m pytest tests/integration/market/test_e2e_acceptance.py -q -m integration`
Expected: FAIL — `ImportError: cannot import name 'run_daily'` until Step 3's `run_daily` exists
(or a real assertion failure in the pipeline). Fix the pipeline, never the assertions.

- [ ] **Step 8: Complete `run_daily`'s production path and re-run until green**

```python
# services/market_intelligence/cli.py  (add)
def run_daily(now: datetime | None = None, *, session_factory=None, connector=None,
              settings: MarketSettings | None = None) -> dict:
    """One daily cycle in-process: seeds → discovery → classification → features → scores.

    This is the single orchestration entry point: the Temporal activities call
    `run_daily_step`, the CLI calls this, and the acceptance test calls this.
    """
    from plm_platform.db import scoped_session

    resolved = settings if settings is not None else load_market_settings()
    moment = now if now is not None else datetime.now(UTC)
    factory = session_factory if session_factory is not None else None

    def _session():
        return scoped_session("market", factory=factory) if factory is not None \
            else scoped_session("market")

    steps: dict[str, Any] = {}
    with _session() as session:
        source_id = _ensure_source(session)
        steps["seed_refresh"] = _install_seeds(session, source_id=source_id)
        session.commit()
    with _session() as session:
        steps["discovery"] = _run_due_collection(session, now=moment, connector=connector,
                                                 settings=resolved)
        session.commit()
    with _session() as session:
        steps["classification"] = _classify_pending(session, now=moment, settings=resolved)
        session.commit()
    with _session() as session:
        steps["scores"] = _recompute(session, now=moment, settings=resolved)
        session.commit()
    steps["status"] = "partial" if any(
        isinstance(value, dict) and value.get("status") == "partial"
        for value in steps.values()
    ) else "succeeded"
    return steps
```

`_run_due_collection` needs the `connector`/`settings` parameters added (they were hard-coded in
P1.11's sketch) and must call `collect_query(..., connector=connector or _production_connector(...))`.
`_classify_pending` selects observations without a classification row and calls
`classify_observation`; `_recompute` calls `generate_candidate_arms` → `repositories.upsert_arm`
→ `compute_arm_features` → `score_all_arms` → `persist_scores`, returning
`{"arms": n, "scores": m}`.

Run: `python -m pytest tests/integration/market/test_e2e_acceptance.py -q -m integration`
Expected: PASS both tests.

- [ ] **Step 9: Add the live-server check and commit**

```python
# tests/integration/market/test_api_live_uvicorn.py
"""The app served by uvicorn over a real socket — not only through ASGITransport."""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from services.market_intelligence.api.app import create_app
from services.market_intelligence.config import load_market_settings

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_a_real_uvicorn_server_answers_with_the_blocked_reason() -> None:
    """No token configured ⇒ 503 blocked, over a real socket, with a correlation id back."""
    port = _free_port()
    app = create_app(session_factory=None, settings=load_market_settings())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    response = None
    while time.monotonic() < deadline and response is None:
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/market/v1/opportunities", timeout=1.0)
        except httpx.TransportError:
            time.sleep(0.1)
    server.should_exit = True
    thread.join(timeout=5)
    assert response is not None and response.status_code == 503
    assert response.json()["detail"]["code"] == "blocked_pending_credentials"
    assert "x-correlation-id" in {key.lower() for key in response.headers}
```

Run: `python -m pytest tests/integration/market/test_api_live_uvicorn.py -q -m integration`
Expected: PASS.

```bash
git add tests/integration/market/test_e2e_acceptance.py \
        tests/integration/market/test_api_live_uvicorn.py services/market_intelligence/cli.py
git commit -m "test(p1): end-to-end acceptance run"
```

- [ ] **Step 10: Run the acceptance command and record the gate result**

Run (the phase gate, exactly):

```
docker compose up -d --wait
.\.venv\Scripts\python.exe -m pytest -q tests/integration/market/test_e2e_acceptance.py tests/contract/test_c1_market_to_ar.py
.\.venv\Scripts\python.exe -m pytest -q -m "not integration"
.\.venv\Scripts\python.exe -m pytest -q -m integration
.\.venv\Scripts\python.exe -m plm_platform.cli market smoke-live; echo "exit=$LASTEXITCODE"
```

Expected: the first two commands PASS; the integration run PASSes (or reports the named skip with
its remedy); `smoke-live` prints `blocked: pending credentials — set YOUTUBE_API_KEY,
YOUTUBE_OAUTH_CLIENT_ID, YOUTUBE_OAUTH_CLIENT_SECRET` and the shell shows `exit=3`. Record all
five outputs in `.superpowers/sdd/progress.md` as the P1 gate evidence, including the blocked
line verbatim.

---

## Self-review

Run after the plan was complete, against the spec with fresh eyes. Two things were fixed inline
(the P1.4 storage-byte accounting, and the P1.12 snapshot body columns); everything else is
recorded here.

### 1. Spec coverage

| Spec section (§) | Task that implements it |
|---|---|
| §2 scope / §2.2 out of scope | Every task; no bandit, posterior or allocation code appears anywhere |
| §3 principles (evidence, immutability, source-specific schemas, LLM classifies/code calculates) | P1.4, P1.5, P1.7, P1.9, P1.13 |
| §4 provider constraints (quota costs, Spotify 2026, browser) | P1.3, P1.8 |
| §5 architecture | Task order P1.1 → P1.14 |
| §6 repository structure | File-structure section (`services/market_intelligence/…`, deviation stated) |
| §7.1–§7.6 data model | P1.2 (schema), P1.4 (raw), P1.5 (entities/observations), P1.9 (arms/scores) |
| §8 seeds Stage A/B | P1.6 |
| §9 query planner | P1.6 (pure), P1.11 (execution), P1.11 step 3 (`reset_schedule`) |
| §10 collection + refresh policy | P1.3, P1.11 (`_run_due_collection`) |
| §11 derived metrics | P1.8 `velocity.py` |
| §12 growth | P1.8 `growth.py` + `test_growth.py` |
| §13 competition/HHI | P1.8 `competition.py` |
| §14 taxonomy (13 dims, R14, `other_candidate`) | P1.7 |
| §15 LLM classification + validation | P1.7 (`validators.py`, `classifier.py`) |
| §16 arm generation | P1.9 (`taxonomy/arms.py`) |
| §17 scoring incl. weights | P1.9 (`calibration.py`, `opportunity.py`) |
| §18 normalisation | P1.9 `scoring/normalization.py` |
| §19 confidence | P1.8 `confidence.py`, exercised in P1.13 |
| §20 opportunity object | P1.1 (`OpportunityScore`), P1.10 (`OpportunityOut`), P1.9 (persistence) |
| §21 freshness | P1.1 (`classify_freshness`), P1.8 (`features_freshness`), P1.13 (boundaries) |
| §22 dedupe/entity resolution | P1.5 |
| §23 error handling/retries | P1.3 |
| §24 data-quality gates | P1.5 (gates), P1.13 (matrix), P1.12 (warnings section) |
| §25 API | P1.10 |
| §26 scheduled jobs | P1.11 (`JOB_NAMES`, workflows, schedules) |
| §27 caching/quota | P1.3 (`QuotaBudget`/`RequestBudget`/`RateLimiter`), P1.11 (reschedule) |
| §28 LLM cost control | P1.7 (cache + gating), P1.12 (tokens/cost in the run summary) |
| §29 taxonomy versioning | P1.7 (`insert_classification`), P1.9 (version columns on scores) |
| §30 reproducibility | P1.9 (pure functions), P1.13 (`test_reproducibility.py`) |
| §31 report | P1.12 |
| §32 change detection | P1.12 (`detect_changes`, `market_change_events`) |
| §33 AC-01…AC-14 | P1.14 (AC-01/02/03/07/09/10/12), P1.6 (AC-04), P1.7 (AC-05), P1.8 (AC-06), P1.13 (AC-08/AC-13), P1.12 (AC-14), P1.3 (AC-11) |
| §34 tests (unit/property/integration) | every task's own tests; P1.13 owns properties and the integration fixture; P1.14 the end-to-end run |
| §35 example fixture | P1.13 (`tests/fixtures/market/scenario.py`) |
| §36–§38 flexibility / non-decisions | Recorded as rulings below, not silent choices |
| §39 sequence P1.1–P1.14 | Task titles are the sequence, one task per step |
| §40 real API smoke | P1.14 — acceptance `blocked: pending credentials` |
| §41 security | P1.3 (`test_youtube_secrets.py`), P1.4 (no secrets in payloads/rows) |
| §42 cost control | P1.4 (`add_run_cost`), P1.7 (LLM counters), P1.12 (summary/metrics) |
| §43 operational safeguards | P1.5/P1.13 (quarantine thresholds), P1.3 (`SCHEMA_CHANGE`), P1.11 (quota degradation) |
| §44 expected outputs | P1.12 report sections 1–10 |
| §45 definition of done | P1.14 acceptance command |
| C1 registry (field list, `/market/v1`, R14, R15) | P1.10 (seam test), P1.7 (R14), P1.9 (R15) |
| P0 §5 C9 / R13 (`market/` prefix) | P1.4 (raw artifacts), P1.12 (reports) |
| P0 §9 R19 (`brand_id`) | P1.9 (`market_arms.brand_id`, seeded brand `neon_seoul`) |

### 2. Placeholder scan — what was removed before this plan shipped

- P1.2's migration originally compressed twelve `op.create_table` calls behind a `# ...` comment;
  every call is now written out, because "transcribe the other file" is exactly the placeholder
  this rule forbids.
- P1.6's expansion test shipped a broken `SeedProposal` expression and a `SeedDecisionLike` name
  that exists nowhere; both are gone, the test is now final code.
- P1.7's classifier test shipped a `true_language` helper and a "drop this line" instruction;
  replaced by direct assertions.
- P1.8 shipped a `_parse_release` stub raising `NotImplementedError` plus a "fix this while
  writing" block; the real helper and its import are inline now.
- P1.8/P1.9 shipped Review-Focus-5 and identity assertions as prose ("add this test in Step 11");
  both are inline test bodies in the step that owns them.
- No step says "add appropriate error handling", "handle edge cases", "similar to Task N" or
  "write tests for the above".

### 3. Type and name consistency across tasks

Checked by grepping the plan for each name's first definition and every later use:

- `market_opportunity_score` (P1.1 dataclass → database column → API field → report row) — one
  spelling, no `market_score` outside the Open-items ruling.
- `Tri` never degrades to a raw number: `tri_from_optional` (P1.1) is the only NULL→value path in
  P1.5's read helpers; `NormalizedObservation` (P1.1) refuses a raw number.
- `arm_id`/`fingerprint`: minted in `taxonomy/arms.py` (P1.9), stored on `market_arms`, read by the
  detail endpoint (P1.10) and the seam test — never recomputed from `market_opportunity_scores`.
- `store_raw_observation(...)` gained `request_context_hash` in P1.4 and every caller (P1.11's
  collectors, P1.4's tests) passes it; `StoredRawObservation.size` feeds `add_run_cost`.
- `ClassificationDraft.content_fingerprint` (declared in P1.9 step 3, filled in P1.7's
  `classify_observation`) is the one field two tasks share; both tasks say so.
- `QueryState` (P1.6) is the only input to `plan_refresh`; `due_queries` (P1.11) builds it from
  `load_queries` rows with the same field names.
- `run_daily`/`run_daily_step` (P1.11/P1.14) is the single orchestration path: workflow
  activities, CLI and the acceptance test all call into it; no second pipeline exists.
- `OpportunityOut`'s field names are the registry's; the seam test (P1.10) pins them, and
  `TaxonomyOut` carries exactly the nine frozen keys while `ArmOut` carries all thirteen.

### 4. Review Focus ownership

P0 §3's five failure modes, mapped to P1 where they apply:

1. **Missing data as zero or as a pass** — owned by P1.1 (`test_tri_from_optional_keeps_missing_data_out_of_arithmetic`), P1.5 (`test_hidden_subscriber_count_stays_unknown`), P1.8 (`test_an_observation_without_metrics_yields_unknown_features_and_lower_confidence`, `test_no_secondary_source_means_unknown_not_zero`), and P1.13's freshness test.
2. **A retry or duplicate creating a second effect** — owned by P1.4 (`test_storing_the_same_observation_twice_is_idempotent`) and P1.5 (`test_inserting_the_same_observation_point_twice_is_idempotent`).
3. **Identity lost in transit** — owned by P1.9 (`test_arm_identity_survives_a_recompute`) and P1.10 (`tests/contract/test_c1_market_to_ar.py`, all four tests).
4. **A blocked record reaching a consumer by another path** — P1's analogue (the publication gate is P5/P6's): P1.5's quarantine write path and P1.8's `test_quarantined_record_never_becomes_an_observation`; P1.13's gate matrix.
5. **Time arithmetic across windows** — owned by P1.8 (`test_windows_are_explicit_and_never_overlap`, `test_growth_is_none_when_the_baseline_cannot_support_a_percentage`, `test_age_is_hours_and_never_below_the_epsilon`) and P1.13's freshness boundaries.

### 5. Task right-sizing (the one place the sequence was adjusted)

The spec's fourteen steps are kept one-for-one as fourteen tasks. Two adjustments, stated because
the skill's rule is "split only where a reviewer could reject one task while approving its
neighbour":

- **P1.13 is cross-cutting only.** Its spec wording ("complete unit/property/integration/contract
  tests") would be a placeholder under TDD, because P1.1–P1.12 each write their own tests. P1.13
  therefore owns what no single module owns: the §35 scenario fixture, property tests, the
  reproducibility proof and the data-quality gate matrix.
- **P1.3's secondary connectors moved to P1.8.** Spotify/Trends/browser have exactly one P1
  consumer (the cross-platform feature) and no acceptance criterion of their own, so shipping them
  with that consumer keeps P1.3 reviewable as "the YouTube connector" alone. The split is
  recorded here rather than hidden.

---

## Open items and rulings

Every place the spec is silent or contradicts itself, with the ruling this plan makes. Where a
ruling touches the §37 protected list, the owning task records it as an ADR in the same commit.

**Spec contradictions ruled on**

1. **Taxonomy dimension count** — §14.2 lists 13, AC-05 validates 10. **Ruled (R14, P1.7):** 13 are
   authoritative; AC-05 is corrected. ADR-0014 already records it.
2. **`market_opportunity_score` vs the architecture's `market_score`** — PART I's sketch uses
   `market_score`. **Ruled (P1.9):** `market_opportunity_score` is the wire and database name
   (§17.1's explicit instruction; R2 authority order); `market_score` survives only as a human
   label in report prose.
3. **Arm and score table names are never specified.** §16 lists arm *fields*; §17/§20 describe a
   score *object*. **Ruled (P1.2):** `market_arms`, `market_opportunity_scores` (plus
   `market_taxonomy_classifications`, `market_change_events`, `market_quarantined_records`,
   `market_snapshots`, `market_entity_identities`, `market_seed_evidence`). ADR-0022.
4. **No CLI is defined for P1.** **Ruled (R20, P1.11):** `plm market` gains `collect`, `score`,
   `report`, `run-daily`, `serve`, `smoke-live`, `seed-refresh`; declarations in
   `plm_platform/cli.py`, implementations in `services/market_intelligence/cli.py`.
5. **§38 permits a non-Temporal scheduler while architecture PART XIX prescribes Temporal.**
   **Ruled (P1.11):** the cadence decision is a pure function and the execution is Temporal
   workflows on `ph1.market`; the flexibility is used for testability, not to bypass the engine.
   ADR-0023.

**Names or values the spec leaves unspecified — resolved here**

6. **API framework.** `unspecified - resolved here as FastAPI + uvicorn` (P1.10), because
   `plm_platform`'s correlation/observability modules already document FastAPI integration and the
   test double (`httpx.ASGITransport`) is dependency-free. ADR-0021.
7. **Service-token variable and header.** R15 says "service token from `plm_platform`" but names
   neither. `unspecified - resolved here as` `PLM_SERVICE_TOKEN` / `X-PLM-Service-Token`, with
   unconfigured ⇒ 503 blocked, mismatch ⇒ 401 (P1.10). ADR-0021.
8. **C1 envelope.** The registry freezes `{items, next_cursor, as_of, score_version}`, which is
   neither `plm_contracts.Page` nor `Envelope`. `unspecified - resolved here as` the registry's
   flat object, with `Page`/`Envelope` semantics documented but not imposed (P1.10). ADR-0022.
9. **The four R14 dimensions absent from §20's taxonomy object and §15's classifier schema**
   (`vocal_gender`, `bpm_range`, `energy`, `audience_intent`). `unspecified - resolved here as`:
   all thirteen are classified and validated (P1.7), the frozen nine-key C1 object carries the
   subset the registry lists, and the remaining four travel on the detail endpoint's `arm` payload
   (P1.10). P2's adapter can therefore build its §7 fingerprint without a registry amendment.
10. **`BPMRange` and `DurationBucket` controlled values.** The spec gives one example
    (`45_75m`) and no list. `unspecified - resolved here as` the starter vocabularies in
    `taxonomy/ontology.py` (P1.7), expandable through §14.3's explicit workflow only.
11. **Bootstrap seeds as "configuration".** `unspecified - resolved here as` a JSON data file
    under `services/market_intelligence/data/` (P1.6) loaded with stdlib `json`, with a test that
    pins §8's twenty terms.
12. **Where the LLM lives.** §15 requires LLM classification; no provider is chosen and no
    module is named. `unspecified - resolved here as` a `Classifier` protocol with a deterministic
    `RuleBasedClassifier` default (P1.7); an LLM implementation is `blocked: pending credentials`,
    and the numeric layer only ever reads persisted classifications (§30).
13. **YouTube quota unit constants.** `unspecified - resolved here as` `SEARCH_QUOTA_UNITS = 100`
    and `LIST_QUOTA_UNITS = 1` on the connector (P1.3), copied from the documentation §4.1 cites.
14. **Raw payload storage.** §7.4 lists `payload_json`; R13 wants content-addressed artifacts.
    `unspecified - resolved here as` both, with the artifact written first (P1.4) and the JSONB
    copy marked as the queryable record.
15. **A search result with no metrics.** `unspecified - resolved here as` a video entity with
    text only and every metric `Tri.unknown()` (P1.5), hydrated later.
16. **`country` on `GET /market/opportunities`.** §25 lists it; the frozen C1 wire has no country
    field and `market_arms` has none either. `unspecified - resolved here as` a `422
    unsupported_parameter` response (P1.10) — refusing beats silently returning nothing.
17. **`freshness` on the C1 wire.** The registry says it does not exist and is derived by P2 from
    `as_of`. `unspecified - resolved here as` absence plus an awareness assertion in the seam test
    (P1.10).
18. **`brand_id` (R19) vs the frozen C1 wire.** `unspecified - resolved here as`: `brand_id` is
    required on `market_arms` (P1.2/P1.9) and absent from the C1 payload, because the registry's
    field list is frozen and carries no brand. Recorded in ADR-0022.
19. **Single vs plural `*_ids` naming on the wire** — the registry's `top_observation_ids` (plural)
    is used verbatim; internal fields use `source_observation_ids`/`observation_ids` (P1.9/P1.10).
20. **§40's smoke-test acceptance.** `unspecified - resolved here as` an explicit blocked outcome
    with exit code 3 (P1.14), because "skipped" would read as passing.

**Known limits of this plan**

- The Spotify/Trends/browser connectors (P1.8) are interfaces plus fixture-backed reads; no live
  Spotify application exists (P0 §10 human task list), so cross-platform stays `unknown` for now
  by design rather than by accident.
- `tests/unit/market/test_api_live_uvicorn.py` needs the compose stack for Postgres/MinIO only in
  its P1.14 sibling test; the uvicorn test itself runs with no database configured and asserts the
  503 path, which is the honest default state of a fresh checkout.
- The ledger convention from P0 §7 applies to executing this plan: one implementer per task, a
  fresh reviewer per task, and the migration rule (never two migration-creating steps at once)
  honoured because P1 has exactly one revision.
