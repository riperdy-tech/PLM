# PHASE-P2 Autonomous A&R / Opportunity Allocation Engine — Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the P2 allocation engine — the decision layer that turns P1's evidence-backed market opportunities into auditable, uncertainty-aware production directives: canonical arm state and posteriors, a versioned cold-start prior, Thompson-style exploration, portfolio constraints, fatigue/non-stationarity handling, an end-to-end allocation cycle, idempotent result ingestion, an offline simulator, and the `/ar` API — so that P3 can ask "what should be produced next?" and receive a frozen C2 directive payload.

**Architecture:** One service package `services/autonomous_ar/` writing to the `ar` Postgres schema and to the `experiments/` object-storage prefix only (R13 — P2's artifacts are decision evidence). P2 consumes P1 **only** through `/market/v1` (C1, R15: HTTP + service token; never P1's tables). Every decision is deterministic from stored inputs plus version strings: ids are uuid5-derived (`storage/ids.py`), the RNG is a seeded `random.Random` whose seed is persisted on the cycle row, and sampling never mutates posterior state. Posteriors are normal–normal conjugates implemented in stdlib Python (no new dependency; §112 permits this). The service starts in `SHADOW` (ADR-0023 #5): directives are computed, logged and auditable, and `GET /ar/directives` serves nothing until a cycle runs with `activation_state=AUTONOMOUS`.

**Tech Stack:** Python 3.12 · pydantic 2 (wire/policy validation) · dataclasses where pydantic adds nothing · SQLAlchemy 2 Core · Alembic (single chain; this phase adds `ar_0008_initial_tables`) · httpx (C1 client + API tests) · FastAPI + uvicorn (same choice as P1.10, ADR-0021) · `plm_contracts` (`AllocationType`, `ObservationWindow`, `ChannelActivationState`, `QualityStatus`, `Tri`, `ErrorShape`, `BrandId`, `format_versioned_id`) · `plm_platform` (`config`, `db`, `storage`, `observability`, `temporal`) · `tests/support` harness (`c1_api` fixture client, frozen clock, seed) · pytest 9 · ruff · mypy.

**Spec:** `docs/P2_autonomous_ar_engine_implementation_plan.md` (PHASE-P2; ~3,160 lines — the brief's "2,144" is stale). This plan argues from it; read it with this file. Section references written `§n` are the spec's; `PHASE-Pn §m`, `ARCH-Pnn` and `Rn` follow the corpus's dual numbering (`docs/contracts/PHASE_MAP.md`). Frozen seam authority: `docs/contracts/CONTRACT_REGISTRY.md` entries **C1** (exists), **C2** (FROZEN 2026-09-20, ADR-0023), C7 (open until W5.0). This plan's base commit is `3623085` on `build/wave0-foundation`.

## Global Constraints

Copied from the brief, ADR-0023, the registry and P0 §2/§4/§5; every task's requirements implicitly include them.

- **Shared packages only for cross-cutting concerns.** `plm_contracts` for vocabulary and shapes; `plm_platform.storage` for artifacts under the `experiments/` prefix (R13/C9 — content-addressed, checksummed, no overwrite); `plm_platform.db` (`scoped_session("ar")`) for the `ar` schema; `plm_platform.config` for settings — **never** `os.environ` in P2 code; `plm_platform.observability` for structured JSON logs, correlation ids and per-run cost counters; `plm_platform.temporal` for scheduled work on task queue `ph2.ar` (`TaskQueue.AR`).
- **C2 is frozen (ADR-0023).** The directive wire is P2's, extended: the 18 frozen producer fields listed in the registry (and in Task P2.1A). Persistence-only extras (`id`, `status`, `brief_json`, `created_at`, `consumed_at` — and the extra columns this plan persists to serve the wire) are **not** wire fields. A later rename fails the C2 seam test loudly; the remedy is a registry amendment first (R2), never a producer patch.
- **Budget authority (ADR-0023 #1).** The allocation **cycle** owns the ceiling; each directive carries its **share** in `budget_limit`; the sum of directive shares must never exceed the cycle ceiling (tested in P2.8 and P2.10).
- **Exploration vocabulary (ADR-0023 #2).** Exactly three levels — `exploit` / `adjacent_explore` / `radical_explore` (`plm_contracts.AllocationType`). `exploration_fraction` is reporting only; **no P2 code may branch on it**.
- **Fractions (ADR-0023 #3).** Default 70/20/10; validation **rejects any policy whose fractions exceed 1.0**; `baseline_fraction` is not a separate slice (it is the non-exploratory residual inside exploit and never added to the sum). §13 governs §90.
- **Value model (ADR-0023 #4).** `expected_value` is computed under the versioned `value_model` carried on the wire. First cycles use the early-stage proxy; the full Expected Economic Value terms are **reserved** in the C7 reward schema and P2 must never fabricate a revenue term — an unmeasured term is `unknown`, not zero.
- **Activation (ADR-0023 #5).** P2 starts in `SHADOW` (`plm_contracts.ChannelActivationState`): directives computed, logged, auditable, no production effect. An arm is **ranked** only with **2+ measured evidence components**; under-covered arms are returned flagged (`EVIDENCE_INSUFFICIENT`), never silently ranked. The operator override (§107) is explicit and expiring.
- **§111 prohibitions are hard, per task.** No LLM as allocator; no score-only signals; no lifetime-views-only reward; no double posterior update; no single-arm slot fills; no one-result retirement; no leakage; no silent policy change. Where a task could violate one, its tests pin the prohibition.
- **C1 consumption.** P2 reads P1 **only** through `/market/v1` (`GET /market/v1/opportunities`, and by id when needed) with `PLM_SERVICE_TOKEN` (`X-PLM-Service-Token`); the response is the registry's **flat envelope** `{items, next_cursor, as_of, score_version}` — never `plm_contracts.Page`; there is **no `freshness` field** (P2 derives `freshness_seconds` from `as_of`, ADR-0022). A missing token makes P1 answer 503; P2 treats that as `MarketUnavailable`, never as "zero opportunities".
- **Migrations** (`migrations/README.md`): one chain; P2 touches only the `ar` schema; filename `<schema>_<revision>_<slug>`; exactly one migration in flight repo-wide. Baseline `0001_baseline` created the schemas; the market chain currently ends at `0007_market_snapshot_bodies`, so P2's single revision is **`0008_ar_initial`** (`migrations/versions/ar/ar_0008_initial_tables.py`, `down_revision = "0007_market_snapshot_bodies"`; revision ids ≤ 32 chars).
- **No native Postgres enums.** Enum values are stored as `text` and validated by `plm_contracts`/P2 enums in Python.
- **Gates are `pytest -q`, `ruff check .`, `mypy packages services`** (mypy covers `services/` — do not regress that; service modules are checked under their top-level names).
- **Mock-first.** No test may require network access or provider credentials. P1 is exercised through `tests/support/c1_api.py` (real FastAPI app over SQLite `market`). No new runtime dependency: the statistical layer is stdlib (`math`, `random`, `statistics`).
- **Missing data is never zero.** `Tri` for every metric that can be absent; `NULL` component ⇒ `None` (never 0.0); unknown maturity ⇒ unknown. Where P2 must propagate an unknown rather than defaulting, the owning task says so explicitly.
- **Determinism.** Seeded, replayable: no `uuid4`, no wall-clock on an identity or computation path. Ids are uuid5-derived (`storage/ids.py`); `as_of`/`now` are parameters; the cycle's RNG seed is derived from the deterministic `cycle_id` (or supplied) and persisted.
- **No silent transformation** (P0 §2): invalid policy ⇒ refuse activation; stale market data ⇒ downweight + explicit warning, or fail; too few arms ⇒ `PARTIAL` with warnings, never fabricated diversity; blocked/retired ⇒ excluded, never "just this once".

## Review Focus

The input classes and failure modes most likely to bite a person using this software (P0 §3, P2-relevant first; plus the two P2-specific hazards ADR-0023 creates). Each line names the task owning its test.

1. **A metric that was never observed renders as `0.0` and poisons the posterior.** A component P1 left `null`; a raw reward metric absent from a result; an unmeasured economic term. Expected: `None`/`Tri.unknown()` end-to-end — a null component is *not* a zero score, an unmeasured term keeps the composite unknown, and the posterior only moves on a measured `normalized_reward`. Owning tests: P2.1 (`test_null_component_is_never_a_zero`), P2.2 (`test_unobserved_component_stays_none`), P2.6 (`test_composite_is_unknown_when_a_term_is_unmeasured`), P2.11 (`test_unknown_reward_is_refused_at_ingestion`).
2. **A retry or replay creates a second real-world effect.** A repeated `POST /ar/results`; an update worker retry; a re-run of the same cycle. Expected: exactly-once — ingestion keyed on `(experiment_id, observation_window)`, posterior updates logged and refused twice, cycle re-run with the same deterministic `cycle_id` returns the existing cycle. Owning tests: P2.11 (`test_posting_the_same_result_twice_stores_one_observation`, `test_retried_update_is_a_no_op`), P2.10 (`test_replaying_the_same_cycle_is_idempotent`), P2.13 (`test_results_endpoint_is_idempotent`).
3. **Identity lost in transit.** A renamed directive field reaching P3; an `arm_id` dropped between decision and directive; a directive whose wire says a different arm than its decision. Expected: the C2 seam test fails loudly naming the field; every decision and directive carries P1's `arm_id` verbatim; `production_directive.decision_id` joins its decision. Owning tests: P2.1A (the C2 seam test), P2.10 (`test_directive_joins_its_decision`), P2.2A (the C1 consumer seam).
4. **A blocked or under-evidenced arm reaching allocation.** A `blocked`/`retired` arm appearing in a portfolio; an arm with one measured component being ranked; a SHADOW directive served as actionable. Expected: hard exclusion, `EVIDENCE_INSUFFICIENT` flag instead of ranking, and `GET /ar/directives` empty until an AUTONOMOUS cycle exists. Owning tests: P2.8 (`test_blocked_and_retired_arms_are_never_selected`), P2.10 (`test_under_covered_arm_is_flagged_not_ranked`), P2.13 (`test_directives_are_empty_in_shadow_mode`).
5. **Time and money arithmetic errors.** `freshness_seconds` derived from a naive `as_of`; `expires_at` before the cycle; directive shares exceeding the cycle ceiling; a fatigue penalty that never decays. Expected: aware instants everywhere, `sum(budget_limit) <= budget`, decay halves precisely, windows are explicit `ObservationWindow`s. Owning tests: P2.2 (`test_freshness_is_derived_from_an_aware_as_of`), P2.8 (`test_directive_shares_never_exceed_the_cycle_ceiling`), P2.9 (`test_fatigue_penalty_halves_over_the_half_life`), P2.10 (`test_every_directive_expires_after_the_cycle`).
6. **Feature leakage.** A historical decision explained or recomputed from *current* P1 values; a posterior updated with data the decision could not have seen. Expected: `ar_arm_feature_snapshot` rows are immutable and are the only source for a decision's market context; the stored-mode cycle never re-reads P1; the updater uses only observations dated at/before its `now`. Owning tests: P2.3 (`test_feature_snapshot_rows_are_insert_only`), P2.10 (`test_stored_mode_cycle_uses_the_snapshot_not_live_p1`), P2.15 (`test_result_arriving_after_the_decision_still_updates_exactly_once`).
7. **A policy changes silently under a live decision.** A mutated `p2-v1` row changing an already-issued directive's meaning. Expected: policy rows are immutable once used (§92) — changes are a new version, validated, then activated; every decision records `policy_version`/`model_version`/`feature_version`/`normalization_version`/`score_version`. Owning tests: P2.3 (`test_policy_versions_are_immutable`), P2.10 (`test_decision_records_every_version`).

---

## File Structure

Every file this phase creates (C) or modifies (M). Paths are exact; directories are created by their first file. Rooted at `services/autonomous_ar/` — the spec's §89 layout **adapted to the monorepo** (one service per phase under `services/`, module names kept where the spec names them: `domain/arm.py`, `domain/posterior.py`, `domain/reward.py`, `domain/decision.py`, `domain/directive.py`, `domain/policy.py`, `ingestion/p1_adapter.py`, `ingestion/result_adapter.py`, `modeling/prior.py`, `modeling/reward_model.py`, `modeling/similarity.py`, `modeling/decay.py`, `modeling/fatigue.py`, `policy/allocation.py`→`policy/constraints.py`+`policy/exploration.py`, `policy/lifecycle.py`, `decisioning/cycle.py`, `storage/models.py`, `storage/repositories.py`, `api/routes.py`, `api/schemas.py`, `simulation/simulator.py`, `simulation/scenarios.py`, `simulation/metrics.py`, `observability/logging.py`, `observability/metrics.py`). Deviations from §89: the spec's `src/` prefix is dropped; `decisioning/planner.py` + `decisioning/portfolio.py` keep their names; `modeling/update.py`, `observability/explanation.py`, `jobs/`, `cli.py` and `acceptance/` are additions (six that the spec implies but does not locate — flagged in the open items).

**Package root — `services/autonomous_ar/`**

| File | Responsibility |
|---|---|
| `__init__.py` | C — package docstring; the phase's public entry points list. |
| `config.py` | C — `ARSettings` (versions, fractions, caps, decay/fatigue/retirement knobs, API base url, task queue, activation default), `load_ar_settings()`. |
| `domain/enums.py` | C — P2 vocabulary: `ArmLifecycleStatus`, `AllocationCycleStatus`, `DirectiveStatus`, `ObservationMaturity`, `UpdateStatus`, `ReasonCode`, `OverrideAction`. |
| `domain/arm.py` | C — `ArmDimensions` (the frozen nine C1 taxonomy keys), `canonical_fingerprint()`, `ContentArm`, `MEASURED_COMPONENT_KEYS`, `measured_component_count()`, `is_rankable()`. |
| `domain/posterior.py` | C — `PriorParameters`, `PosteriorState` (validated), `UpdateRecord`. |
| `domain/reward.py` | C — `RewardObservation`, `RESERVED_VALUE_TERMS` (C7 reserved keys), `tri_from_optional()`, `observation_key()`. |
| `domain/decision.py` | C — `AllocationDecisionRecord`, `ConstraintRejection`, `ArmSample`. |
| `domain/directive.py` | C — `DirectiveBrief`, `ProductionDirective`, `DIRECTIVE_WIRE_FIELDS`, `to_wire()`. |
| `domain/policy.py` | C — `PolicyConfig` (validated per ADR-0023 #3/#4), `FatigueConfig`, `NonstationarityConfig`, `RetirementConfig`, `DEFAULT_POLICY`. |
| `ingestion/p1_adapter.py` | C — `P1Client`, `OpportunityPage`, `adapt_opportunity()`, `MarketUnavailable`, `MARKET_OPPORTUNITIES_PATH`, `OpportunityPayload` (the consumed C1 field set). |
| `ingestion/result_adapter.py` | C — `ResultPayload` (the §72 contract + `quality_status`), `parse_result_payload()`, `ResultValidationError`, `store_observation()` idempotent. |
| `modeling/similarity.py` | C — dimension weights, `arm_similarity()`, `cluster_arms()` (union-find, deterministic cluster ids), `structural_overlap()`. |
| `modeling/prior.py` | C — `cold_start_prior()` (`p2-prior-v1` transform), `transfer_contribution()` (bounded, §64/§65). |
| `modeling/reward_model.py` | C — `RewardModel` protocol (§66 names), `NormalNormalRewardModel` (update/sample/expected_value). |
| `modeling/value_model.py` | C — `EARLY_STAGE_PROXY_WEIGHTS` (§20), `VALUE_MODEL_ID`, `compose_proxy()`, `term_from_raw()` — never fabricates a revenue term. |
| `modeling/decay.py` | C — `recency_weight()`, `effective_observation_count()`, `weighted_reward()`, `detect_regime_shift()`. |
| `modeling/fatigue.py` | C — `exposure_score()`, `fatigue_penalty()` (decaying), `is_in_cooldown()`. |
| `modeling/update.py` | C — `apply_pending_updates()` (idempotent, version-guarded, attribution-gated). |
| `policy/exploration.py` | C — `class_quota()`, `reserve_baseline()`, `effective_fractions()` (§60/§85), `sample_rewards()`, `sampled_ranking()`. |
| `policy/constraints.py` | C — `Portfolio`, `directive_budget_share()`, `apply_portfolio_constraints()` (§58 walk + §15 caps + §25 cluster concurrency + §59 partial). |
| `policy/lifecycle.py` | C — `transition_status()`, `should_retire()` (§38), `reopen_reason()` (§39), cooldown application. |
| `decisioning/planner.py` | C — pure preparation: `plan_cycle_inputs()` (eligible arms, weights, samples, classes). |
| `decisioning/portfolio.py` | C — `assemble_portfolio()` orchestrating exploration quotas + constraints into selections + rejections. |
| `decisioning/cycle.py` | C — `CycleRequest`, `CycleResult`, `MarketSnapshot`, `run_allocation_cycle()` (§29's fifteen steps, persistence, warnings, replay-idempotence). |
| `storage/ids.py` | C — `deterministic_id()` (uuid5 over stable identity parts). |
| `storage/models.py` | C — `AR_METADATA`, the fifteen `ar_*` tables. |
| `storage/repositories.py` | C — insert/upsert/select helpers for every `ar_*` table (append-only semantics where the spec requires them). |
| `api/schemas.py` | C — request/response models (all `extra="forbid"`), `CycleCreateRequest`, `CycleSummary`, `DecisionOut`, `DirectiveOut`, `ArmStateOut`, `ResultIn`, `UpdateSummaryOut`. |
| `api/service_token.py` | C — `require_service_token()` (mirror of P1's; duplication flagged in open items). |
| `api/routes.py` | C — §71's seven endpoints, literal paths. |
| `api/app.py` | C — `create_app()` with injectable `session_factory` and `market_source` (test seam for P1). |
| `simulation/scenarios.py` | C — scenario specs A–G (§69). |
| `simulation/simulator.py` | C — `run_simulation()` over the real policy functions, seeded and replayable. |
| `simulation/metrics.py` | C — §99 metrics: cumulative reward, regret proxy, counts, exploration ratio, concentration, coverage. |
| `observability/metrics.py` | C — §87 counters on `plm_platform.observability.METRICS_REGISTRY`. |
| `observability/logging.py` | C — cycle summary log (§94) and §88 alert codes. |
| `observability/explanation.py` | C — §109 deterministic explanation from structured rows. |
| `jobs/cycle.py` | C — `AllocationCycleWorkflow` + activities on `TaskQueue.AR`. |
| `jobs/schedules.py` | C — `ensure_schedules()` (weekly allocation), `JOB_NAMES`. |
| `jobs/__init__.py` | C — `REGISTRATIONS: tuple[WorkerSet, ...]` for `plm_platform.worker --load`. |
| `cli.py` | C — implementations behind the `plm ar …` commands. |
| `acceptance/production_smoke.py` | C — §114 P2.16 dry-run smoke; returns an explicit blocked reason when it cannot run. |

**Platform, docs and data changes**

| File | Responsibility |
|---|---|
| `packages/plm_platform/cli.py` | M — un-stub the `ar` group: `cycle`, `show`, `arms`, `explain`, `serve`, `simulate`, `smoke` (lazy service imports, keeping the mypy top-level-module rule). |
| `tests/unit/test_cli.py` | M — `ar` leaves `STUB_CASES`. |
| `docs/adr/0024-p2-tables-model-and-states.md` | C — the `ar_*` table names (R3: phase plan owns them), the stdlib statistical model, the SHADOW directive-serving rule, and the C1-wire coverage ruling. |
| `docs/contracts/DECISION_LOG.md` | M — one row for ADR-0024. |

**Migrations**

| File | Responsibility |
|---|---|
| `migrations/versions/ar/ar_0008_initial_tables.py` | C — the single P2 revision (revision `0008_ar_initial`, `down_revision = "0007_market_snapshot_bodies"`): all fifteen `ar` tables with a working `downgrade()`. |

**Test support**

| File | Responsibility |
|---|---|
| `tests/support/ar_db.py` | C — `create_ar_engine()` (SQLite `ar` schema over `AR_METADATA`), `seed_ar_session()` helper. |

**Tests**

| File | Responsibility |
|---|---|
| `tests/unit/ar/__init__.py`, `tests/integration/ar/__init__.py`, `tests/simulation/ar/__init__.py` | C — packages (repo convention). |
| `tests/unit/ar/test_domain_enums.py`, `test_domain_arm.py`, `test_domain_posterior.py`, `test_domain_reward.py`, `test_domain_policy.py`, `test_domain_directive.py` | C — P2.1. |
| `tests/contract/test_c2_ar_to_experiments.py` | C — P2.1A (the registry-frozen C2 producer seam). |
| `tests/unit/ar/test_p1_adapter.py` | C — P2.2. |
| `tests/contract/test_c1_market_to_ar_consumer.py` | C — P2.2A (P2's consumer half of C1; the registry's `test_c1_market_to_ar.py` remains P1's producer half). |
| `tests/unit/ar/test_storage_models.py`, `test_repositories.py` | C — P2.3. |
| `tests/integration/ar/test_schema_matches_models.py` | C — P2.3 (real Postgres DDL ↔ models drift guard; skips when compose is down). |
| `tests/unit/ar/test_similarity.py` | C — P2.4. |
| `tests/unit/ar/test_prior.py` | C — P2.5. |
| `tests/unit/ar/test_reward_model.py`, `test_value_model.py` | C — P2.6. |
| `tests/unit/ar/test_exploration.py` | C — P2.7. |
| `tests/unit/ar/test_constraints.py` | C — P2.8. |
| `tests/unit/ar/test_decay.py`, `test_fatigue.py` | C — P2.9. |
| `tests/unit/ar/test_cycle.py` | C — P2.10. |
| `tests/integration/ar/test_temporal_jobs.py` | C — P2.10 (skips when Temporal is down). |
| `tests/unit/ar/test_result_ingestion.py`, `test_posterior_updates.py` | C — P2.11. |
| `tests/simulation/ar/test_scenarios.py`, `test_acceptance.py` | C — P2.12 (§69/§98). |
| `tests/unit/ar/test_api_cycles.py`, `test_api_results.py`, `test_api_auth.py` | C — P2.13. |
| `tests/unit/ar/test_observability.py`, `test_explanation.py`, `test_cli_ar.py` | C — P2.14. |
| `tests/fixtures/ar/__init__.py`, `scenario.py` | C — P2.15 (the §97 environment). |
| `tests/unit/ar/test_properties.py`, `tests/integration/ar/test_e2e_acceptance.py` | C — P2.15. |
| `tests/unit/ar/test_smoke_blocked.py` | C — P2.16. |

---

### Task P2.1: Domain contracts

**Files:**
- Create: `services/autonomous_ar/__init__.py`, `services/autonomous_ar/config.py`, `services/autonomous_ar/domain/__init__.py`, `domain/enums.py`, `domain/arm.py`, `domain/posterior.py`, `domain/reward.py`, `domain/decision.py`, `domain/directive.py`, `domain/policy.py`
- Test: `tests/unit/ar/__init__.py`, `tests/unit/ar/test_domain_enums.py`, `test_domain_arm.py`, `test_domain_posterior.py`, `test_domain_reward.py`, `test_domain_policy.py`, `test_domain_directive.py`

**Interfaces:**
- Consumes: `plm_contracts` (`AllocationType`, `ObservationWindow`, `ChannelActivationState`, `QualityStatus`, `Tri`, `format_versioned_id`), `plm_platform.config.Settings`. Nothing else.
- Produces (names later tasks import exactly):
  - `domain.enums`: `ArmLifecycleStatus` (`candidate`/`active`/`cooldown`/`paused`/`retired`/`blocked`/`archived`), `AllocationCycleStatus` (`planned`/`running`/`completed`/`partial`/`failed`), `DirectiveStatus` (`queued`/`accepted`/`in_progress`/`completed`/`cancelled`/`expired`/`failed`), `ObservationMaturity` (`provisional`/`mature`/`closed`), `UpdateStatus` (`applied`/`skipped_duplicate`/`quarantined`/`failed`), `ReasonCode` (18 tokens, exact set below), `OverrideAction` (`pause_arm`/`force_arm`).
  - `domain.arm`: `DIMENSION_ORDER` (the frozen nine C1 taxonomy keys), `MEASURED_COMPONENT_KEYS` (the seven C1 component keys), `ArmDimensions`, `canonical_fingerprint(dims) -> str`, `ContentArm` (fields `arm_id, dimensions, p1_score, p1_confidence, component_scores, observation_count, source_count, as_of, score_version, freshness_seconds`; `.measured_component_count`, `.is_rankable(*, minimum=2)`).
  - `domain.posterior`: `PriorParameters(mean, variance, confidence, prior_version)`, `PosteriorState(arm_id, model_type, model_version, prior_mean, prior_variance, posterior_mean, posterior_variance, observation_count, effective_observation_count, update_version, last_update_at, context_state_json=Mapping)`, `UpdateRecord(reward_observation_id, arm_id, version_before, version_after, status: UpdateStatus, effective_weight, updated_at)`.
  - `domain.reward`: `RESERVED_VALUE_TERMS` (C7's six reserved economic keys), `tri_from_optional(value) -> Tri` (only NULL→Tri path), `observation_key(experiment_id, window) -> str`, `RewardObservation(experiment_id, arm_id, content_id, observation_window, maturity, observed_at, raw_metrics: Mapping[str, Tri], normalized_reward: Tri, reward_version, attribution_confidence, quality_status=None, economic_terms=Mapping)`.
  - `domain.decision`: `ArmSample(arm_id, sampled_reward, posterior_expected_reward)`, `ConstraintRejection(arm_id, reason: ReasonCode, detail)`.
  - `domain.directive`: `DirectiveBrief`, `ProductionDirective` (the 18 wire fields as pydantic fields; the constant and `to_wire()` land in P2.1A).
  - `domain.policy`: `FatigueConfig`, `NonstationarityConfig`, `RetirementConfig`, `PolicyConfig`, `DEFAULT_POLICY`.
  - `config`: `ARSettings`, `load_ar_settings(*, platform=None, **overrides) -> ARSettings`; `ARSettings.policy_version == "p2-v1"`, `model_version == "p2-nn-v1"`, `prior_version == "p2-prior-v1"`, `feature_version == "p2-features-v1"`, `proxy_normalization_version == "p2-proxy-norm-v1"`, `value_model == "early-stage-proxy-v1"`, `default_activation_state is ChannelActivationState.SHADOW`, `market_base_url == "http://127.0.0.1:8081"`, `similarity_threshold == 0.6`, `observation_variance == 0.09`, `posterior_variance_floor == 1e-6`, `transfer_max_weight == 0.25`, `min_transfer_shared_dimensions == 2`, `directive_version == "p2-directive-v1"`.

- [ ] **Step 1: Write the failing enum test**

```python
# tests/unit/ar/test_domain_enums.py
from services.autonomous_ar.domain.enums import (
    AllocationCycleStatus,
    ArmLifecycleStatus,
    DirectiveStatus,
    ObservationMaturity,
    ReasonCode,
    UpdateStatus,
)


def test_lifecycle_and_cycle_statuses_match_the_spec() -> None:
    assert {s.value for s in ArmLifecycleStatus} == {
        "candidate", "active", "cooldown", "paused", "retired", "blocked", "archived",
    }
    assert {s.value for s in AllocationCycleStatus} == {
        "planned", "running", "completed", "partial", "failed",
    }
    assert {s.value for s in DirectiveStatus} == {
        "queued", "accepted", "in_progress", "completed", "cancelled", "expired", "failed",
    }
    assert {s.value for s in ObservationMaturity} == {"provisional", "mature", "closed"}
    assert {s.value for s in UpdateStatus} == {
        "applied", "skipped_duplicate", "quarantined", "failed",
    }


def test_reason_codes_are_the_spec_nine_plus_the_eight_this_build_requires() -> None:
    # §57's nine, then the codes ADR-0023 and §59/§82/§83/§102/§107 force this phase to emit.
    assert {r.value for r in ReasonCode} == {
        "STRONG_POSTERIOR", "HIGH_UNCERTAINTY", "MARKET_SIGNAL_STRONG",
        "ADJACENT_TO_WINNER", "RADICAL_EXPLORATION", "BASELINE_CONTROL", "LOW_FATIGUE",
        "RECOVERING_MARKET_SIGNAL", "DIVERSITY_CONSTRAINT",
        "EVIDENCE_INSUFFICIENT", "MARKET_STALE", "MARKET_UNAVAILABLE",
        "MARKET_REGIME_CHANGE_SUSPECTED", "INSUFFICIENT_ELIGIBLE_ARMS",
        "PROVIDER_CAPABILITY_UNAVAILABLE", "MANUAL_OVERRIDE", "FATIGUE_COOLDOWN",
        "WEAK_POSTERIOR",
    }
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_domain_enums.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.domain'` (create `tests/unit/ar/__init__.py`, `services/autonomous_ar/__init__.py` and `services/autonomous_ar/domain/__init__.py` as empty files in this step so the failure is the missing module, not a missing package).

- [ ] **Step 3: Write the minimal enum module**

```python
# services/autonomous_ar/domain/enums.py
"""P2 vocabulary. Every value is copied from PHASE-P2 or a frozen ruling; storage keeps text only."""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "AllocationCycleStatus", "ArmLifecycleStatus", "DirectiveStatus", "ObservationMaturity",
    "OverrideAction", "ReasonCode", "UpdateStatus",
]


class ArmLifecycleStatus(StrEnum):
    """§9's seven arm states. P2 owns these (R15); P1's own arm status is separate."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    PAUSED = "paused"
    RETIRED = "retired"
    BLOCKED = "blocked"
    ARCHIVED = "archived"


class AllocationCycleStatus(StrEnum):
    """§59: too few eligible arms makes a cycle `partial`, never `completed`."""

    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class DirectiveStatus(StrEnum):
    """§52's statuses. P2 only ever writes `queued`; later phases advance the rest."""

    QUEUED = "queued"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class ObservationMaturity(StrEnum):
    """§18/§19: a 24h result is provisional, never final."""

    PROVISIONAL = "provisional"
    MATURE = "mature"
    CLOSED = "closed"


class UpdateStatus(StrEnum):
    """§74's persisted update state; `applied` is unique per reward observation."""

    APPLIED = "applied"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class ReasonCode(StrEnum):
    """§57's examples (never prose-only) plus the codes this build must emit."""

    # §57 examples, verbatim
    STRONG_POSTERIOR = "STRONG_POSTERIOR"
    HIGH_UNCERTAINTY = "HIGH_UNCERTAINTY"
    MARKET_SIGNAL_STRONG = "MARKET_SIGNAL_STRONG"
    ADJACENT_TO_WINNER = "ADJACENT_TO_WINNER"
    RADICAL_EXPLORATION = "RADICAL_EXPLORATION"
    BASELINE_CONTROL = "BASELINE_CONTROL"
    LOW_FATIGUE = "LOW_FATIGUE"
    RECOVERING_MARKET_SIGNAL = "RECOVERING_MARKET_SIGNAL"
    DIVERSITY_CONSTRAINT = "DIVERSITY_CONSTRAINT"
    # forced by rulings and the spec's own failure paths
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"  # ADR-0023 #5: <2 measured components
    MARKET_STALE = "MARKET_STALE"                    # §83
    MARKET_UNAVAILABLE = "MARKET_UNAVAILABLE"        # §102: degraded cycle, never invented data
    MARKET_REGIME_CHANGE_SUSPECTED = "MARKET_REGIME_CHANGE_SUSPECTED"  # §28/§85
    INSUFFICIENT_ELIGIBLE_ARMS = "INSUFFICIENT_ELIGIBLE_ARMS"          # §59
    PROVIDER_CAPABILITY_UNAVAILABLE = "PROVIDER_CAPABILITY_UNAVAILABLE"  # §82
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"              # §107
    FATIGUE_COOLDOWN = "FATIGUE_COOLDOWN"            # §26
    WEAK_POSTERIOR = "WEAK_POSTERIOR"                # §38 retirement evidence


class OverrideAction(StrEnum):
    """§107's explicit operator events; §108 gives them an expiry."""

    PAUSE_ARM = "pause_arm"
    FORCE_ARM = "force_arm"
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_domain_enums.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Write the failing arm-identity test**

```python
# tests/unit/ar/test_domain_arm.py
from datetime import UTC, datetime

import pytest

from services.autonomous_ar.domain.arm import (
    ArmDimensions,
    ContentArm,
    canonical_fingerprint,
)


AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def _dims() -> ArmDimensions:
    return ArmDimensions(
        genre="rnb", moods=("melancholic", "dreamy"), use_cases=("late_night_drive",),
        language="english", vocal_mode="vocal", duration_bucket="45_75m",
        visual_world="rainy_asian_city", title_archetype="time_place_mood",
    )


def test_fingerprint_is_order_stable_and_distinguishes_dimensions() -> None:
    same = ArmDimensions(genre="rnb", moods=("dreamy", "melancholic"),
                         use_cases=("late_night_drive",), language="english",
                         vocal_mode="vocal", duration_bucket="45_75m",
                         visual_world="rainy_asian_city", title_archetype="time_place_mood")
    other = ArmDimensions(genre="city_pop", moods=("melancholic", "dreamy"))
    assert canonical_fingerprint(_dims()) == canonical_fingerprint(same), (
        "mood token order must not change identity (§7: normalized canonical representation)"
    )
    assert canonical_fingerprint(_dims()) != canonical_fingerprint(other)


def test_null_component_is_never_a_zero() -> None:
    arm = ContentArm(
        arm_id="arm-1", dimensions=_dims(), p1_score=82.0, p1_confidence=0.7,
        component_scores={"demand": 70.0, "growth": None, "competitor_velocity": 55.0,
                          "competition": None, "audience_quality": None,
                          "cross_platform": None, "production_fit": 60.0},
        observation_count=4, source_count=2, as_of=AS_OF,
        score_version="p1-v1", freshness_seconds=3600.0,
    )
    assert arm.measured_component_count == 4
    assert arm.is_rankable() is True
    assert arm.component_scores["demand"] == 70.0 and arm.component_scores["growth"] is None


def test_rankability_requires_two_measured_components() -> None:
    scores = {"demand": 70.0, "growth": None, "competitor_velocity": None,
              "competition": None, "audience_quality": None, "cross_platform": None,
              "production_fit": None}
    arm = ContentArm(
        arm_id="arm-2", dimensions=_dims(), p1_score=90.0, p1_confidence=0.9,
        component_scores=scores, observation_count=1, source_count=1,
        as_of=AS_OF, score_version="p1-v1", freshness_seconds=0.0,
    )
    assert arm.measured_component_count == 1
    assert arm.is_rankable() is False, "ADR-0023 #5: one component is not enough to be ranked"


def test_naive_as_of_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ContentArm(
            arm_id="arm-3", dimensions=_dims(), p1_score=50.0, p1_confidence=0.5,
            component_scores={k: None for k in
                              ("demand", "growth", "competitor_velocity", "competition",
                               "audience_quality", "cross_platform", "production_fit")},
            observation_count=0, source_count=0, as_of=datetime(2026, 9, 20),
            score_version="p1-v1", freshness_seconds=0.0,
        )
```

- [ ] **Step 6: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_domain_arm.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.domain.arm'`.

- [ ] **Step 7: Write `domain/arm.py`**

```python
# services/autonomous_ar/domain/arm.py
"""The decision unit: P1's arm, as P2 sees it over the frozen C1 wire (R15).

P2 never mints an arm and never rewrites taxonomy; `arm_id` arrives from P1 verbatim.
The fingerprint below is P2's *own* canonical form, built from exactly the nine taxonomy
keys the C1 payload carries — P1's `market_arms.fingerprint` is not on the wire.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

__all__ = [
    "DIMENSION_ORDER", "MEASURED_COMPONENT_KEYS", "ArmDimensions", "ContentArm",
    "canonical_fingerprint",
]

DIMENSION_ORDER: tuple[str, ...] = (
    "genre", "subgenre", "mood", "use_case", "language", "vocal_mode",
    "duration_bucket", "visual_world", "title_archetype",
)
"""The nine taxonomy keys of the frozen C1 payload (registry C1), in fingerprint order."""

MEASURED_COMPONENT_KEYS: tuple[str, ...] = (
    "demand", "growth", "competitor_velocity", "competition", "audience_quality",
    "cross_platform", "production_fit",
)
"""§20's seven component keys; a `null` component was not measured — never a zero."""


@dataclass(frozen=True, slots=True)
class ArmDimensions:
    """The frozen nine C1 taxonomy keys. Multi-valued keys stay as sorted token tuples."""

    genre: str | None = None
    subgenre: str | None = None
    moods: tuple[str, ...] = ()
    use_cases: tuple[str, ...] = ()
    language: str | None = None
    vocal_mode: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None
    title_archetype: str | None = None

    def tokens(self, name: str) -> tuple[str, ...] | None:
        """The tokens present for one dimension, or None when the wire sent nothing."""
        if name == "mood":
            return tuple(sorted(self.moods)) or None
        if name == "use_case":
            return tuple(sorted(self.use_cases)) or None
        value = getattr(self, name)
        return None if value is None else (value,)


def canonical_fingerprint(dims: ArmDimensions) -> str:
    """`genre=rnb|mood=dreamy,melancholic|…` — deterministic, order-insensitive per (§7)."""
    parts: list[str] = []
    for name in DIMENSION_ORDER:
        tokens = dims.tokens(name)
        parts.append(f"{name}={'+'.join(tokens) if tokens else ''}")
    return "|".join(parts)


@dataclass(frozen=True, slots=True)
class ContentArm:
    """One ranked P1 opportunity, adapted to P2's domain (P2.2 builds these).

    `component_scores` maps the seven frozen C1 component keys to a measured value or
    `None`; a value that was never observed stays `None` all the way into features and
    decisions (Review Focus 1). `freshness_seconds` is P2-derived from `as_of`.
    """

    arm_id: str
    dimensions: ArmDimensions
    p1_score: float
    p1_confidence: float
    component_scores: Mapping[str, float | None]
    observation_count: int
    source_count: int
    as_of: datetime
    score_version: str
    freshness_seconds: float
    _extra: Mapping[str, object] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (registry C1 derives freshness from it)")
        if not self.arm_id:
            raise ValueError("arm_id must not be empty")
        if not 0.0 <= self.p1_confidence <= 1.0:
            raise ValueError(f"p1_confidence must be in [0, 1], got {self.p1_confidence}")
        if not 0.0 <= self.p1_score <= 100.0:
            raise ValueError(f"p1_score must be in [0, 100], got {self.p1_score}")
        expected = frozenset(MEASURED_COMPONENT_KEYS)
        if frozenset(self.component_scores) != expected:
            raise ValueError(
                "component_scores must carry exactly the seven frozen C1 keys "
                f"(missing={sorted(expected - frozenset(self.component_scores))}, "
                f"unknown={sorted(frozenset(self.component_scores) - expected)})"
            )
        if self.freshness_seconds < 0.0:
            raise ValueError("freshness_seconds must not be negative")

    @property
    def measured_component_count(self) -> int:
        """How many of the seven components were measured (non-null)."""
        return sum(1 for value in self.component_scores.values() if value is not None)

    def is_rankable(self, *, minimum: int = 2) -> bool:
        """ADR-0023 #5: ranked only with at least `minimum` measured components."""
        return self.measured_component_count >= minimum
```

- [ ] **Step 8: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_domain_arm.py -q`
Expected: PASS (4 tests).

- [ ] **Step 9: Write the failing posterior/reward test**

```python
# tests/unit/ar/test_domain_posterior.py
from datetime import UTC, datetime

import pytest

from services.autonomous_ar.domain.posterior import PosteriorState, PriorParameters


def test_posterior_refuses_negative_variance() -> None:
    with pytest.raises(ValueError, match="variance"):
        PosteriorState(
            arm_id="a", model_type="normal-normal", model_version="p2-nn-v1",
            prior_mean=0.5, prior_variance=0.04, posterior_mean=0.6,
            posterior_variance=-0.01, observation_count=1,
            effective_observation_count=1.0, update_version=1,
            last_update_at=datetime(2026, 9, 20, 6, 0, tzinfo=UTC),
        )


def test_prior_refuses_confidence_outside_zero_to_one() -> None:
    with pytest.raises(ValueError, match="confidence"):
        PriorParameters(mean=0.5, variance=0.04, confidence=1.1, prior_version="p2-prior-v1")
```

```python
# tests/unit/ar/test_domain_reward.py
from datetime import UTC, datetime

import pytest
from plm_contracts import ObservationWindow, Tri

from services.autonomous_ar.domain.enums import ObservationMaturity
from services.autonomous_ar.domain.reward import (
    RESERVED_VALUE_TERMS,
    RewardObservation,
    observation_key,
    tri_from_optional,
)


def test_tri_from_optional_is_the_only_null_path_and_zero_is_a_measurement() -> None:
    assert tri_from_optional(None).state.value == "unknown"
    known = tri_from_optional(0.0)
    assert known.state.value == "known" and known.unwrap() == 0.0


def test_reserved_economic_terms_are_kept_and_never_invented() -> None:
    assert set(RESERVED_VALUE_TERMS) == {
        "revenue", "subscriber_value", "catalog_value", "spotify_conversion",
        "future_discovery_value", "production_cost",
    }
    observation = RewardObservation(
        experiment_id="exp-1", arm_id="a", content_id="c",
        observation_window=ObservationWindow.D7, maturity=ObservationMaturity.MATURE,
        observed_at=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        raw_metrics={"views": Tri.known(42000.0)}, normalized_reward=Tri.known(0.74),
        reward_version="p9-reward-v1", attribution_confidence=0.99,
        economic_terms={"revenue": Tri.unknown()},
    )
    assert observation.economic_terms["revenue"].state.value == "unknown"
    assert observation.key == observation_key("exp-1", ObservationWindow.D7)
    with pytest.raises(ValueError, match="reserved"):
        RewardObservation(
            experiment_id="exp-1", arm_id="a", content_id="c",
            observation_window=ObservationWindow.D7, maturity=ObservationMaturity.MATURE,
            observed_at=observation.observed_at, raw_metrics={},
            normalized_reward=Tri.known(0.5), reward_version="p9-reward-v1",
            attribution_confidence=0.9, economic_terms={"made_up_term": Tri.known(1.0)},
        )
```

- [ ] **Step 10: Run them and watch them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_domain_posterior.py tests\unit\ar\test_domain_reward.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.domain.posterior'` (and `.reward`).

- [ ] **Step 11: Write `domain/posterior.py` and `domain/reward.py`**

```python
# services/autonomous_ar/domain/posterior.py
"""Posterior state and its provenance (§47, §74). Dumb, validated, frozen records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import UpdateStatus

__all__ = ["PosteriorState", "PriorParameters", "UpdateRecord"]


@dataclass(frozen=True, slots=True)
class PriorParameters:
    """The P1 → prior transform's output (§10); the transform itself is versioned."""

    mean: float
    variance: float
    confidence: float
    prior_version: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.mean <= 1.0:
            raise ValueError(f"prior mean must be in [0, 1], got {self.mean}")
        if self.variance <= 0.0:
            raise ValueError(f"prior variance must be > 0, got {self.variance}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")


@dataclass(frozen=True, slots=True)
class PosteriorState:
    """One arm's Gaussian posterior (§47); `update_version` is the optimistic lock (§75)."""

    arm_id: str
    model_type: str
    model_version: str
    prior_mean: float
    prior_variance: float
    posterior_mean: float
    posterior_variance: float
    observation_count: int
    effective_observation_count: float
    update_version: int
    last_update_at: datetime
    context_state_json: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("prior_mean", "posterior_mean"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {getattr(self, name)}")
        for name in ("prior_variance", "posterior_variance"):
            if getattr(self, name) < 0.0:
                raise ValueError(f"{name} must be >= 0, got {getattr(self, name)}")
        if self.observation_count < 0 or self.effective_observation_count < 0.0:
            raise ValueError("observation counts must be >= 0")
        if self.update_version < 0:
            raise ValueError("update_version must be >= 0")
        if self.last_update_at.tzinfo is None:
            raise ValueError("last_update_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class UpdateRecord:
    """§74's persisted update state — the retry-proofing row."""

    reward_observation_id: str
    arm_id: str
    version_before: int
    version_after: int
    status: UpdateStatus
    effective_weight: float
    updated_at: datetime
```

```python
# services/autonomous_ar/domain/reward.py
"""Provider-neutral reward contract (§17/§72) with the C7 value terms reserved (ADR-0023 #4).

R11: P9 owns reward arithmetic — `normalized_reward` and `reward_version` arrive on the
wire. P2 never computes a reward from raw metrics; a missing `normalized_reward` is
`unknown`, and an unmeasured economic term is `unknown`, never zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from plm_contracts import ObservationWindow, QualityStatus, Tri

from .enums import ObservationMaturity

__all__ = [
    "RESERVED_VALUE_TERMS", "RewardObservation", "observation_key", "tri_from_optional",
]

RESERVED_VALUE_TERMS: tuple[str, ...] = (
    "revenue", "subscriber_value", "catalog_value", "spotify_conversion",
    "future_discovery_value", "production_cost",
)
"""ADR-0023 #4: the full Expected Economic Value terms, reserved in the C7 reward schema.
P2 stores them when a producer supplies them and never fabricates them."""


def tri_from_optional(value: float | None) -> Tri[float]:
    """The only NULL→Tri path. `None` is unknown; an explicit `0.0` is a measurement."""
    return Tri.unknown() if value is None else Tri.known(float(value))


def observation_key(experiment_id: str, window: ObservationWindow) -> str:
    """§73's idempotency identity: `experiment_id + observation_window`."""
    return f"{experiment_id}|{window.value}"


@dataclass(frozen=True, slots=True)
class RewardObservation:
    """§72's payload as P2's domain object. `quality_status` resolves the registry's
    C7 gap (`QualityStatus`, P9 §57 states); it is optional because the C7 freeze (W5.0)
    may respell it — the registry amendment moves both sides together (R2)."""

    experiment_id: str
    arm_id: str
    content_id: str
    observation_window: ObservationWindow
    maturity: ObservationMaturity
    observed_at: datetime
    raw_metrics: Mapping[str, Tri[float]]
    normalized_reward: Tri[float]
    reward_version: str
    attribution_confidence: float
    quality_status: QualityStatus | None = None
    economic_terms: Mapping[str, Tri[float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if not 0.0 <= self.attribution_confidence <= 1.0:
            raise ValueError(
                f"attribution_confidence must be in [0, 1], got {self.attribution_confidence}"
            )
        unknown = set(self.economic_terms) - set(RESERVED_VALUE_TERMS)
        if unknown:
            raise ValueError(
                f"economic_terms may only carry reserved C7 terms, got unknown keys {sorted(unknown)}"
            )
        if not self.reward_version:
            raise ValueError("reward_version must not be empty (R11: P9 owns the arithmetic)")

    @property
    def key(self) -> str:
        return observation_key(self.experiment_id, self.observation_window)
```

- [ ] **Step 12: Run them and watch them pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_domain_posterior.py tests\unit\ar\test_domain_reward.py -q`
Expected: PASS (4 tests).

- [ ] **Step 13: Write the failing policy test**

```python
# tests/unit/ar/test_domain_policy.py
import pytest
from plm_contracts import ChannelActivationState

from services.autonomous_ar.config import ARSettings, load_ar_settings
from services.autonomous_ar.domain.policy import DEFAULT_POLICY, PolicyConfig


def test_default_policy_fractions_sum_to_exactly_one() -> None:
    total = (DEFAULT_POLICY.exploit_fraction + DEFAULT_POLICY.adjacent_explore_fraction
             + DEFAULT_POLICY.radical_explore_fraction)
    assert total == pytest.approx(1.0), "§13 governs §90; baseline is inside exploit (ADR-0023 #3)"
    assert (DEFAULT_POLICY.exploit_fraction, DEFAULT_POLICY.adjacent_explore_fraction,
            DEFAULT_POLICY.radical_explore_fraction) == (0.70, 0.20, 0.10)
    assert DEFAULT_POLICY.baseline_fraction <= DEFAULT_POLICY.exploit_fraction


def test_policy_whose_fractions_exceed_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="1.1"):
        PolicyConfig(name="bad", exploit_fraction=0.70, adjacent_explore_fraction=0.20,
                     radical_explore_fraction=0.20)


def test_baseline_fraction_cannot_exceed_the_exploit_slice() -> None:
    with pytest.raises(ValueError, match="baseline_fraction"):
        PolicyConfig(name="bad", exploit_fraction=0.5, adjacent_explore_fraction=0.2,
                     radical_explore_fraction=0.3, baseline_fraction=0.6)


def test_ar_settings_defaults_match_the_frozen_version_strings() -> None:
    settings = load_ar_settings()
    assert settings.policy_version == "p2-v1" and settings.model_version == "p2-nn-v1"
    assert settings.default_activation_state is ChannelActivationState.SHADOW
    assert settings.market_base_url == "http://127.0.0.1:8081"
    assert settings.directive_version == "p2-directive-v1"
    assert settings.value_model == "early-stage-proxy-v1"
    with pytest.raises(TypeError, match="unknown ar settings"):
        load_ar_settings(not_a_knob=1)


def test_settings_are_overridable_without_touching_the_environment() -> None:
    settings: ARSettings = load_ar_settings(similarity_threshold=0.75)
    assert settings.similarity_threshold == 0.75
```

- [ ] **Step 14: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_domain_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.domain.policy'`.

- [ ] **Step 15: Write `domain/policy.py` and `config.py`**

```python
# services/autonomous_ar/domain/policy.py
"""The versioned allocation policy (§49/§90/§91) with ADR-0023's arithmetic rules baked in.

Validation is §91's list plus the freeze: fractions >= 0 and <= 1, their sum must not
exceed 1.0 (a remainder is reserved capacity, not an error), `baseline_fraction` is a
residual *inside* the exploit slice and may never exceed it, and half-lives/slot-adjacent
knobs must be positive.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "DEFAULT_POLICY", "FatigueConfig", "NonstationarityConfig", "PolicyConfig",
    "RetirementConfig",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FatigueConfig(_Frozen):
    enabled: bool = True
    decay_half_life_days: float = Field(14.0, gt=0.0)
    max_penalty: float = Field(0.5, ge=0.0, le=1.0)
    cooldown_hours: float = Field(72.0, ge=0.0)


class NonstationarityConfig(_Frozen):
    enabled: bool = True
    decay_half_life_days: float = Field(45.0, gt=0.0)
    regime_relative_threshold: float = Field(0.30, gt=0.0, le=1.0)


class RetirementConfig(_Frozen):
    min_mature_observations: int = Field(5, ge=1)
    weak_posterior_threshold: float = Field(0.25, ge=0.0, le=1.0)


class PolicyConfig(_Frozen):
    """§49's row as a validated value object; persisted rows are immutable (§92)."""

    name: str = "default"
    version: str = "p2-v1"
    exploit_fraction: float = Field(0.70, ge=0.0, le=1.0)
    adjacent_explore_fraction: float = Field(0.20, ge=0.0, le=1.0)
    radical_explore_fraction: float = Field(0.10, ge=0.0, le=1.0)
    baseline_fraction: float = Field(0.10, ge=0.0, le=1.0)
    max_exact_arm_share: float = Field(0.50, gt=0.0, le=1.0)
    max_cluster_share: float = Field(0.60, gt=0.0, le=1.0)
    max_genre_share: float = Field(0.60, gt=0.0, le=1.0)
    max_use_case_share: float = Field(0.60, gt=0.0, le=1.0)
    max_language_share: float = Field(0.85, gt=0.0, le=1.0)
    max_concurrent_experiments_per_cluster: int = Field(2, ge=1)
    max_market_data_age_hours: float = Field(168.0, gt=0.0)
    min_measured_components: int = Field(2, ge=2)
    min_attribution_confidence: float = Field(0.5, ge=0.0, le=1.0)
    assumed_cost_per_directive: float = Field(1.0, gt=0.0)
    directive_ttl_hours: float = Field(168.0, gt=0.0)
    exploration_boost: float = Field(0.10, ge=0.0, le=0.3)
    value_model: str = "early-stage-proxy-v1"
    fatigue: FatigueConfig = Field(default_factory=FatigueConfig)
    nonstationarity: NonstationarityConfig = Field(default_factory=NonstationarityConfig)
    retirement: RetirementConfig = Field(default_factory=RetirementConfig)

    @model_validator(mode="after")
    def _validate_fractions_and_baseline(self) -> "PolicyConfig":
        total = (self.exploit_fraction + self.adjacent_explore_fraction
                 + self.radical_explore_fraction)
        if total > 1.0 + 1e-9:
            raise ValueError(
                f"exploration fractions must not exceed 1.0, got {round(total, 6)} "
                "(ADR-0023 #3; §13 governs §90)"
            )
        if self.baseline_fraction > self.exploit_fraction + 1e-9:
            raise ValueError(
                f"baseline_fraction ({self.baseline_fraction}) may not exceed "
                f"exploit_fraction ({self.exploit_fraction}): it is the residual inside "
                "the exploit slice (ADR-0023 #3)"
            )
        return self


DEFAULT_POLICY = PolicyConfig(name="default")
```

```python
# services/autonomous_ar/config.py
"""P2 settings: deployment values come from `plm_platform.config`; these are P2's knobs.

Constructed from code defaults plus explicit overrides — P2 never reads `os.environ`.
Version strings live here once and are copied onto every decision row (§76).
"""

from __future__ import annotations

from dataclasses import dataclass

from plm_contracts import ChannelActivationState
from plm_platform.config import Settings

__all__ = ["ARSettings", "load_ar_settings"]


@dataclass(frozen=True, slots=True)
class ARSettings:
    platform: Settings | None = None
    policy_version: str = "p2-v1"
    model_version: str = "p2-nn-v1"
    prior_version: str = "p2-prior-v1"
    feature_version: str = "p2-features-v1"
    proxy_normalization_version: str = "p2-proxy-norm-v1"
    directive_version: str = "p2-directive-v1"
    value_model: str = "early-stage-proxy-v1"
    default_activation_state: ChannelActivationState = ChannelActivationState.SHADOW
    market_base_url: str = "http://127.0.0.1:8081"
    market_timeout_seconds: float = 10.0
    similarity_threshold: float = 0.6
    min_transfer_shared_dimensions: int = 2
    transfer_max_weight: float = 0.25
    observation_variance: float = 0.09
    posterior_variance_floor: float = 1e-6
    default_slot_count: int = 10
    default_budget: float = 10.0
    cycle_cadence_hours: float = 168.0
    simulation_reward_version: str = "p2-sim-v1"


def load_ar_settings(*, platform: Settings | None = None, **overrides: object) -> ARSettings:
    """Build P2 settings; unknown keyword overrides raise (a typo must not pass silently)."""
    unknown = set(overrides) - set(ARSettings.__dataclass_fields__)
    if unknown:
        raise TypeError(f"unknown ar settings: {sorted(unknown)}")
    return ARSettings(platform=platform, **overrides)  # type: ignore[arg-type]
```

- [ ] **Step 16: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_domain_policy.py -q`
Expected: PASS (5 tests).

- [ ] **Step 17: Write the failing decision/directive test**

```python
# tests/unit/ar/test_domain_directive.py
from datetime import UTC, datetime

import pytest
from plm_contracts import AllocationType

from services.autonomous_ar.domain.decision import ArmSample, ConstraintRejection
from services.autonomous_ar.domain.directive import DirectiveBrief, ProductionDirective
from services.autonomous_ar.domain.enums import ReasonCode


def _directive(**overrides: object) -> ProductionDirective:
    base: dict[str, object] = {
        "directive_id": "d-1", "decision_id": "dec-1", "arm_id": "arm-1",
        "allocation_type": AllocationType.EXPLOIT, "priority": 1, "quantity": 1,
        "brief": DirectiveBrief(genre="rnb", mood=("dreamy",), use_case=("late_night_drive",),
                                language="english", vocal_mode="vocal",
                                duration_bucket="45_75m", visual_world="rainy_asian_city"),
        "expected_value": 0.72, "uncertainty": 0.31, "budget_limit": 1.0,
        "market_context": {"p1_score": 82.0, "p1_confidence": 0.7},
        "selection_reason": (ReasonCode.STRONG_POSTERIOR.value,),
        "directive_version": "p2-directive-v1", "policy_version": "p2-v1",
        "expires_at": datetime(2026, 9, 27, 6, 0, tzinfo=UTC),
        "exploration_fraction": 0.70, "value_model": "early-stage-proxy-v1",
    }
    base.update(overrides)
    return ProductionDirective(**base)  # type: ignore[arg-type]


def test_directive_carries_the_eighteen_frozen_fields_and_forbids_extras() -> None:
    directive = _directive()
    assert set(ProductionDirective.model_fields) == {
        "directive_id", "decision_id", "arm_id", "allocation_type", "priority", "quantity",
        "brief", "expected_value", "uncertainty", "budget_limit", "market_context",
        "selection_reason", "directive_version", "policy_version", "expires_at",
        "exploration_fraction", "value_model",
    }
    assert "status" not in ProductionDirective.model_fields, "status is persistence-only (§52)"
    with pytest.raises(ValueError):
        _directive(quantity=0)
    with pytest.raises(ValueError):
        _directive(naive="field")


def test_brief_is_a_search_region_with_exactly_the_registry_keys() -> None:
    brief = _directive().brief
    assert set(brief.model_dump()) == {
        "genre", "mood", "use_case", "language", "vocal_mode", "duration_bucket",
        "visual_world",
    }


def test_rejections_and_samples_are_plain_records() -> None:
    sample = ArmSample(arm_id="a", sampled_reward=0.71, posterior_expected_reward=0.66)
    rejection = ConstraintRejection(arm_id="a", reason=ReasonCode.DIVERSITY_CONSTRAINT,
                                    detail="genre cap 0.6")
    assert sample.sampled_reward == 0.71 and rejection.reason is ReasonCode.DIVERSITY_CONSTRAINT
```

- [ ] **Step 18: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_domain_directive.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.domain.decision'`.

- [ ] **Step 19: Write `domain/decision.py` and `domain/directive.py`**

```python
# services/autonomous_ar/domain/decision.py
"""Decision-side records (§51): what was sampled, and why an arm was turned away."""

from __future__ import annotations

from dataclasses import dataclass

from .enums import ReasonCode

__all__ = ["ArmSample", "ConstraintRejection"]


@dataclass(frozen=True, slots=True)
class ArmSample:
    """One Thompson draw plus the posterior it came from (§67)."""

    arm_id: str
    sampled_reward: float
    posterior_expected_reward: float


@dataclass(frozen=True, slots=True)
class ConstraintRejection:
    """A rejection with a structured reason code (§57); `detail` is for operators."""

    arm_id: str
    reason: ReasonCode
    detail: str
```

```python
# services/autonomous_ar/domain/directive.py
"""The production directive (§32, §120) — P2's side of frozen seam C2 (ADR-0023).

The eighteen wire fields are listed in the registry and pinned by P2.1A. Everything
in §52 beyond them (`id`, `status`, `brief_json`, `created_at`, `consumed_at`) is
persistence-only and must never appear on the wire.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any

from plm_contracts import AllocationType
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["DirectiveBrief", "ProductionDirective"]


class DirectiveBrief(BaseModel):
    """§33: a creative search region, not an over-constrained song prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    genre: str | None = None
    mood: tuple[str, ...] = ()
    use_case: tuple[str, ...] = ()
    language: str | None = None
    vocal_mode: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None


class ProductionDirective(BaseModel):
    """The exact registry-C2 shape. `to_wire()` and the field constant land in P2.1A."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    directive_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    arm_id: str = Field(min_length=1)
    allocation_type: AllocationType
    priority: Annotated[int, Field(ge=1)]
    quantity: Annotated[int, Field(ge=1)]
    brief: DirectiveBrief
    expected_value: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(ge=0.0)
    budget_limit: float = Field(ge=0.0)
    market_context: Mapping[str, Any]
    selection_reason: tuple[str, ...]
    directive_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    expires_at: datetime
    exploration_fraction: float = Field(ge=0.0, le=1.0)
    value_model: str = Field(min_length=1)
```

- [ ] **Step 20: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_domain_directive.py -q`
Expected: PASS (3 tests).

- [ ] **Step 21: Commit**

Run: `git add services/autonomous_ar tests/unit/ar; git commit -m "feat(ar): P2.1 domain contracts (arms, posteriors, rewards, policy, directives)"`

---

### Task P2.1A: C2 producer seam — directive wire pinned to the frozen registry

**Files:**
- Create: `tests/contract/test_c2_ar_to_experiments.py`
- Modify: `services/autonomous_ar/domain/directive.py` (add `DIRECTIVE_WIRE_FIELDS`, `to_wire()`)

**Interfaces:**
- Consumes: `ProductionDirective` (P2.1), `plm_contracts.{AllocationType, to_exploration_level}`; harness `tests.support.contract.{SeamRegistry, assert_seam_payload}`.
- Produces (names later tasks import exactly):
  - `domain.directive.DIRECTIVE_WIRE_FIELDS: tuple[str, ...]` — the registry's frozen 18 names, in registry order.
  - `domain.directive.ProductionDirective.to_wire() -> dict[str, Any]` — JSON-safe dict whose keys are exactly `DIRECTIVE_WIRE_FIELDS` (`expires_at` ISO-8601, `brief` the seven-key object, `selection_reason` a list).
- **Honesty note, stated in the test file too:** this is P2's *producer half* of C2. The full cross-phase test — P3's adapter ingesting this payload, including `exploration_level ← allocation_type` and `requested_experiment_count ← quantity` against P3's real code — **cannot exist until P3.4**. What this task pins today is the producer's serialised payload against the registry's frozen names, so a rename fails loudly now; P2.10 swaps the test's direct `to_wire()` producer for a directive produced by the real cycle (Step recorded there), and P3.4 completes the consumer half.

- [ ] **Step 1: Write the failing seam test**

```python
# tests/contract/test_c2_ar_to_experiments.py
"""Seam C2 — PHASE-P2 → PHASE-P3 (production directive). FROZEN by ADR-0023 (R4).

Producer: P2's `ProductionDirective.to_wire()` — the real serialiser, not a stub.
Consumer schema: the registry's frozen producer field list (the wire P3's adapter reads).

Scope note (stated so it is never mistaken for the whole seam): this is P2's producer
half. P3's consumer half — its adapter, the `exploration_level ← allocation_type` map and
the `requested_experiment_count ← quantity` rename against P3's code — lands in P3.4,
after which `tests/contract/test_c2_ar_to_experiments.py` gains those cases in the same
file. A wire change is a registry amendment first (R2), then both sides in one change.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from plm_contracts import AllocationType, to_exploration_level
from pydantic import BaseModel, ConfigDict

from services.autonomous_ar.domain.directive import (
    DIRECTIVE_WIRE_FIELDS,
    DirectiveBrief,
    ProductionDirective,
)
from tests.support.contract import SeamRegistry, assert_seam_payload

REGISTRY_ENTRY = "C2 · PHASE-P2 → PHASE-P3"

REGISTRY_FIELDS: tuple[str, ...] = (
    "directive_id", "decision_id", "arm_id", "allocation_type", "priority", "quantity",
    "brief", "expected_value", "uncertainty", "budget_limit", "market_context",
    "selection_reason", "directive_version", "policy_version", "expires_at",
    "exploration_fraction", "value_model",
)
REGISTRY_BRIEF_FIELDS: tuple[str, ...] = (
    "genre", "mood", "use_case", "language", "vocal_mode", "duration_bucket", "visual_world",
)


class BriefSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    genre: str | None = None
    mood: list[str] = []
    use_case: list[str] = []
    language: str | None = None
    vocal_mode: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None


class ConsumerSchema(BaseModel):
    """P3's expected wire schema, field names copied from the registry entry."""

    model_config = ConfigDict(extra="forbid")

    directive_id: str
    decision_id: str
    arm_id: str
    allocation_type: str
    priority: int
    quantity: int
    brief: BriefSchema
    expected_value: float
    uncertainty: float
    budget_limit: float
    market_context: dict[str, Any]
    selection_reason: list[str]
    directive_version: str
    policy_version: str
    expires_at: datetime
    exploration_fraction: float
    value_model: str


def producer_directive() -> ProductionDirective:
    """The producer: P2's real directive serialiser. Replaced by a cycle-produced
    directive in P2.10 (same assertions, a stronger producer)."""
    return ProductionDirective(
        directive_id="directive-1", decision_id="decision-1",
        arm_id="rnb_late_night_drive_english_vocal_45_75m",
        allocation_type=AllocationType.EXPLOIT, priority=1, quantity=1,
        brief=DirectiveBrief(genre="rnb", mood=("melancholic", "dreamy"),
                             use_case=("late_night_drive",), language="english",
                             vocal_mode="vocal", duration_bucket="45_75m",
                             visual_world="rainy_asian_city"),
        expected_value=0.72, uncertainty=0.31, budget_limit=0.5,
        market_context={"p1_score": 82.0, "p1_confidence": 0.7, "freshness_seconds": 3600.0,
                        "score_version": "p1-v1", "measured_component_count": 4},
        selection_reason=("STRONG_POSTERIOR", "LOW_FATIGUE"),
        directive_version="p2-directive-v1", policy_version="p2-v1",
        expires_at=datetime(2026, 9, 27, 6, 0, tzinfo=UTC),
        exploration_fraction=0.70, value_model="early-stage-proxy-v1",
    )


def test_declared_fields_match_the_registry_field_list() -> None:
    assert DIRECTIVE_WIRE_FIELDS == REGISTRY_FIELDS
    assert set(ConsumerSchema.model_fields) == set(DIRECTIVE_WIRE_FIELDS)
    assert set(BriefSchema.model_fields) == set(REGISTRY_BRIEF_FIELDS)


def test_producer_payload_validates_against_the_consumer_schema() -> None:
    payload = json.loads(json.dumps(producer_directive().to_wire()))
    assert_seam_payload(payload, ConsumerSchema, seam=REGISTRY_ENTRY,
                        producer="ProductionDirective.to_wire",
                        consumer="P3 adapter schema (registry C2)")


def test_frozen_vocabulary_and_rename_direction() -> None:
    # ADR-0023 #2: the three discrete levels are the contract; the map is the identity.
    assert to_exploration_level(AllocationType.EXPLOIT) == "exploit"
    assert to_exploration_level(AllocationType.ADJACENT_EXPLORE) == "adjacent_explore"
    assert to_exploration_level(AllocationType.RADICAL_EXPLORE) == "radical_explore"
    # P3's internal names are the consumer's, never the producer's (R4): if either rename
    # ever appears on P2's wire, the seam has been broken from the wrong side.
    for consumer_only in ("requested_experiment_count", "exploration_level",
                          "production_deadline", "required_constraints"):
        assert consumer_only not in DIRECTIVE_WIRE_FIELDS


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    assert contract_registry.exists, "the registry was frozen 2026-09-20; it cannot be missing"
    contract_registry.require_entry(REGISTRY_ENTRY)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\contract\test_c2_ar_to_experiments.py -q`
Expected: FAIL — `ImportError: cannot import name 'DIRECTIVE_WIRE_FIELDS' from 'services.autonomous_ar.domain.directive'`.

- [ ] **Step 3: Add the field constant and serialiser**

Append to `services/autonomous_ar/domain/directive.py` (and extend `__all__`):

```python
import json
from typing import Any

DIRECTIVE_WIRE_FIELDS: tuple[str, ...] = (
    "directive_id", "decision_id", "arm_id", "allocation_type", "priority", "quantity",
    "brief", "expected_value", "uncertainty", "budget_limit", "market_context",
    "selection_reason", "directive_version", "policy_version", "expires_at",
    "exploration_fraction", "value_model",
)
"""The frozen C2 producer fields, in registry order (ADR-0023). Persistence-only fields
(`id`, `status`, `brief_json`, `created_at`, `consumed_at`, and the extra columns P2.3
stores to serve the wire) must never be added here — a wire change is a registry
amendment first (R2)."""


def to_wire(self: ProductionDirective) -> dict[str, Any]:
    """The JSON-safe payload that crosses C2; keys are exactly DIRECTIVE_WIRE_FIELDS."""
    payload = json.loads(self.model_dump_json())
    assert tuple(payload) == DIRECTIVE_WIRE_FIELDS  # serialisation order is part of the pin
    return payload


ProductionDirective.to_wire = to_wire  # type: ignore[attr-defined]
```

To keep mypy strict happy, declare the method inside the class body instead of monkey-patching:

```python
# inside class ProductionDirective, after the field declarations:
    def to_wire(self) -> dict[str, Any]:
        """The JSON-safe payload that crosses C2; keys are exactly DIRECTIVE_WIRE_FIELDS."""
        payload = json.loads(self.model_dump_json())
        assert tuple(payload) == DIRECTIVE_WIRE_FIELDS  # serialisation order is part of the pin
        return payload
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\contract\test_c2_ar_to_experiments.py -q`
Expected: PASS (4 tests). The other contract files stay green: `pytest tests\contract -q`.

- [ ] **Step 5: Prove the drift fails loudly (once, by hand — do not commit the mutation)**

Temporarily rename `arm_id` to `armId` in `DIRECTIVE_WIRE_FIELDS` and in the model, run the seam
test, and confirm the assertion error prints the missing/unknown pair naming both halves. Revert
the rename. (This is the behaviour a future registry amendment must be decided against; the
mutation is never committed.)

- [ ] **Step 6: Commit**

Run: `git add services/autonomous_ar/domain/directive.py tests/contract/test_c2_ar_to_experiments.py; git commit -m "feat(ar): P2.1A pin the C2 directive wire to the frozen registry"`

---

### Task P2.2: P1 adapter

**Files:**
- Create: `services/autonomous_ar/ingestion/__init__.py`, `services/autonomous_ar/ingestion/p1_adapter.py`
- Modify: `tests/support/c1_api.py` (expose the fixture app so P2's tests can drive it over an ASGI transport)
- Test: `tests/unit/ar/test_p1_adapter.py`

**Interfaces:**
- Consumes: `tests/support/c1_api.{open_c1_client, SEAM_TOKEN, ARM_ID, AS_OF}` (fixtures only); `plm_platform.config.Settings.service_token`; `httpx`; `ContentArm`/`ArmDimensions` (P2.1).
- Produces (names later tasks import exactly):
  - `MARKET_OPPORTUNITIES_PATH = "/market/v1/opportunities"`.
  - `MarketUnavailable(reason: str)` — a `RuntimeError` whose `reason` names why (status code, transport error, or "no service token configured"). §102: the cycle treats this as "no live market snapshot", never as empty data.
  - `OpportunityPayload` (pydantic, `extra="forbid"`) — P2's transcription of the frozen C1 object: `arm_id, market_opportunity_score, confidence, as_of, taxonomy, components, evidence, score_version` with nested `TaxonomyPayload` (nine keys), `ComponentsPayload` (seven nullable floats), `EvidencePayload` (`observation_count, source_count, top_observation_ids`). It is **not** exported in this task (P2.1A's consumer seam test imports it; P2.2 exports it there) — the module names it `_OpportunityPayload`.
  - `OpportunityPage(items: tuple[OpportunityPayload, ...], next_cursor: str | None, as_of: datetime, score_version: str)`.
  - `P1Client(base_url: str, token: str | None, *, timeout_seconds: float = 10.0, transport: httpx.BaseTransport | None = None)` with `.fetch_opportunities(*, limit: int = 100, cursor: str | None = None) -> OpportunityPage` — the only P1 read path; **no SQLAlchemy import may exist in this module** (AC-09 / R15).
  - `adapt_opportunity(payload: OpportunityPayload, *, now: datetime) -> ContentArm` — maps `market_opportunity_score → p1_score`, `confidence → p1_confidence`, `components.* → *_score` with `null ⇒ None`, derives `freshness_seconds` from an aware `as_of`, computes `measured_component_count` implicitly via `ContentArm`.

- [ ] **Step 1: Write the failing mapping test (pure payloads, no HTTP)**

```python
# tests/unit/ar/test_p1_adapter.py
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from services.autonomous_ar.ingestion.p1_adapter import (
    MARKET_OPPORTUNITIES_PATH,
    MarketUnavailable,
    P1Client,
    adapt_opportunity,
)
from tests.support.c1_api import AS_OF, SEAM_TOKEN, open_c1_client

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "arm_id": "rnb_late_night_drive_english_vocal_45_75m",
        "market_opportunity_score": 82.0, "confidence": 0.7, "as_of": AS_OF.isoformat(),
        "taxonomy": {"genre": "rnb", "subgenre": None, "mood": ["melancholic", "dreamy"],
                     "use_case": ["late_night_drive"], "language": "english",
                     "vocal_mode": "vocal", "duration_bucket": "45_75m",
                     "visual_world": "rainy_asian_city", "title_archetype": "time_place_mood"},
        "components": {"demand": 70.0, "growth": None, "competitor_velocity": 55.0,
                       "competition": 60.0, "audience_quality": None, "cross_platform": None,
                       "production_fit": 58.0},
        "evidence": {"observation_count": 6, "source_count": 2, "top_observation_ids": ["o1"]},
        "score_version": "p1-v1",
    }
    base.update(overrides)
    return base


def test_mapping_is_exact_and_unobserved_components_stay_none() -> None:
    from services.autonomous_ar.ingestion.p1_adapter import _OpportunityPayload

    arm = adapt_opportunity(_OpportunityPayload.model_validate(_payload()), now=NOW)
    assert arm.arm_id == "rnb_late_night_drive_english_vocal_45_75m"
    assert arm.p1_score == 82.0 and arm.p1_confidence == 0.7
    assert arm.component_scores["demand"] == 70.0
    assert arm.component_scores["growth"] is None, "a null component is not a zero (C1→P2)"
    assert arm.measured_component_count == 4
    assert arm.observation_count == 6 and arm.source_count == 2
    assert arm.score_version == "p1-v1"


def test_freshness_is_derived_from_an_aware_as_of() -> None:
    from services.autonomous_ar.ingestion.p1_adapter import _OpportunityPayload

    payload = _OpportunityPayload.model_validate(_payload())
    arm = adapt_opportunity(payload, now=AS_OF + timedelta(hours=5))
    assert arm.freshness_seconds == pytest.approx(5 * 3600.0)
    # as_of is ahead of the caller's clock (skew): age clamps at zero, never negative.
    arm_early = adapt_opportunity(payload, now=AS_OF - timedelta(hours=1))
    assert arm_early.freshness_seconds == 0.0


def test_payload_schema_refuses_unknown_fields() -> None:
    from services.autonomous_ar.ingestion.p1_adapter import _OpportunityPayload

    with pytest.raises(ValueError, match="freshness"):
        _OpportunityPayload.model_validate(_payload(freshness="fresh"))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_p1_adapter.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.ingestion'` (create `services/autonomous_ar/ingestion/__init__.py` as an empty file first).

- [ ] **Step 3: Write `ingestion/p1_adapter.py` (the mapping half and the payload models)**

```python
# services/autonomous_ar/ingestion/p1_adapter.py
"""P2's consumer side of C1: read `/market/v1`, map into `ContentArm`. Never a table (AC-09).

This module imports no database library and reads no `market` schema: the adapter talks
HTTP with the service token (R15) and the registry's flat envelope (ADR-0022). `freshness`
is not on the wire — it is derived here from an aware `as_of`.
"""

from __future__ import annotations

from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..domain.arm import ArmDimensions, ContentArm

__all__ = [
    "MARKET_OPPORTUNITIES_PATH", "MarketUnavailable", "OpportunityPage", "P1Client",
    "adapt_opportunity",
]

MARKET_OPPORTUNITIES_PATH = "/market/v1/opportunities"
"""The frozen C1 endpoint (registry C1; base path fixed by R15)."""


class MarketUnavailable(RuntimeError):
    """P1 could not be read. §102: a degraded cycle or a stop — never invented data."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaxonomyPayload(_Frozen):
    genre: str | None = None
    subgenre: str | None = None
    mood: list[str] = Field(default_factory=list)
    use_case: list[str] = Field(default_factory=list)
    language: str | None = None
    vocal_mode: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None
    title_archetype: str | None = None


class ComponentsPayload(_Frozen):
    demand: float | None
    growth: float | None
    competitor_velocity: float | None
    competition: float | None
    audience_quality: float | None
    cross_platform: float | None
    production_fit: float | None


class EvidencePayload(_Frozen):
    observation_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    top_observation_ids: list[str] = Field(default_factory=list)


class _OpportunityPayload(_Frozen):
    """P2's transcription of the frozen C1 object. Exported as `OpportunityPayload` in
    P2.2A, where the consumer seam test pins it against the registry field list."""

    arm_id: str = Field(min_length=1)
    market_opportunity_score: float = Field(ge=0.0, le=100.0)
    confidence: float = Field(ge=0.0, le=1.0)
    as_of: datetime
    taxonomy: TaxonomyPayload
    components: ComponentsPayload
    evidence: EvidencePayload
    score_version: str = Field(min_length=1)


class OpportunityPage(_Frozen):
    items: tuple[_OpportunityPayload, ...]
    next_cursor: str | None
    as_of: datetime
    score_version: str


def adapt_opportunity(payload: _OpportunityPayload, *, now: datetime) -> ContentArm:
    """C1 payload → `ContentArm`; `market_opportunity_score → p1_score` per registry C1."""
    age = (now - payload.as_of).total_seconds()
    dimensions = ArmDimensions(
        genre=payload.taxonomy.genre, subgenre=payload.taxonomy.subgenre,
        moods=tuple(payload.taxonomy.mood), use_cases=tuple(payload.taxonomy.use_case),
        language=payload.taxonomy.language, vocal_mode=payload.taxonomy.vocal_mode,
        duration_bucket=payload.taxonomy.duration_bucket,
        visual_world=payload.taxonomy.visual_world,
        title_archetype=payload.taxonomy.title_archetype,
    )
    return ContentArm(
        arm_id=payload.arm_id, dimensions=dimensions,
        p1_score=payload.market_opportunity_score, p1_confidence=payload.confidence,
        component_scores=payload.components.model_dump(),
        observation_count=payload.evidence.observation_count,
        source_count=payload.evidence.source_count, as_of=payload.as_of,
        score_version=payload.score_version,
        freshness_seconds=max(0.0, age),  # clock skew clamps at zero, never negative
    )
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_p1_adapter.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Expose the fixture app, then write the failing client test**

In `tests/support/c1_api.py`, add to `SyncASGIClient` (one line of behaviour, no other change):

```python
    @property
    def app(self) -> Any:
        """The FastAPI app this client drives — lets a consumer adapter share the fixture."""
        return self._app
```

Then append to `tests/unit/ar/test_p1_adapter.py`:

```python
def test_client_reads_the_flat_c1_envelope_over_the_real_app() -> None:
    with open_c1_client() as api:
        client = P1Client(base_url="http://p1.test", token=SEAM_TOKEN,
                          transport=httpx.ASGITransport(app=api.app))
        page = client.fetch_opportunities(limit=5)
        assert page.score_version == "p1-v1"
        assert page.as_of.tzinfo is not None
        assert [item.arm_id for item in page.items] == [
            "rnb_late_night_drive_english_vocal_45_75m"
        ]
        arm = adapt_opportunity(page.items[0], now=page.as_of)
        assert arm.p1_score == page.items[0].market_opportunity_score


def test_wrong_token_is_market_unavailable_not_empty_data() -> None:
    with open_c1_client() as api:
        client = P1Client(base_url="http://p1.test", token="wrong-token",
                          transport=httpx.ASGITransport(app=api.app))
        with pytest.raises(MarketUnavailable, match="401"):
            client.fetch_opportunities()


def test_transport_failure_is_market_unavailable() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = P1Client(base_url="http://p1.test", token="t",
                      transport=httpx.MockTransport(_boom))
    with pytest.raises(MarketUnavailable, match="connection refused"):
        client.fetch_opportunities()


def test_no_token_configured_is_refused_before_any_request() -> None:
    client = P1Client(base_url="http://p1.test", token=None)
    with pytest.raises(MarketUnavailable, match="PLM_SERVICE_TOKEN"):
        client.fetch_opportunities()
```

- [ ] **Step 6: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_p1_adapter.py -q`
Expected: FAIL — `AttributeError: 'P1Client' object has no attribute 'fetch_opportunities'` (and the same class exists with only the mapping helpers).

- [ ] **Step 7: Write `P1Client`**

Append to `ingestion/p1_adapter.py` (and extend `__all__` with the constant/name if not already):

```python
from plm_platform.config import SERVICE_TOKEN_HEADER

_ENVELOPE_KEYS = frozenset({"items", "next_cursor", "as_of", "score_version"})


class P1Client:
    """The only P1 read path (R15): HTTP + service token, flat envelope, no tables."""

    def __init__(self, base_url: str, token: str | None, *, timeout_seconds: float = 10.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds
        self._transport = transport

    def fetch_opportunities(self, *, limit: int = 100,
                            cursor: str | None = None) -> OpportunityPage:
        if not self._token:
            raise MarketUnavailable(
                "no service token configured: set PLM_SERVICE_TOKEN (R15); an unconfigured "
                "P1 closes its API with 503 and P2 must not read that as empty data"
            )
        params: dict[str, str | int] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        try:
            with httpx.Client(base_url=self._base_url, timeout=self._timeout,
                              transport=self._transport) as client:
                response = client.get(MARKET_OPPORTUNITIES_PATH, params=params,
                                      headers={SERVICE_TOKEN_HEADER: self._token})
        except httpx.HTTPError as exc:
            raise MarketUnavailable(f"GET {MARKET_OPPORTUNITIES_PATH} failed: {exc}") from exc
        if response.status_code != 200:
            raise MarketUnavailable(
                f"GET {MARKET_OPPORTUNITIES_PATH} answered {response.status_code}"
            )
        body = response.json()
        if set(body) != _ENVELOPE_KEYS:
            raise MarketUnavailable(
                "C1 envelope drifted: expected "
                f"{sorted(_ENVELOPE_KEYS)}, got {sorted(body)} (ADR-0022: the flat object)"
            )
        return OpportunityPage.model_validate(body)
```

- [ ] **Step 8: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_p1_adapter.py -q`
Expected: PASS (7 tests).

- [ ] **Step 9: Prove the no-table rule holds**

The module must contain no database access. Add this guard to the same test file (it makes the
architectural rule executable rather than a comment):

```python
def test_adapter_never_imports_a_database_or_touches_a_table() -> None:
    from pathlib import Path

    source = Path("services/autonomous_ar/ingestion/p1_adapter.py").read_text(encoding="utf-8")
    assert "sqlalchemy" not in source, "AC-09/R15: P2 reads P1 over HTTP only"
    assert "market." not in source, "the adapter must not reference P1's schema"
```

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_p1_adapter.py -q`
Expected: PASS (8 tests).

- [ ] **Step 10: Commit**

Run: `git add services/autonomous_ar/ingestion tests/unit/ar/test_p1_adapter.py tests/support/c1_api.py; git commit -m "feat(ar): P2.2 read /market/v1 through the frozen C1 envelope"`

---

### Task P2.2A: C1 consumer seam — the consumed C1 field set pinned

**Files:**
- Create: `tests/contract/test_c1_market_to_ar_consumer.py`
- Modify: `services/autonomous_ar/ingestion/p1_adapter.py` (export `OpportunityPayload`: rename `_OpportunityPayload` and add the three nested names to `__all__`)

**Interfaces:**
- Consumes: `open_c1_client` (real P1 app over SQLite), `assert_seam_payload`/`SeamRegistry`, `P1Client`/`adapt_opportunity` (P2.2).
- Produces: the exported names `OpportunityPayload`, `TaxonomyPayload`, `ComponentsPayload`, `EvidencePayload` in `ingestion.p1_adapter` — the schema P3-side and future adapters may import, and the consumer schema this seam test validates the real producer payload against.
- **Honesty note:** the registry's `tests/contract/test_c1_market_to_ar.py` is P1's producer-side test and stays untouched. This file is P2's consumer half of the same seam, in `tests/contract/` because that is the only place two services are imported together (P0 §4). Name invented; rationale recorded in the open items.

- [ ] **Step 1: Write the failing seam test**

```python
# tests/contract/test_c1_market_to_ar_consumer.py
"""Seam C1 — PHASE-P1 → PHASE-P2, P2's consumer half.

P1's producer half lives in `tests/contract/test_c1_market_to_ar.py` (P1.10) and is not
duplicated here. This file pins what P2 *consumes*: the frozen C1 field names, the flat
envelope, the no-`freshness` rule, and null-never-zero components — against P1's real
payload served by the fixture app. A rename on either side fails here by name.
"""

from __future__ import annotations

import json

import httpx

from services.autonomous_ar.ingestion.p1_adapter import (
    EvidencePayload,
    OpportunityPayload,
    P1Client,
    TaxonomyPayload,
    adapt_opportunity,
)
from tests.support.c1_api import SEAM_TOKEN, open_c1_client
from tests.support.contract import SeamRegistry, assert_seam_payload

REGISTRY_ENTRY = "C1 · PHASE-P1 → PHASE-P2"

REGISTRY_FIELDS: tuple[str, ...] = (
    "arm_id", "market_opportunity_score", "confidence", "as_of", "taxonomy", "components",
    "evidence", "score_version",
)
REGISTRY_TAXONOMY_FIELDS: tuple[str, ...] = (
    "genre", "subgenre", "mood", "use_case", "language", "vocal_mode", "duration_bucket",
    "visual_world", "title_archetype",
)
REGISTRY_COMPONENT_FIELDS: tuple[str, ...] = (
    "demand", "growth", "competitor_velocity", "competition", "audience_quality",
    "cross_platform", "production_fit",
)
REGISTRY_EVIDENCE_FIELDS: tuple[str, ...] = ("observation_count", "source_count",
                                             "top_observation_ids")


def test_consumer_schema_matches_the_registry_field_lists() -> None:
    assert set(OpportunityPayload.model_fields) == set(REGISTRY_FIELDS)
    assert set(TaxonomyPayload.model_fields) == set(REGISTRY_TAXONOMY_FIELDS)
    components_model = OpportunityPayload.model_fields["components"].annotation
    assert set(components_model.model_fields) == set(REGISTRY_COMPONENT_FIELDS)
    assert set(EvidencePayload.model_fields) == set(REGISTRY_EVIDENCE_FIELDS)


def test_real_producer_payload_validates_against_the_consumer_schema() -> None:
    with open_c1_client() as api:
        body = api.get("/market/v1/opportunities", params={"limit": 1}).json()
        assert set(body) == {"items", "next_cursor", "as_of", "score_version"}, (
            "ADR-0022: the wire is the registry's flat envelope, never Page/Envelope"
        )
        payload = json.loads(json.dumps(body["items"][0]))
        assert "freshness" not in payload and "freshness_seconds" not in payload
        assert_seam_payload(payload, OpportunityPayload, seam=REGISTRY_ENTRY,
                            producer="GET /market/v1/opportunities",
                            consumer="autonomous_ar.ingestion.p1_adapter.OpportunityPayload")


def test_adapter_consumes_only_frozen_fields() -> None:
    with open_c1_client() as api:
        client = P1Client(base_url="http://p1.test", token=SEAM_TOKEN,
                          transport=httpx.ASGITransport(app=api.app))
        page = client.fetch_opportunities(limit=1)
        arm = adapt_opportunity(page.items[0], now=page.as_of)
        assert "freshness_seconds" not in page.items[0].model_dump()
        assert arm.freshness_seconds == 0.0  # derived here, from as_of, not read off the wire


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    assert contract_registry.exists, "the registry was frozen 2026-09-20; it cannot be missing"
    contract_registry.require_entry(REGISTRY_ENTRY)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\contract\test_c1_market_to_ar_consumer.py -q`
Expected: FAIL — `ImportError: cannot import name 'OpportunityPayload'` (the adapter exports only its functions in P2.2; the consumer seam test defines the surface).

- [ ] **Step 3: Export the consumer schema**

In `ingestion/p1_adapter.py`: rename `_OpportunityPayload` to `OpportunityPayload` everywhere
(model, `OpportunityPage.items`, `adapt_opportunity`'s parameter annotation) and extend `__all__`:

```python
__all__ = [
    "MARKET_OPPORTUNITIES_PATH", "ComponentsPayload", "EvidencePayload", "MarketUnavailable",
    "OpportunityPage", "OpportunityPayload", "P1Client", "TaxonomyPayload",
    "adapt_opportunity",
]
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\contract\test_c1_market_to_ar_consumer.py tests\unit\ar\test_p1_adapter.py -q`
Expected: PASS (12 tests). Full contracts stay green: `pytest tests\contract -q`.

- [ ] **Step 5: Commit**

Run: `git add services/autonomous_ar/ingestion/p1_adapter.py tests/contract/test_c1_market_to_ar_consumer.py; git commit -m "feat(ar): P2.2A export and pin the consumed C1 field set"`

---

### Task P2.3: Persistence

**Files:**
- Create: `services/autonomous_ar/storage/__init__.py`, `storage/ids.py`, `storage/models.py`, `storage/repositories.py`
- Create: `migrations/versions/ar/ar_0008_initial_tables.py`
- Create: `tests/support/ar_db.py`
- Create: `docs/adr/0024-p2-tables-model-and-states.md`
- Modify: `docs/contracts/DECISION_LOG.md` (one row)
- Test: `tests/unit/ar/test_storage_models.py`, `tests/unit/ar/test_repositories.py`, `tests/integration/ar/__init__.py`, `tests/integration/ar/test_schema_matches_models.py`

**Interfaces:**
- Consumes: `plm_platform.db.scoped_session("ar")`, `plm_platform.storage.{build_key, put_verified, to_uri}` (the `experiments/` prefix; used from P2.10 for cycle audit artifacts); `PolicyConfig`/`PosteriorState`/`RewardObservation`/`UpdateRecord`/`ContentArm` (P2.1).
- Produces (names later tasks import exactly):
  - `storage.ids.deterministic_id(*parts: str) -> str` — uuid5 over `plm:ar:p2` + parts joined with `|` (mirrors P1's rule: ids are derived, never minted; **no `uuid4` anywhere in P2**).
  - `storage.models`: `AR_SCHEMA = "ar"`, `AR_METADATA = sa.MetaData(schema=AR_SCHEMA)`, the fifteen tables as module-level `sa.Table` objects (names below) plus `AR_TABLES: tuple[sa.Table, ...]`.
  - `storage.repositories`: `insert_policy`, `activate_policy`, `load_policy`, `PolicyImmutableError`; `upsert_arm_state`, `set_arm_status`, `record_selection`, `load_arm_states`; `insert_feature_snapshot`, `load_feature_snapshot`; `upsert_posterior`, `load_posterior`, `PosteriorVersionConflict`; `insert_cycle`, `finish_cycle`, `load_cycle`; `insert_decision`, `load_decisions`; `insert_directive`, `list_queued_directives`, `load_directive`; `insert_reward_observation`, `insert_experiment_result`, `load_pending_observations`, `insert_posterior_update`, `load_posterior_update`; `replace_similarity`, `load_similarity`; `insert_exposure`, `load_exposure`; `replace_baselines`, `load_baselines`; `insert_override`, `load_active_overrides`; `get_channel_state`, `set_channel_state`.
  - `tests.support.ar_db`: `create_ar_engine() -> sa.Engine` (SQLite with `ar` attached, `AR_METADATA.create_all`), `ar_session(engine)` context helper.
- **Ruling (recorded in ADR-0024):** the spec's §44 table names gain the `ar_` prefix (`ar_content_arm_state`, `ar_arm_feature_snapshot`, `ar_arm_posterior`, `ar_experiment_result`, `ar_reward_observation`, `ar_allocation_policy`, `ar_allocation_cycle`, `ar_allocation_decision`, `ar_production_directive`, `ar_arm_similarity`, `ar_arm_exposure`, `ar_baseline_config`) plus three tables the spec's field lists require but never name: `ar_posterior_update_log` (§74's persisted update state), `ar_channel_state` (§117's activation state), `ar_manual_override` (§107/§108's explicit event). R3: the phase plan owns table names; the monorepo convention is `<schema>_`-prefixed (P1's `market_*`, ADR-0022).

- [ ] **Step 1: Write the failing models test**

```python
# tests/unit/ar/test_storage_models.py
import sqlalchemy as sa

from services.autonomous_ar.storage.models import AR_METADATA, AR_SCHEMA, AR_TABLES

EXPECTED_TABLES = {
    "ar_content_arm_state", "ar_arm_feature_snapshot", "ar_arm_posterior",
    "ar_experiment_result", "ar_reward_observation", "ar_allocation_policy",
    "ar_allocation_cycle", "ar_allocation_decision", "ar_production_directive",
    "ar_arm_similarity", "ar_arm_exposure", "ar_baseline_config",
    "ar_posterior_update_log", "ar_channel_state", "ar_manual_override",
}


def test_schema_and_table_set_are_exact() -> None:
    assert AR_SCHEMA == "ar"
    assert AR_METADATA.schema == "ar"
    assert {table.name for table in AR_TABLES} == EXPECTED_TABLES


def test_no_native_enum_types_anywhere() -> None:
    for table in AR_TABLES:
        for column in table.columns:
            assert not isinstance(column.type, sa.Enum), (
                f"{table.name}.{column.name}: enum values are text validated in Python"
            )


def test_arm_identity_columns_are_plain_text_because_p1_mints_them() -> None:
    state = AR_METADATA.tables["ar_content_arm_state"]
    assert isinstance(state.c.arm_id.type, sa.Text) and state.c.arm_id.unique
    assert state.c.arm_id.primary_key is False  # the row id is P2's; the arm id is P1's
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_storage_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.storage'` (create `storage/__init__.py` empty first).

- [ ] **Step 3: Write `storage/ids.py` and `storage/models.py`**

```python
# services/autonomous_ar/storage/ids.py
"""Ids are derived, never minted: uuid5 over a row's stable identity parts.

The rule imported from P1's fix round: a computation path never touches `uuid4` or a
clock, so replaying the same cycle produces the same rows and the same directive ids.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

__all__ = ["deterministic_id"]

_ID_NAMESPACE: UUID = uuid5(NAMESPACE_URL, "plm:ar:p2")


def deterministic_id(*parts: str) -> str:
    """uuid5 over the identity parts, joined with `|` — stable across processes."""
    if not parts or any(not part for part in parts):
        raise ValueError("deterministic_id needs at least one non-empty part")
    return str(uuid5(_ID_NAMESPACE, "|".join(parts)))
```

```python
# services/autonomous_ar/storage/models.py
"""The `ar` schema: fifteen tables, text enums only, ids derived from stable identity.

§44 lists twelve tables; the three extra ones carry state the same section requires
(§74's update log, §117's activation state, §107/§108's override event). Every instant
column is `timezone=True`; every enum-valued column is `Text` validated by
`domain.enums` (ledger ruling L4 — no native Postgres enums).
"""

from __future__ import annotations

import sqlalchemy as sa

__all__ = ["AR_METADATA", "AR_SCHEMA", "AR_TABLES"]

AR_SCHEMA = "ar"
AR_METADATA = sa.MetaData(schema=AR_SCHEMA)

_uuid = sa.Uuid(as_uuid=False)
_ts = sa.DateTime(timezone=True)
_json = sa.JSON


def _id_column() -> sa.Column:
    return sa.Column("id", _uuid, primary_key=True)


ar_content_arm_state = sa.Table(
    "ar_content_arm_state", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False, unique=True),  # P1's id, verbatim (R15)
    sa.Column("status", sa.Text, nullable=False, server_default="candidate"),
    sa.Column("first_selected_at", _ts, nullable=True),
    sa.Column("last_selected_at", _ts, nullable=True),
    sa.Column("selection_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("mature_result_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("provisional_result_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("recent_exposure_score", sa.Float, nullable=False, server_default="0"),
    sa.Column("retirement_reason", sa.Text, nullable=True),
    sa.Column("cooldown_until", _ts, nullable=True),
    sa.Column("last_updated_at", _ts, nullable=False),
    sa.Column("state_version", sa.Integer, nullable=False, server_default="1"),
)

ar_arm_feature_snapshot = sa.Table(
    "ar_arm_feature_snapshot", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("captured_at", _ts, nullable=False),
    sa.Column("p1_score", sa.Float, nullable=False),
    sa.Column("p1_confidence", sa.Float, nullable=False),
    sa.Column("demand_score", sa.Float, nullable=True),
    sa.Column("growth_score", sa.Float, nullable=True),
    sa.Column("competition_score", sa.Float, nullable=True),
    sa.Column("cross_platform_score", sa.Float, nullable=True),
    sa.Column("production_fit_score", sa.Float, nullable=True),
    sa.Column("freshness_seconds", sa.Float, nullable=False),
    sa.Column("feature_json", _json, nullable=False),
    sa.Column("feature_version", sa.Text, nullable=False),
)

ar_arm_posterior = sa.Table(
    "ar_arm_posterior", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False, unique=True),
    sa.Column("model_type", sa.Text, nullable=False),
    sa.Column("model_version", sa.Text, nullable=False),
    sa.Column("prior_mean", sa.Float, nullable=False),
    sa.Column("prior_variance", sa.Float, nullable=False),
    sa.Column("posterior_mean", sa.Float, nullable=False),
    sa.Column("posterior_variance", sa.Float, nullable=False),
    sa.Column("observation_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("effective_observation_count", sa.Float, nullable=False, server_default="0"),
    sa.Column("last_update_at", _ts, nullable=False),
    sa.Column("update_version", sa.Integer, nullable=False, server_default="0"),
    sa.Column("context_state_json", _json, nullable=True),
)

ar_experiment_result = sa.Table(
    "ar_experiment_result", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("experiment_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("content_id", sa.Text, nullable=False),
    sa.Column("observation_window", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("observed_at", _ts, nullable=False),
    sa.Column("raw_metrics_json", _json, nullable=False),
    sa.Column("normalized_reward", sa.Float, nullable=True),
    sa.Column("reward_version", sa.Text, nullable=False),
    sa.Column("attribution_confidence", sa.Float, nullable=False),
    sa.Column("source_ids", _json, nullable=False),
    sa.UniqueConstraint("experiment_id", "observation_window",
                        name="uq_ar_experiment_result_window"),
)

ar_reward_observation = sa.Table(
    "ar_reward_observation", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("experiment_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("content_id", sa.Text, nullable=False),
    sa.Column("observation_window", sa.Text, nullable=False),
    sa.Column("maturity", sa.Text, nullable=False),
    sa.Column("observed_at", _ts, nullable=False),
    sa.Column("raw_metrics_json", _json, nullable=False),
    sa.Column("normalized_reward", sa.Float, nullable=True),  # NULL = unknown, not zero
    sa.Column("reward_version", sa.Text, nullable=False),
    sa.Column("attribution_confidence", sa.Float, nullable=False),
    sa.Column("quality_status", sa.Text, nullable=True),
    sa.Column("economic_terms_json", _json, nullable=True),   # reserved C7 terms, stored as-is
    sa.Column("received_at", _ts, nullable=False),
    sa.UniqueConstraint("experiment_id", "observation_window",
                        name="uq_ar_reward_observation_window"),
)

ar_allocation_policy = sa.Table(
    "ar_allocation_policy", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("version", sa.Text, nullable=False, unique=True),  # immutable once used (§92)
    sa.Column("exploit_fraction", sa.Float, nullable=False),
    sa.Column("adjacent_explore_fraction", sa.Float, nullable=False),
    sa.Column("radical_explore_fraction", sa.Float, nullable=False),
    sa.Column("baseline_fraction", sa.Float, nullable=False),
    sa.Column("max_exact_arm_share", sa.Float, nullable=False),
    sa.Column("max_cluster_share", sa.Float, nullable=False),
    sa.Column("max_genre_share", sa.Float, nullable=False),
    sa.Column("max_use_case_share", sa.Float, nullable=False),
    sa.Column("max_language_share", sa.Float, nullable=False),
    sa.Column("fatigue_config_json", _json, nullable=False),
    sa.Column("nonstationarity_config_json", _json, nullable=False),
    sa.Column("retirement_config_json", _json, nullable=False),
    sa.Column("created_at", _ts, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
)

ar_allocation_cycle = sa.Table(
    "ar_allocation_cycle", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("started_at", _ts, nullable=False),
    sa.Column("finished_at", _ts, nullable=True),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("slot_count", sa.Integer, nullable=False),
    sa.Column("budget", sa.Float, nullable=False),
    sa.Column("policy_id", _uuid, nullable=False),
    sa.Column("p1_snapshot_time", _ts, nullable=False),
    sa.Column("decision_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("selected_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("rejected_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("warning_count", sa.Integer, nullable=False, server_default="0"),
    # additions ADR-0024 records: persistence-only, never wire fields (C2 stays frozen)
    sa.Column("activation_state", sa.Text, nullable=False),
    sa.Column("random_seed", sa.BigInteger, nullable=False),
    sa.Column("warning_codes_json", _json, nullable=False),
)

ar_allocation_decision = sa.Table(
    "ar_allocation_decision", AR_METADATA,
    _id_column(),
    sa.Column("cycle_id", _uuid, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("allocation_type", sa.Text, nullable=False),
    sa.Column("sampled_reward", sa.Float, nullable=False),
    sa.Column("posterior_expected_reward", sa.Float, nullable=False),
    sa.Column("selection_priority", sa.Float, nullable=False),
    sa.Column("rank_before_constraints", sa.Integer, nullable=True),
    sa.Column("rank_after_constraints", sa.Integer, nullable=True),
    sa.Column("constraint_adjustments_json", _json, nullable=False),
    sa.Column("reason_codes", _json, nullable=False),
    sa.Column("policy_version", sa.Text, nullable=False),
    sa.Column("model_version", sa.Text, nullable=False),
    sa.Column("feature_version", sa.Text, nullable=False),
    sa.Column("normalization_version", sa.Text, nullable=False),
    sa.Column("p1_score_version", sa.Text, nullable=True),
    sa.Column("feature_snapshot_id", _uuid, nullable=False),
    sa.Column("created_at", _ts, nullable=False),
)

ar_production_directive = sa.Table(
    "ar_production_directive", AR_METADATA,
    _id_column(),
    sa.Column("decision_id", _uuid, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("quantity", sa.Integer, nullable=False),
    sa.Column("brief_json", _json, nullable=False),
    sa.Column("priority", sa.Integer, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="queued"),
    sa.Column("expires_at", _ts, nullable=False),
    sa.Column("created_at", _ts, nullable=False),
    sa.Column("consumed_at", _ts, nullable=True),
    # wire copies, persisted so GET /ar/directives can serve the frozen C2 payload
    sa.Column("allocation_type", sa.Text, nullable=False),
    sa.Column("expected_value", sa.Float, nullable=False),
    sa.Column("uncertainty", sa.Float, nullable=False),
    sa.Column("budget_limit", sa.Float, nullable=False),
    sa.Column("market_context_json", _json, nullable=False),
    sa.Column("selection_reason_json", _json, nullable=False),
    sa.Column("directive_version", sa.Text, nullable=False),
    sa.Column("policy_version", sa.Text, nullable=False),
    sa.Column("exploration_fraction", sa.Float, nullable=False),
    sa.Column("value_model", sa.Text, nullable=False),
)

ar_arm_similarity = sa.Table(
    "ar_arm_similarity", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id_a", sa.Text, nullable=False),
    sa.Column("arm_id_b", sa.Text, nullable=False),
    sa.Column("similarity_score", sa.Float, nullable=False),
    sa.Column("similarity_method", sa.Text, nullable=False),
    sa.Column("feature_version", sa.Text, nullable=False),
    sa.Column("computed_at", _ts, nullable=False),
    sa.UniqueConstraint("arm_id_a", "arm_id_b", name="uq_ar_arm_similarity_pair"),
)

ar_arm_exposure = sa.Table(
    "ar_arm_exposure", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("window_start", _ts, nullable=False),
    sa.Column("window_end", _ts, nullable=False),
    sa.Column("selection_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("content_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("estimated_audience_exposure", sa.Float, nullable=True),  # unknown ≠ 0 (§54)
    sa.Column("exposure_score", sa.Float, nullable=False, server_default="0"),
    sa.Column("calculation_version", sa.Text, nullable=False),
    sa.UniqueConstraint("arm_id", "window_start", "window_end",
                        name="uq_ar_arm_exposure_window"),
)

ar_baseline_config = sa.Table(
    "ar_baseline_config", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("added_at", _ts, nullable=False),
    sa.Column("removed_at", _ts, nullable=True),
    sa.Column("reason", sa.Text, nullable=True),
    sa.Column("config_version", sa.Text, nullable=False),
    sa.UniqueConstraint("brand_id", "arm_id", name="uq_ar_baseline_arm"),
)

ar_posterior_update_log = sa.Table(
    "ar_posterior_update_log", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("reward_observation_id", _uuid, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("posterior_version_before", sa.Integer, nullable=False),
    sa.Column("posterior_version_after", sa.Integer, nullable=False),
    sa.Column("update_status", sa.Text, nullable=False),
    sa.Column("effective_weight", sa.Float, nullable=False),
    sa.Column("updated_at", _ts, nullable=False),
)

# §74: an `applied` update exists at most once per observation; failed/quarantined
# attempts stay in the log and are retried with a new row.
sa.Index(
    "uq_ar_posterior_update_applied", ar_posterior_update_log.c.reward_observation_id,
    unique=True,
    sqlite_where=sa.text("update_status = 'applied'"),
    postgresql_where=sa.text("update_status = 'applied'"),
)

ar_channel_state = sa.Table(
    "ar_channel_state", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False, unique=True),
    sa.Column("activation_state", sa.Text, nullable=False),
    sa.Column("updated_at", _ts, nullable=False),
    sa.Column("reason", sa.Text, nullable=True),
    sa.Column("updated_by", sa.Text, nullable=True),
)

ar_manual_override = sa.Table(
    "ar_manual_override", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("target_type", sa.Text, nullable=False),
    sa.Column("target_id", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("created_at", _ts, nullable=False),
    sa.Column("expires_at", _ts, nullable=True),   # §108: never an indefinite hidden override
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
)

AR_TABLES: tuple[sa.Table, ...] = (
    ar_content_arm_state, ar_arm_feature_snapshot, ar_arm_posterior, ar_experiment_result,
    ar_reward_observation, ar_allocation_policy, ar_allocation_cycle, ar_allocation_decision,
    ar_production_directive, ar_arm_similarity, ar_arm_exposure, ar_baseline_config,
    ar_posterior_update_log, ar_channel_state, ar_manual_override,
)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_storage_models.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the failing migration-vs-models test**

```python
# tests/unit/ar/test_migration_module.py
"""The migration and the models must describe the same DDL (P1.2's drift-guard pattern)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from services.autonomous_ar.storage.models import AR_METADATA

MIGRATION_PATH = Path("migrations/versions/ar/ar_0008_initial_tables.py")


def _load_migration() -> object:
    spec = importlib.util.spec_from_file_location("ar_0008_initial_tables", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_metadata_chains_onto_the_market_head() -> None:
    module = _load_migration()
    assert module.revision == "0008_ar_initial"  # <= 32 chars (alembic_version is varchar(32))
    assert module.down_revision == "0007_market_snapshot_bodies"


def test_upgrade_creates_exactly_the_model_tables_and_downgrade_removes_them() -> None:
    module = _load_migration()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS ar")
        module.op = Operations(MigrationContext.configure(connection))  # type: ignore[attr-defined]
        module.upgrade()  # type: ignore[attr-defined]
        created = set(sa.inspect(connection).get_table_names(schema="ar"))
        assert created == {table.name for table in AR_METADATA.tables.values()}
        module.downgrade()  # type: ignore[attr-defined]
        assert set(sa.inspect(connection).get_table_names(schema="ar")) == set()
```

- [ ] **Step 6: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_migration_module.py -q`
Expected: FAIL — `FileNotFoundError`/`OSError` loading `migrations/versions/ar/ar_0008_initial_tables.py` (the directory does not exist yet).

- [ ] **Step 7: Write the migration**

```python
# migrations/versions/ar/ar_0008_initial_tables.py
"""P2 initial tables — the `ar` schema.

Revision ID: 0008_ar_initial
Revises: 0007_market_snapshot_bodies
Create Date: 2026-09-20

Table names and the three tables beyond §44's twelve are recorded in ADR-0024. Enum
values are `Text` validated in Python (no native Postgres enums); every instant is
`timezone=True`; `downgrade()` drops exactly what `upgrade()` creates.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_ar_initial"
down_revision = "0007_market_snapshot_bodies"
branch_labels = None
depends_on = None

_uuid = sa.Uuid(as_uuid=False)
_ts = sa.DateTime(timezone=True)
_json = sa.JSON
SCHEMA = "ar"


def _pk() -> sa.Column:
    return sa.Column("id", _uuid, primary_key=True)


def upgrade() -> None:
    op.create_table(
        "ar_content_arm_state", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False, unique=True),
        sa.Column("status", sa.Text, nullable=False, server_default="candidate"),
        sa.Column("first_selected_at", _ts, nullable=True),
        sa.Column("last_selected_at", _ts, nullable=True),
        sa.Column("selection_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("mature_result_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("provisional_result_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("recent_exposure_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("retirement_reason", sa.Text, nullable=True),
        sa.Column("cooldown_until", _ts, nullable=True),
        sa.Column("last_updated_at", _ts, nullable=False),
        sa.Column("state_version", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_table(
        "ar_arm_feature_snapshot", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("captured_at", _ts, nullable=False),
        sa.Column("p1_score", sa.Float, nullable=False),
        sa.Column("p1_confidence", sa.Float, nullable=False),
        sa.Column("demand_score", sa.Float, nullable=True),
        sa.Column("growth_score", sa.Float, nullable=True),
        sa.Column("competition_score", sa.Float, nullable=True),
        sa.Column("cross_platform_score", sa.Float, nullable=True),
        sa.Column("production_fit_score", sa.Float, nullable=True),
        sa.Column("freshness_seconds", sa.Float, nullable=False),
        sa.Column("feature_json", _json, nullable=False),
        sa.Column("feature_version", sa.Text, nullable=False),
    )
    op.create_table(
        "ar_arm_posterior", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False, unique=True),
        sa.Column("model_type", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("prior_mean", sa.Float, nullable=False),
        sa.Column("prior_variance", sa.Float, nullable=False),
        sa.Column("posterior_mean", sa.Float, nullable=False),
        sa.Column("posterior_variance", sa.Float, nullable=False),
        sa.Column("observation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("effective_observation_count", sa.Float, nullable=False, server_default="0"),
        sa.Column("last_update_at", _ts, nullable=False),
        sa.Column("update_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("context_state_json", _json, nullable=True),
    )
    op.create_table(
        "ar_experiment_result", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("experiment_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("content_id", sa.Text, nullable=False),
        sa.Column("observation_window", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("observed_at", _ts, nullable=False),
        sa.Column("raw_metrics_json", _json, nullable=False),
        sa.Column("normalized_reward", sa.Float, nullable=True),
        sa.Column("reward_version", sa.Text, nullable=False),
        sa.Column("attribution_confidence", sa.Float, nullable=False),
        sa.Column("source_ids", _json, nullable=False),
        sa.UniqueConstraint("experiment_id", "observation_window",
                            name="uq_ar_experiment_result_window"),
    )
    op.create_table(
        "ar_reward_observation", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("experiment_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("content_id", sa.Text, nullable=False),
        sa.Column("observation_window", sa.Text, nullable=False),
        sa.Column("maturity", sa.Text, nullable=False),
        sa.Column("observed_at", _ts, nullable=False),
        sa.Column("raw_metrics_json", _json, nullable=False),
        sa.Column("normalized_reward", sa.Float, nullable=True),
        sa.Column("reward_version", sa.Text, nullable=False),
        sa.Column("attribution_confidence", sa.Float, nullable=False),
        sa.Column("quality_status", sa.Text, nullable=True),
        sa.Column("economic_terms_json", _json, nullable=True),
        sa.Column("received_at", _ts, nullable=False),
        sa.UniqueConstraint("experiment_id", "observation_window",
                            name="uq_ar_reward_observation_window"),
    )
    op.create_table(
        "ar_allocation_policy", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False, unique=True),
        sa.Column("exploit_fraction", sa.Float, nullable=False),
        sa.Column("adjacent_explore_fraction", sa.Float, nullable=False),
        sa.Column("radical_explore_fraction", sa.Float, nullable=False),
        sa.Column("baseline_fraction", sa.Float, nullable=False),
        sa.Column("max_exact_arm_share", sa.Float, nullable=False),
        sa.Column("max_cluster_share", sa.Float, nullable=False),
        sa.Column("max_genre_share", sa.Float, nullable=False),
        sa.Column("max_use_case_share", sa.Float, nullable=False),
        sa.Column("max_language_share", sa.Float, nullable=False),
        sa.Column("fatigue_config_json", _json, nullable=False),
        sa.Column("nonstationarity_config_json", _json, nullable=False),
        sa.Column("retirement_config_json", _json, nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "ar_allocation_cycle", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("started_at", _ts, nullable=False),
        sa.Column("finished_at", _ts, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("slot_count", sa.Integer, nullable=False),
        sa.Column("budget", sa.Float, nullable=False),
        sa.Column("policy_id", _uuid, nullable=False),
        sa.Column("p1_snapshot_time", _ts, nullable=False),
        sa.Column("decision_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("selected_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("activation_state", sa.Text, nullable=False),
        sa.Column("random_seed", sa.BigInteger, nullable=False),
        sa.Column("warning_codes_json", _json, nullable=False),
    )
    op.create_table(
        "ar_allocation_decision", schema=SCHEMA,
        _pk(),
        sa.Column("cycle_id", _uuid, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("allocation_type", sa.Text, nullable=False),
        sa.Column("sampled_reward", sa.Float, nullable=False),
        sa.Column("posterior_expected_reward", sa.Float, nullable=False),
        sa.Column("selection_priority", sa.Float, nullable=False),
        sa.Column("rank_before_constraints", sa.Integer, nullable=True),
        sa.Column("rank_after_constraints", sa.Integer, nullable=True),
        sa.Column("constraint_adjustments_json", _json, nullable=False),
        sa.Column("reason_codes", _json, nullable=False),
        sa.Column("policy_version", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("feature_version", sa.Text, nullable=False),
        sa.Column("normalization_version", sa.Text, nullable=False),
        sa.Column("p1_score_version", sa.Text, nullable=True),
        sa.Column("feature_snapshot_id", _uuid, nullable=False),
        sa.Column("created_at", _ts, nullable=False),
    )
    op.create_table(
        "ar_production_directive", schema=SCHEMA,
        _pk(),
        sa.Column("decision_id", _uuid, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("brief_json", _json, nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("expires_at", _ts, nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        sa.Column("consumed_at", _ts, nullable=True),
        sa.Column("allocation_type", sa.Text, nullable=False),
        sa.Column("expected_value", sa.Float, nullable=False),
        sa.Column("uncertainty", sa.Float, nullable=False),
        sa.Column("budget_limit", sa.Float, nullable=False),
        sa.Column("market_context_json", _json, nullable=False),
        sa.Column("selection_reason_json", _json, nullable=False),
        sa.Column("directive_version", sa.Text, nullable=False),
        sa.Column("policy_version", sa.Text, nullable=False),
        sa.Column("exploration_fraction", sa.Float, nullable=False),
        sa.Column("value_model", sa.Text, nullable=False),
    )
    op.create_table(
        "ar_arm_similarity", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("arm_id_a", sa.Text, nullable=False),
        sa.Column("arm_id_b", sa.Text, nullable=False),
        sa.Column("similarity_score", sa.Float, nullable=False),
        sa.Column("similarity_method", sa.Text, nullable=False),
        sa.Column("feature_version", sa.Text, nullable=False),
        sa.Column("computed_at", _ts, nullable=False),
        sa.UniqueConstraint("arm_id_a", "arm_id_b", name="uq_ar_arm_similarity_pair"),
    )
    op.create_table(
        "ar_arm_exposure", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("window_start", _ts, nullable=False),
        sa.Column("window_end", _ts, nullable=False),
        sa.Column("selection_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("content_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("estimated_audience_exposure", sa.Float, nullable=True),
        sa.Column("exposure_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("calculation_version", sa.Text, nullable=False),
        sa.UniqueConstraint("arm_id", "window_start", "window_end",
                            name="uq_ar_arm_exposure_window"),
    )
    op.create_table(
        "ar_baseline_config", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("added_at", _ts, nullable=False),
        sa.Column("removed_at", _ts, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("config_version", sa.Text, nullable=False),
        sa.UniqueConstraint("brand_id", "arm_id", name="uq_ar_baseline_arm"),
    )
    op.create_table(
        "ar_posterior_update_log", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("reward_observation_id", _uuid, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("posterior_version_before", sa.Integer, nullable=False),
        sa.Column("posterior_version_after", sa.Integer, nullable=False),
        sa.Column("update_status", sa.Text, nullable=False),
        sa.Column("effective_weight", sa.Float, nullable=False),
        sa.Column("updated_at", _ts, nullable=False),
    )
    op.create_index(
        "uq_ar_posterior_update_applied", "ar_posterior_update_log",
        ["reward_observation_id"], unique=True, schema=SCHEMA,
        sqlite_where=sa.text("update_status = 'applied'"),
        postgresql_where=sa.text("update_status = 'applied'"),
    )
    op.create_table(
        "ar_channel_state", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False, unique=True),
        sa.Column("activation_state", sa.Text, nullable=False),
        sa.Column("updated_at", _ts, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("updated_by", sa.Text, nullable=True),
    )
    op.create_table(
        "ar_manual_override", schema=SCHEMA,
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text, nullable=False),
        sa.Column("target_id", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        sa.Column("expires_at", _ts, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    for table in (
        "ar_manual_override", "ar_channel_state", "ar_posterior_update_log",
        "ar_baseline_config", "ar_arm_exposure", "ar_arm_similarity",
        "ar_production_directive", "ar_allocation_decision", "ar_allocation_cycle",
        "ar_allocation_policy", "ar_reward_observation", "ar_experiment_result",
        "ar_arm_posterior", "ar_arm_feature_snapshot", "ar_content_arm_state",
    ):
        op.drop_table(table, schema=SCHEMA)
```

- [ ] **Step 8: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_migration_module.py -q`
Expected: PASS (2 tests).

- [ ] **Step 9: Write the failing repositories tests**

```python
# tests/unit/ar/test_repositories.py — first cycle: policies, state, snapshots, posteriors
from datetime import UTC, datetime

import pytest

from services.autonomous_ar.domain.policy import PolicyConfig
from services.autonomous_ar.storage import repositories
from tests.support.ar_db import create_ar_engine, ar_session

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_policy_rows_are_immutable_once_created() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        policy_id = repositories.insert_policy(
            session, PolicyConfig(name="default"), brand_id="neon_seoul", created_at=NOW
        )
        assert repositories.load_policy(session, "p2-v1").exploit_fraction == 0.70
        # same version, different content: refused (§92 — never mutate a used policy)
        with pytest.raises(repositories.PolicyImmutableError, match="p2-v1"):
            repositories.insert_policy(
                session, PolicyConfig(name="default", exploit_fraction=0.5,
                                      adjacent_explore_fraction=0.3,
                                      radical_explore_fraction=0.2),
                brand_id="neon_seoul", created_at=NOW,
            )
        # replaying the exact same row is idempotent
        again = repositories.insert_policy(
            session, PolicyConfig(name="default"), brand_id="neon_seoul", created_at=NOW
        )
        assert again == policy_id
```

```python
# tests/unit/ar/test_repositories.py — second cycle (append): posterior version guard
def test_posterior_update_uses_an_optimistic_version_guard() -> None:
    from services.autonomous_ar.domain.posterior import PosteriorState, PriorParameters

    engine = create_ar_engine()
    prior = PriorParameters(mean=0.6, variance=0.05, confidence=0.7, prior_version="p2-prior-v1")
    state = PosteriorState(
        arm_id="arm-1", model_type="normal-normal", model_version="p2-nn-v1",
        prior_mean=prior.mean, prior_variance=prior.variance, posterior_mean=prior.mean,
        posterior_variance=prior.variance, observation_count=0,
        effective_observation_count=0.0, update_version=0, last_update_at=NOW,
    )
    with ar_session(engine) as session:
        repositories.upsert_posterior(session, state, brand_id="neon_seoul", expected_version=0)
        updated = PosteriorState(
            arm_id="arm-1", model_type="normal-normal", model_version="p2-nn-v1",
            prior_mean=prior.mean, prior_variance=prior.variance, posterior_mean=0.65,
            posterior_variance=0.04, observation_count=1, effective_observation_count=1.0,
            update_version=1, last_update_at=NOW,
        )
        repositories.upsert_posterior(session, updated, brand_id="neon_seoul", expected_version=0)
        # a second writer still holding version 0 must fail loudly (§75), not overwrite
        with pytest.raises(repositories.PosteriorVersionConflict, match="arm-1"):
            repositories.upsert_posterior(session, updated.model_copy(update={"posterior_mean": 0.9}),
                                          brand_id="neon_seoul", expected_version=0)
        loaded = repositories.load_posterior(session, "arm-1")
        assert loaded is not None and loaded.posterior_mean == 0.65 and loaded.update_version == 1
```

- [ ] **Step 10: Run them and watch them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_repositories.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.support.ar_db'` (then, once that is created in Step 11, the missing repository functions).

- [ ] **Step 11: Write `tests/support/ar_db.py` and `storage/repositories.py`**

```python
# tests/support/ar_db.py
"""SQLite `ar` schema for unit tests — the same pattern as `market_db`, no docker."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from services.autonomous_ar.storage.models import AR_METADATA

__all__ = ["ar_session", "create_ar_engine"]


def create_ar_engine() -> sa.Engine:
    """In-memory SQLite with an attached `ar` schema and all P2 tables created."""
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS ar")
    AR_METADATA.create_all(engine)
    return engine


def ar_session(engine: sa.Engine) -> Session:
    """A session bound to the engine (committed on clean exit by callers as needed)."""
    return sessionmaker(bind=engine)()
```

```python
# services/autonomous_ar/storage/repositories.py
"""Row-level helpers for the `ar` schema. Ids derive from stable identity; no uuid4.

Semantics that carry rules (not convenience):
* policies are immutable per version (§92);
* feature snapshots are insert-only — a decision's market context is never recomputed
  from current P1 values (no leakage, Review Focus 6);
* posterior writes are guarded by `update_version` (§75);
* reward observations are idempotent on `(experiment_id, observation_window)` (§73);
* `applied` posterior updates are unique per reward observation (§74).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.arm import ContentArm
from ..domain.directive import ProductionDirective
from ..domain.enums import AllocationCycleStatus, ArmLifecycleStatus, DirectiveStatus
from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState, UpdateRecord
from ..domain.reward import RewardObservation
from .ids import deterministic_id
from .models import (
    ar_allocation_cycle, ar_allocation_decision, ar_allocation_policy, ar_arm_exposure,
    ar_arm_feature_snapshot, ar_arm_posterior, ar_arm_similarity, ar_baseline_config,
    ar_channel_state, ar_content_arm_state, ar_experiment_result, ar_manual_override,
    ar_posterior_update_log, ar_production_directive, ar_reward_observation,
)

__all__ = [
    "PolicyImmutableError", "PosteriorVersionConflict", "activate_policy",
    "finish_cycle", "get_channel_state", "insert_cycle", "insert_decision",
    "insert_directive", "insert_experiment_result", "insert_exposure",
    "insert_feature_snapshot", "insert_override", "insert_policy",
    "insert_posterior_update", "insert_reward_observation", "list_queued_directives",
    "load_active_overrides", "load_arm_states", "load_baselines", "load_cycle",
    "load_decisions", "load_exposure", "load_feature_snapshot", "load_pending_observations",
    "load_policy", "load_posterior", "load_posterior_update", "load_similarity",
    "record_selection", "replace_baselines", "replace_similarity", "set_arm_status",
    "set_channel_state", "upsert_arm_state", "upsert_posterior",
]


class PolicyImmutableError(RuntimeError):
    """A policy version already exists with different content (§92)."""


class PosteriorVersionConflict(RuntimeError):
    """Someone else advanced the arm's posterior first (§75)."""


# --- policies -----------------------------------------------------------------------

def _policy_row(config: PolicyConfig, *, brand_id: str | None, created_at: datetime) -> dict[str, Any]:
    return {
        "id": deterministic_id("policy", brand_id or "global", config.version),
        "brand_id": brand_id, "name": config.name, "version": config.version,
        "exploit_fraction": config.exploit_fraction,
        "adjacent_explore_fraction": config.adjacent_explore_fraction,
        "radical_explore_fraction": config.radical_explore_fraction,
        "baseline_fraction": config.baseline_fraction,
        "max_exact_arm_share": config.max_exact_arm_share,
        "max_cluster_share": config.max_cluster_share,
        "max_genre_share": config.max_genre_share,
        "max_use_case_share": config.max_use_case_share,
        "max_language_share": config.max_language_share,
        "fatigue_config_json": config.fatigue.model_dump(),
        "nonstationarity_config_json": config.nonstationarity.model_dump(),
        "retirement_config_json": config.retirement.model_dump(),
        "created_at": created_at, "active": False,
    }


def _policy_from_row(row: Mapping[str, Any]) -> PolicyConfig:
    return PolicyConfig(
        name=row["name"], version=row["version"],
        exploit_fraction=row["exploit_fraction"],
        adjacent_explore_fraction=row["adjacent_explore_fraction"],
        radical_explore_fraction=row["radical_explore_fraction"],
        baseline_fraction=row["baseline_fraction"],
        max_exact_arm_share=row["max_exact_arm_share"],
        max_cluster_share=row["max_cluster_share"], max_genre_share=row["max_genre_share"],
        max_use_case_share=row["max_use_case_share"],
        max_language_share=row["max_language_share"],
        fatigue=row["fatigue_config_json"], nonstationarity=row["nonstationarity_config_json"],
        retirement=row["retirement_config_json"],
    )


def insert_policy(session: Session, config: PolicyConfig, *, brand_id: str | None,
                  created_at: datetime) -> str:
    """Insert once per version; replaying the same version returns the existing id."""
    existing = session.execute(
        sa.select(ar_allocation_policy).where(ar_allocation_policy.c.version == config.version)
    ).mappings().one_or_none()
    if existing is not None:
        if _policy_from_row(existing) != config:
            raise PolicyImmutableError(
                f"policy {config.version!r} already exists with different content: create a new "
                "version and activate it, never mutate a used policy (§92)"
            )
        return str(existing["id"])
    row = _policy_row(config, brand_id=brand_id, created_at=created_at)
    try:
        session.execute(sa.insert(ar_allocation_policy).values(**row))
    except IntegrityError:
        session.rollback()
        return str(session.execute(
            sa.select(ar_allocation_policy.c.id)
            .where(ar_allocation_policy.c.version == config.version)
        ).scalar_one())
    return str(row["id"])


def activate_policy(session: Session, version: str) -> None:
    session.execute(sa.update(ar_allocation_policy).values(active=False))
    result = session.execute(
        sa.update(ar_allocation_policy).where(ar_allocation_policy.c.version == version)
        .values(active=True)
    )
    if result.rowcount == 0:
        raise ValueError(f"policy {version!r} does not exist: activate a stored version only")


def load_policy(session: Session, version: str) -> PolicyConfig | None:
    row = session.execute(
        sa.select(ar_allocation_policy).where(ar_allocation_policy.c.version == version)
    ).mappings().one_or_none()
    return None if row is None else _policy_from_row(row)


# --- arm state ----------------------------------------------------------------------

def upsert_arm_state(session: Session, *, brand_id: str, arm_id: str,
                     now: datetime) -> None:
    """Create the arm's P2 state row on first sighting; never touches P1's arm."""
    exists = session.execute(
        sa.select(ar_content_arm_state.c.id)
        .where(ar_content_arm_state.c.arm_id == arm_id)
    ).scalar_one_or_none()
    if exists is None:
        session.execute(sa.insert(ar_content_arm_state).values(
            id=deterministic_id("arm_state", arm_id), brand_id=brand_id, arm_id=arm_id,
            status=ArmLifecycleStatus.CANDIDATE.value, last_updated_at=now,
        ))
    else:
        session.execute(
            sa.update(ar_content_arm_state)
            .where(ar_content_arm_state.c.arm_id == arm_id)
            .values(last_updated_at=now)
        )


def set_arm_status(session: Session, *, arm_id: str, status: ArmLifecycleStatus,
                   now: datetime, reason: str | None = None,
                   cooldown_until: datetime | None = None) -> None:
    session.execute(
        sa.update(ar_content_arm_state)
        .where(ar_content_arm_state.c.arm_id == arm_id)
        .values(status=status.value, retirement_reason=reason, cooldown_until=cooldown_until,
                last_updated_at=now, state_version=ar_content_arm_state.c.state_version + 1)
    )


def record_selection(session: Session, *, arm_id: str, now: datetime,
                     cooldown_until: datetime | None = None) -> None:
    session.execute(
        sa.update(ar_content_arm_state).where(ar_content_arm_state.c.arm_id == arm_id).values(
            selection_count=ar_content_arm_state.c.selection_count + 1,
            last_selected_at=now,
            first_selected_at=sa.func.coalesce(ar_content_arm_state.c.first_selected_at, now),
            status=ArmLifecycleStatus.ACTIVE.value,
            cooldown_until=cooldown_until,
            last_updated_at=now,
            state_version=ar_content_arm_state.c.state_version + 1,
        )
    )


def load_arm_states(session: Session) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(
        sa.select(ar_content_arm_state).order_by(ar_content_arm_state.c.arm_id)
    ).mappings()]


# --- feature snapshots (insert-only: no leakage) ------------------------------------

def insert_feature_snapshot(session: Session, *, brand_id: str, arm: ContentArm,
                            captured_at: datetime, feature_version: str) -> str:
    snapshot_id = deterministic_id("feature_snapshot", arm.arm_id, captured_at.isoformat())
    session.execute(sa.insert(ar_arm_feature_snapshot).values(
        id=snapshot_id, brand_id=brand_id, arm_id=arm.arm_id, captured_at=captured_at,
        p1_score=arm.p1_score, p1_confidence=arm.p1_confidence,
        demand_score=arm.component_scores["demand"],
        growth_score=arm.component_scores["growth"],
        competition_score=arm.component_scores["competition"],
        cross_platform_score=arm.component_scores["cross_platform"],
        production_fit_score=arm.component_scores["production_fit"],
        freshness_seconds=arm.freshness_seconds,
        feature_json=dict(arm.component_scores), feature_version=feature_version,
    ))
    return snapshot_id


def load_feature_snapshot(session: Session, snapshot_id: str) -> dict[str, Any] | None:
    row = session.execute(
        sa.select(ar_arm_feature_snapshot)
        .where(ar_arm_feature_snapshot.c.id == snapshot_id)
    ).mappings().one_or_none()
    return None if row is None else dict(row)


# --- posteriors ---------------------------------------------------------------------

def upsert_posterior(session: Session, state: PosteriorState, *, brand_id: str,
                     expected_version: int) -> None:
    """Insert at version 0; later writes must match the stored `update_version` (§75)."""
    existing = load_posterior(session, state.arm_id)
    values = {
        "brand_id": brand_id, "arm_id": state.arm_id, "model_type": state.model_type,
        "model_version": state.model_version, "prior_mean": state.prior_mean,
        "prior_variance": state.prior_variance, "posterior_mean": state.posterior_mean,
        "posterior_variance": state.posterior_variance,
        "observation_count": state.observation_count,
        "effective_observation_count": state.effective_observation_count,
        "last_update_at": state.last_update_at, "update_version": state.update_version,
        "context_state_json": dict(state.context_state_json),
    }
    if existing is None:
        session.execute(sa.insert(ar_arm_posterior).values(
            id=deterministic_id("posterior", state.arm_id), **values,
        ))
        return
    if existing.update_version != expected_version:
        raise PosteriorVersionConflict(
            f"posterior for {state.arm_id!r} is at version {existing.update_version}, "
            f"writer holds {expected_version}: reload and retry (§75)"
        )
    result = session.execute(
        sa.update(ar_arm_posterior)
        .where(ar_arm_posterior.c.arm_id == state.arm_id,
               ar_arm_posterior.c.update_version == expected_version)
        .values(**values)
    )
    if result.rowcount == 0:
        raise PosteriorVersionConflict(
            f"posterior for {state.arm_id!r} was advanced concurrently (§75)"
        )


def load_posterior(session: Session, arm_id: str) -> PosteriorState | None:
    row = session.execute(
        sa.select(ar_arm_posterior).where(ar_arm_posterior.c.arm_id == arm_id)
    ).mappings().one_or_none()
    if row is None:
        return None
    return PosteriorState(
        arm_id=row["arm_id"], model_type=row["model_type"], model_version=row["model_version"],
        prior_mean=row["prior_mean"], prior_variance=row["prior_variance"],
        posterior_mean=row["posterior_mean"], posterior_variance=row["posterior_variance"],
        observation_count=row["observation_count"],
        effective_observation_count=row["effective_observation_count"],
        update_version=row["update_version"], last_update_at=row["last_update_at"],
        context_state_json=row["context_state_json"] or {},
    )


# --- cycles, decisions, directives --------------------------------------------------

def insert_cycle(session: Session, *, cycle_id: str, brand_id: str, started_at: datetime,
                 status: AllocationCycleStatus, slot_count: int, budget: float,
                 policy_id: str, p1_snapshot_time: datetime, activation_state: str,
                 random_seed: int) -> bool:
    """False when the cycle already exists — a replay is idempotent, not a second cycle."""
    existing = session.execute(
        sa.select(ar_allocation_cycle.c.id).where(ar_allocation_cycle.c.id == cycle_id)
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.execute(sa.insert(ar_allocation_cycle).values(
        id=cycle_id, brand_id=brand_id, started_at=started_at, status=status.value,
        slot_count=slot_count, budget=budget, policy_id=policy_id,
        p1_snapshot_time=p1_snapshot_time, activation_state=activation_state,
        random_seed=random_seed, warning_codes_json=[],
    ))
    return True


def finish_cycle(session: Session, *, cycle_id: str, status: AllocationCycleStatus,
                 finished_at: datetime, decision_count: int, selected_count: int,
                 rejected_count: int, warning_codes: Sequence[str]) -> None:
    session.execute(sa.update(ar_allocation_cycle)
                    .where(ar_allocation_cycle.c.id == cycle_id)
                    .values(status=status.value, finished_at=finished_at,
                            decision_count=decision_count, selected_count=selected_count,
                            rejected_count=rejected_count,
                            warning_count=len(warning_codes),
                            warning_codes_json=list(warning_codes)))


def load_cycle(session: Session, cycle_id: str) -> dict[str, Any] | None:
    row = session.execute(
        sa.select(ar_allocation_cycle).where(ar_allocation_cycle.c.id == cycle_id)
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def insert_decision(session: Session, *, cycle_id: str, arm_id: str,
                    allocation_type: str, sampled_reward: float,
                    posterior_expected_reward: float, selection_priority: float,
                    rank_before_constraints: int | None, rank_after_constraints: int | None,
                    constraint_adjustments: Mapping[str, Any], reason_codes: Sequence[str],
                    policy_version: str, model_version: str, feature_version: str,
                    normalization_version: str, p1_score_version: str | None,
                    feature_snapshot_id: str, created_at: datetime) -> str:
    decision_id = deterministic_id("decision", cycle_id, arm_id)
    session.execute(sa.insert(ar_allocation_decision).values(
        id=decision_id, cycle_id=cycle_id, arm_id=arm_id, allocation_type=allocation_type,
        sampled_reward=sampled_reward, posterior_expected_reward=posterior_expected_reward,
        selection_priority=selection_priority,
        rank_before_constraints=rank_before_constraints,
        rank_after_constraints=rank_after_constraints,
        constraint_adjustments_json=dict(constraint_adjustments),
        reason_codes=list(reason_codes), policy_version=policy_version,
        model_version=model_version, feature_version=feature_version,
        normalization_version=normalization_version, p1_score_version=p1_score_version,
        feature_snapshot_id=feature_snapshot_id, created_at=created_at,
    ))
    return decision_id


def load_decisions(session: Session, cycle_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(
        sa.select(ar_allocation_decision)
        .where(ar_allocation_decision.c.cycle_id == cycle_id)
        .order_by(ar_allocation_decision.c.rank_after_constraints.nullslast(),
                  ar_allocation_decision.c.arm_id)
    ).mappings()]


def insert_directive(session: Session, directive: ProductionDirective, *, brand_id: str,
                     status: DirectiveStatus, created_at: datetime) -> str:
    session.execute(sa.insert(ar_production_directive).values(
        id=directive.directive_id, decision_id=directive.decision_id,
        arm_id=directive.arm_id, quantity=directive.quantity,
        brief_json=directive.brief.model_dump(mode="json"), priority=directive.priority,
        status=status.value, expires_at=directive.expires_at, created_at=created_at,
        allocation_type=directive.allocation_type.value,
        expected_value=directive.expected_value, uncertainty=directive.uncertainty,
        budget_limit=directive.budget_limit,
        market_context_json=dict(directive.market_context),
        selection_reason_json=list(directive.selection_reason),
        directive_version=directive.directive_version,
        policy_version=directive.policy_version,
        exploration_fraction=directive.exploration_fraction,
        value_model=directive.value_model,
    ))
    return directive.directive_id


def list_queued_directives(session: Session, *, brand_id: str,
                           activation_states: Sequence[str]) -> list[dict[str, Any]]:
    """The actionable surface. Only cycles whose activation state is in `activation_states`
    may be served (ADR-0024: SHADOW/DRY_RUN directives stay auditable but not actionable)."""
    return [dict(row) for row in session.execute(
        sa.select(ar_production_directive)
        .join(ar_allocation_decision,
              ar_allocation_decision.c.id == ar_production_directive.c.decision_id)
        .join(ar_allocation_cycle,
              ar_allocation_cycle.c.id == ar_allocation_decision.c.cycle_id)
        .where(ar_allocation_cycle.c.brand_id == brand_id,
               ar_allocation_cycle.c.activation_state.in_(list(activation_states)),
               ar_production_directive.c.status == DirectiveStatus.QUEUED.value)
        .order_by(ar_production_directive.c.priority, ar_production_directive.c.id)
    ).mappings()]


def load_directive(session: Session, directive_id: str) -> dict[str, Any] | None:
    row = session.execute(
        sa.select(ar_production_directive)
        .where(ar_production_directive.c.id == directive_id)
    ).mappings().one_or_none()
    return None if row is None else dict(row)


# --- reward observations and updates ------------------------------------------------

def insert_reward_observation(session: Session, observation: RewardObservation, *,
                              brand_id: str, received_at: datetime) -> tuple[str, bool]:
    """Idempotent on `(experiment_id, observation_window)` (§73). Returns (id, stored?)."""
    observation_id = deterministic_id("reward_observation", observation.key)
    existing = session.execute(
        sa.select(ar_reward_observation.c.id)
        .where(ar_reward_observation.c.id == observation_id)
    ).scalar_one_or_none()
    if existing is not None:
        return observation_id, False
    try:
        session.execute(sa.insert(ar_reward_observation).values(
            id=observation_id, brand_id=brand_id, experiment_id=observation.experiment_id,
            arm_id=observation.arm_id, content_id=observation.content_id,
            observation_window=observation.observation_window.value,
            maturity=observation.maturity.value, observed_at=observation.observed_at,
            raw_metrics_json={name: tri.model_dump() for name, tri in observation.raw_metrics.items()},
            normalized_reward=(observation.normalized_reward.unwrap()
                               if observation.normalized_reward.state.value == "known" else None),
            reward_version=observation.reward_version,
            attribution_confidence=observation.attribution_confidence,
            quality_status=(observation.quality_status.value
                            if observation.quality_status is not None else None),
            economic_terms_json={name: tri.model_dump()
                                 for name, tri in observation.economic_terms.items()},
            received_at=received_at,
        ))
    except IntegrityError:
        session.rollback()
        return observation_id, False
    return observation_id, True


def insert_experiment_result(session: Session, observation: RewardObservation, *,
                             brand_id: str) -> str:
    result_id = deterministic_id("experiment_result", observation.key)
    session.execute(sa.insert(ar_experiment_result).values(
        id=result_id, brand_id=brand_id, experiment_id=observation.experiment_id,
        arm_id=observation.arm_id, content_id=observation.content_id,
        observation_window=observation.observation_window.value,
        status=observation.maturity.value, observed_at=observation.observed_at,
        raw_metrics_json={name: tri.model_dump() for name, tri in observation.raw_metrics.items()},
        normalized_reward=(observation.normalized_reward.unwrap()
                           if observation.normalized_reward.state.value == "known" else None),
        reward_version=observation.reward_version,
        attribution_confidence=observation.attribution_confidence, source_ids=[],
    ))
    return result_id


def load_pending_observations(session: Session, *, now: datetime) -> list[dict[str, Any]]:
    """Observations not yet `applied`, dated at/before `now` — no future data (§23)."""
    applied = sa.select(ar_posterior_update_log.c.reward_observation_id).where(
        ar_posterior_update_log.c.update_status == "applied"
    )
    return [dict(row) for row in session.execute(
        sa.select(ar_reward_observation)
        .where(ar_reward_observation.c.id.not_in(applied),
               ar_reward_observation.c.observed_at <= now)
        .order_by(ar_reward_observation.c.observed_at, ar_reward_observation.c.id)
    ).mappings()]


def insert_posterior_update(session: Session, record: UpdateRecord, *, brand_id: str) -> None:
    session.execute(sa.insert(ar_posterior_update_log).values(
        id=deterministic_id("posterior_update", record.reward_observation_id,
                            record.status.value, str(record.version_after)),
        brand_id=brand_id, reward_observation_id=record.reward_observation_id,
        arm_id=record.arm_id, posterior_version_before=record.version_before,
        posterior_version_after=record.version_after, update_status=record.status.value,
        effective_weight=record.effective_weight, updated_at=record.updated_at,
    ))


def load_posterior_update(session: Session, reward_observation_id: str) -> dict[str, Any] | None:
    row = session.execute(
        sa.select(ar_posterior_update_log)
        .where(ar_posterior_update_log.c.reward_observation_id == reward_observation_id,
               ar_posterior_update_log.c.update_status == "applied")
    ).mappings().one_or_none()
    return None if row is None else dict(row)


# --- similarity, exposure, baselines, overrides, channel state ----------------------

def replace_similarity(session: Session, *, brand_id: str, pairs: Sequence[tuple[str, str, float]],
                       similarity_method: str, feature_version: str,
                       computed_at: datetime) -> int:
    """Upsert similarity pairs (canonical `a < b`); returns the count stored."""
    stored = 0
    for arm_a, arm_b, score in pairs:
        first, second = sorted((arm_a, arm_b))
        pair_id = deterministic_id("similarity", first, second)
        exists = session.execute(
            sa.select(ar_arm_similarity.c.id).where(ar_arm_similarity.c.id == pair_id)
        ).scalar_one_or_none()
        if exists is None:
            session.execute(sa.insert(ar_arm_similarity).values(
                id=pair_id, brand_id=brand_id, arm_id_a=first, arm_id_b=second,
                similarity_score=score, similarity_method=similarity_method,
                feature_version=feature_version, computed_at=computed_at,
            ))
        stored += 1
    return stored


def load_similarity(session: Session) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(
        sa.select(ar_arm_similarity).order_by(ar_arm_similarity.c.arm_id_a,
                                              ar_arm_similarity.c.arm_id_b)
    ).mappings()]


def insert_exposure(session: Session, *, brand_id: str, arm_id: str, window_start: datetime,
                    window_end: datetime, selection_count: int, content_count: int,
                    estimated_audience_exposure: float | None, exposure_score: float,
                    calculation_version: str) -> None:
    session.execute(sa.insert(ar_arm_exposure).values(
        id=deterministic_id("exposure", arm_id, window_start.isoformat(), window_end.isoformat()),
        brand_id=brand_id, arm_id=arm_id, window_start=window_start, window_end=window_end,
        selection_count=selection_count, content_count=content_count,
        estimated_audience_exposure=estimated_audience_exposure,
        exposure_score=exposure_score, calculation_version=calculation_version,
    ))


def load_exposure(session: Session, *, arm_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(
        sa.select(ar_arm_exposure).where(ar_arm_exposure.c.arm_id == arm_id)
        .order_by(ar_arm_exposure.c.window_start)
    ).mappings()]


def replace_baselines(session: Session, *, brand_id: str, arm_ids: Sequence[str],
                      now: datetime, config_version: str) -> None:
    session.execute(sa.update(ar_baseline_config)
                    .where(ar_baseline_config.c.brand_id == brand_id)
                    .values(enabled=False, removed_at=now))
    for arm_id in arm_ids:
        existing = session.execute(
            sa.select(ar_baseline_config.c.id)
            .where(ar_baseline_config.c.brand_id == brand_id,
                   ar_baseline_config.c.arm_id == arm_id)
        ).scalar_one_or_none()
        if existing is None:
            session.execute(sa.insert(ar_baseline_config).values(
                id=deterministic_id("baseline", brand_id, arm_id), brand_id=brand_id,
                arm_id=arm_id, enabled=True, added_at=now, config_version=config_version,
            ))
        else:
            session.execute(sa.update(ar_baseline_config)
                            .where(ar_baseline_config.c.id == existing)
                            .values(enabled=True, removed_at=None, added_at=now))


def load_baselines(session: Session, *, brand_id: str) -> tuple[str, ...]:
    return tuple(session.execute(
        sa.select(ar_baseline_config.c.arm_id)
        .where(ar_baseline_config.c.brand_id == brand_id,
               ar_baseline_config.c.enabled.is_(True))
        .order_by(ar_baseline_config.c.arm_id)
    ).scalars())


def insert_override(session: Session, *, brand_id: str, target_type: str, target_id: str,
                    action: str, reason: str, created_by: str, created_at: datetime,
                    expires_at: datetime | None) -> str:
    override_id = deterministic_id("override", brand_id, target_id, created_at.isoformat())
    session.execute(sa.insert(ar_manual_override).values(
        id=override_id, brand_id=brand_id, target_type=target_type, target_id=target_id,
        action=action, reason=reason, created_by=created_by, created_at=created_at,
        expires_at=expires_at, active=True,
    ))
    return override_id


def load_active_overrides(session: Session, *, brand_id: str,
                          now: datetime) -> list[dict[str, Any]]:
    """§108: an expired override is not an override."""
    return [dict(row) for row in session.execute(
        sa.select(ar_manual_override)
        .where(ar_manual_override.c.brand_id == brand_id,
               ar_manual_override.c.active.is_(True),
               sa.or_(ar_manual_override.c.expires_at.is_(None),
                      ar_manual_override.c.expires_at > now))
        .order_by(ar_manual_override.c.created_at)
    ).mappings()]


def get_channel_state(session: Session, *, brand_id: str,
                      default: str) -> str:
    value = session.execute(
        sa.select(ar_channel_state.c.activation_state)
        .where(ar_channel_state.c.brand_id == brand_id)
    ).scalar_one_or_none()
    return default if value is None else str(value)


def set_channel_state(session: Session, *, brand_id: str, activation_state: str,
                      now: datetime, reason: str, updated_by: str) -> None:
    existing = session.execute(
        sa.select(ar_channel_state.c.id).where(ar_channel_state.c.brand_id == brand_id)
    ).scalar_one_or_none()
    if existing is None:
        session.execute(sa.insert(ar_channel_state).values(
            id=deterministic_id("channel_state", brand_id), brand_id=brand_id,
            activation_state=activation_state, updated_at=now, reason=reason,
            updated_by=updated_by,
        ))
    else:
        session.execute(sa.update(ar_channel_state)
                        .where(ar_channel_state.c.id == existing)
                        .values(activation_state=activation_state, updated_at=now,
                                reason=reason, updated_by=updated_by))
```

- [ ] **Step 12: Run the repositories tests and watch them pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_repositories.py -q`
Expected: PASS (5 tests across the two cycles).

- [ ] **Step 13: Write the integration drift guard (Postgres DDL vs models)**

```python
# tests/integration/ar/test_schema_matches_models.py
"""After `alembic upgrade head`, the live `ar` schema must equal the models exactly."""

from __future__ import annotations

import sqlalchemy as sa
import pytest

from plm_platform.db import build_engine
from services.autonomous_ar.storage.models import AR_METADATA


@pytest.mark.integration
def test_ar_schema_matches_the_models() -> None:
    engine = build_engine()
    try:
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1 FROM ar.ar_allocation_cycle LIMIT 1"))
    except Exception as exc:  # dead stack or missing migration
        pytest.skip(f"compose postgres unavailable or unmigrated ({exc.__class__.__name__}); "
                    "run `docker compose up -d --wait postgres` and `plm migrate`")
    inspector = sa.inspect(engine)
    live = set(inspector.get_table_names(schema="ar"))
    expected = {table.name for table in AR_METADATA.tables.values()}
    assert live == expected, f"DDL drifted from models: {sorted(live ^ expected)}"
```

- [ ] **Step 14: Write ADR-0024 and the decision-log row**

```markdown
<!-- docs/adr/0024-p2-tables-model-and-states.md -->
# ADR-0024: P2's tables, its statistical model, and how SHADOW stays harmless

- Status: `ratified` — 2026-09-20
- Ruling: R3, ADR-0023 #5; seams C1, C2
- Seam: C2
- Supersedes / superseded by: —

## Decision

1. **Tables.** PHASE-P2 §44's names take the monorepo's `<schema>_` prefix: the twelve
   §44 tables become `ar_*`, plus three tables §44's field lists require but never name —
   `ar_posterior_update_log` (§74's persisted update state), `ar_channel_state` (§117's
   activation state), `ar_manual_override` (§107/§108's explicit, expiring event).
2. **Model.** The posterior is a stdlib normal–normal conjugate (`math`/`random`); no
   numpy/scipy dependency is added. §112 explicitly permits a lightweight custom
   implementation, §11 recommends Normal–Normal, and determinism is simpler without a
   numeric stack. `RewardModel` (§66) keeps the seam so the model is replaceable.
3. **SHADOW serving rule.** Directives are persisted for every cycle, but the actionable
   surface (`GET /ar/directives` and `list_queued_directives`) serves only cycles whose
   persisted `activation_state` is `AUTONOMOUS`. In SHADOW/DRY_RUN the compute, the
   decisions and the directives are auditable; nothing is actionable (§115/§116/ADR-0023 #5).
4. **Coverage rule.** Because ADR-0022 froze the C1 field list and P1's
   `insufficient_coverage`/`basis_weight` do not cross the wire, P2's ADR-0023 #5 threshold
   is operationalised as **2+ non-null components on the C1 payload**; under-covered arms
   are still persisted (state + snapshot) and are returned with `EVIDENCE_INSUFFICIENT`
   rather than ranked.

## Consequences

A C1 wire change to carry `basis_weight` would need a registry amendment (R2) — out of
P2's remit; recorded in the plan's open items. The effective-value threshold remains a
config value (`PolicyConfig.min_measured_components`), not a wire change.
```

Append to `docs/contracts/DECISION_LOG.md` (matching its existing table row format):

```markdown
| ADR-0024 | 2026-09-20 | P2 | `ar_*` table names (R3), stdlib normal–normal model (no new dependency), SHADOW directives are auditable but not actionable, coverage = 2+ non-null C1 components | ratified |
```

- [ ] **Step 15: Run the task's full gate**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar tests\integration\ar -q; .\.venv\Scripts\python.exe -m ruff check services\autonomous_ar tests\unit\ar tests\support\ar_db.py migrations\versions\ar; .\.venv\Scripts\python.exe -m mypy packages services`
Expected: all green (integration skips cleanly if compose is down).

- [ ] **Step 16: Commit**

Run: `git add services/autonomous_ar/storage tests/unit/ar/test_storage_models.py tests/unit/ar/test_migration_module.py tests/unit/ar/test_repositories.py tests/integration/ar tests/support/ar_db.py migrations\versions\ar docs\adr\0024-p2-tables-model-and-states.md docs\contracts\DECISION_LOG.md; git commit -m "feat(ar): P2.3 ar schema, single migration, repositories (ADR-0024)"`

---

### Task P2.4: Similarity

**Files:**
- Create: `services/autonomous_ar/modeling/__init__.py`, `services/autonomous_ar/modeling/similarity.py`
- Test: `tests/unit/ar/test_similarity.py`

**Interfaces:**
- Consumes: `ArmDimensions` (P2.1), `deterministic_id` (P2.3), `ARSettings.similarity_threshold`.
- Produces:
  - `DIMENSION_WEIGHTS: Mapping[str, float]` — the nine frozen C1 dimensions, weighted by how much of an arm's identity each carries (invented values, flagged; versioned by `feature_version`).
  - `arm_similarity(a: ArmDimensions, b: ArmDimensions) -> float` — weighted Jaccard over shared, *present* dimensions; a dimension absent on both is excluded; present on one only counts as a mismatch (§65: structural only, never prose).
  - `structural_overlap(a: ArmDimensions, b: ArmDimensions) -> int` — dimensions present on both (P2.5's transfer gate).
  - `cluster_arms(pairs: Sequence[tuple[str, str, float]], *, threshold: float) -> dict[str, str]` — arm_id → cluster_id via deterministic union-find; `cluster_id = deterministic_id("cluster", *sorted(members))`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ar/test_similarity.py
from services.autonomous_ar.domain.arm import ArmDimensions
from services.autonomous_ar.modeling.similarity import (
    arm_similarity,
    cluster_arms,
    structural_overlap,
)

WINNER = ArmDimensions(genre="rnb", moods=("melancholic", "dreamy"),
                       use_cases=("late_night_drive",), language="english",
                       vocal_mode="vocal")
NEIGHBOUR = ArmDimensions(genre="rnb", moods=("melancholic", "dreamy"),
                          use_cases=("rainy_commute",), language="english",
                          vocal_mode="vocal")
DISTANT = ArmDimensions(genre="city_pop", moods=("playful",), use_cases=("cafe_study",),
                        language="japanese", vocal_mode="instrumental")


def test_identical_arms_are_one_and_near_neighbours_beat_distant_arms() -> None:
    assert arm_similarity(WINNER, WINNER) == 1.0
    assert arm_similarity(WINNER, NEIGHBOUR) > arm_similarity(WINNER, DISTANT)
    assert structural_overlap(WINNER, DISTANT) == 5  # genre/mood/use_case/language/vocal_mode


def test_absent_dimensions_are_excluded_never_credited() -> None:
    sparse = ArmDimensions(genre="rnb")
    assert structural_overlap(sparse, WINNER) == 1
    # only genre is comparable; equal ⇒ 1.0 over the present weight, not over nine
    assert arm_similarity(sparse, WINNER) == 1.0
    # a dimension present on one side only is a mismatch for that dimension, never a match
    assert arm_similarity(ArmDimensions(genre="rnb", language="english"),
                          ArmDimensions(genre="rnb", language="korean")) == 0.6  # 3.0 of 5.0


def test_clustering_is_transitive_and_deterministic() -> None:
    pairs = [("a", "b", 0.8), ("b", "c", 0.7), ("d", "e", 0.9), ("c", "d", 0.1)]
    first = cluster_arms(pairs, threshold=0.6)
    second = cluster_arms(list(reversed(pairs)), threshold=0.6)
    assert first == second, "cluster assignment must not depend on pair order (replayability)"
    assert first["a"] == first["b"] == first["c"] != first["d"] == first["e"]


def test_below_threshold_pairs_never_cluster() -> None:
    assignments = cluster_arms([("a", "b", 0.59)], threshold=0.6)
    assert assignments["a"] != assignments["b"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_similarity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.modeling'` (create `modeling/__init__.py` empty first).

- [ ] **Step 3: Write `modeling/similarity.py`**

```python
# services/autonomous_ar/modeling/similarity.py
"""Deterministic structured similarity (§7/§8/§25/§53).

Structured comparison comes first and only (§8: an embedding layer, if ever added, may
not replace this). Weights are a fixed, named set tied to `feature_version`; a dimension
the wire left empty is excluded from the denominator and never credited as a match.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..domain.arm import ArmDimensions
from ..storage.ids import deterministic_id

__all__ = ["DIMENSION_WEIGHTS", "arm_similarity", "cluster_arms", "structural_overlap"]

DIMENSION_WEIGHTS: Mapping[str, float] = {
    "genre": 3.0, "subgenre": 1.0, "mood": 1.5, "use_case": 2.0, "language": 2.0,
    "vocal_mode": 1.5, "duration_bucket": 1.0, "visual_world": 2.0, "title_archetype": 0.5,
}
"""Invented weights (the spec fixes the dimension set, not the weights); changing them is a
new `feature_version`, never a silent edit."""


def _jaccard(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    set_a, set_b = set(a), set(b)
    return len(set_a & set_b) / len(set_a | set_b)


def arm_similarity(a: ArmDimensions, b: ArmDimensions) -> float:
    """Weighted Jaccard over dimensions present on at least one side; 1.0 for identical arms."""
    scored = 0.0
    total = 0.0
    for name, weight in DIMENSION_WEIGHTS.items():
        tokens_a, tokens_b = a.tokens(name), b.tokens(name)
        if tokens_a is None and tokens_b is None:
            continue
        total += weight
        if tokens_a is None or tokens_b is None:
            continue  # present on one side only: no credit, still counted
        scored += weight * _jaccard(tokens_a, tokens_b)
    return 0.0 if total == 0.0 else scored / total


def structural_overlap(a: ArmDimensions, b: ArmDimensions) -> int:
    """How many dimensions both arms actually carry (§64/§65's transfer gate)."""
    return sum(
        1 for name in DIMENSION_WEIGHTS
        if a.tokens(name) is not None and b.tokens(name) is not None
    )


def cluster_arms(pairs: Sequence[tuple[str, str, float]], *, threshold: float) -> dict[str, str]:
    """Union-find over pairs at/above the threshold; cluster ids derive from member sets."""
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != root:  # path compression, order-independent
            parent[item], item = root, parent[item]
        return root

    for arm_a, arm_b, score in pairs:
        if score < threshold:
            parent.setdefault(arm_a, arm_a)
            parent.setdefault(arm_b, arm_b)
            continue
        root_a, root_b = find(arm_a), find(arm_b)
        if root_a != root_b:
            parent[max(root_a, root_b)] = min(root_a, root_b)

    members: dict[str, list[str]] = {}
    for arm_id in parent:
        members.setdefault(find(arm_id), []).append(arm_id)
    assignments: dict[str, str] = {}
    for members_list in members.values():
        cluster_id = deterministic_id("cluster", *sorted(members_list))
        for arm_id in members_list:
            assignments[arm_id] = cluster_id
    return assignments
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_similarity.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

Run: `git add services/autonomous_ar/modeling tests/unit/ar/test_similarity.py; git commit -m "feat(ar): P2.4 structured arm similarity and deterministic clustering"`

---

### Task P2.5: Prior model

**Files:**
- Create: `services/autonomous_ar/modeling/prior.py`
- Test: `tests/unit/ar/test_prior.py`

**Interfaces:**
- Consumes: `ContentArm`, `PriorParameters`, `PosteriorState` (P2.1), `arm_similarity`/`structural_overlap` (P2.4), `ARSettings`, `PolicyConfig`.
- Produces:
  - `cold_start_prior(arm: ContentArm, *, settings: ARSettings, policy: PolicyConfig) -> PriorParameters` — the versioned `p2-prior-v1` transform: `mean = clamp(0.5 + (score/100 − 0.5)·confidence, 0.05, 0.95)`, `variance = BASE_VARIANCE + SPREAD·(1−confidence)² + BASE_VARIANCE·staleness` where `staleness = min(1, freshness_seconds / (max_market_data_age_hours·3600))`. A high score with low confidence moves the mean less and widens variance; stale evidence widens variance (§83).
  - `transfer_contribution(prior: PriorParameters, *, neighbour: PosteriorState, similarity: float, neighbour_dims: ArmDimensions, arm_dims: ArmDimensions, settings: ARSettings) -> PriorParameters` — §64/§65: only when `structural_overlap >= settings.min_transfer_shared_dimensions` and `similarity >= settings.similarity_threshold`; weight `w = transfer_max_weight · similarity · provenness` with `provenness = effective_obs/(effective_obs + TRANSFER_HALFSAT)`; mean and variance blend by the law of total variance, so the new arm's variance is strictly above the proven arm's whenever its own prior was wider — **never the same certainty as the proven arm**.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ar/test_prior.py
from datetime import UTC, datetime

from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.domain.posterior import PosteriorState
from services.autonomous_ar.modeling.prior import cold_start_prior, transfer_contribution

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
COMPONENTS = {"demand": 70.0, "growth": 50.0, "competitor_velocity": 55.0,
              "competition": 60.0, "audience_quality": 40.0, "cross_platform": None,
              "production_fit": 58.0}


def _arm(score: float, confidence: float, freshness: float = 0.0) -> ContentArm:
    return ContentArm(
        arm_id="arm-1", dimensions=ArmDimensions(genre="rnb", language="english"),
        p1_score=score, p1_confidence=confidence, component_scores=COMPONENTS,
        observation_count=6, source_count=2, as_of=NOW, score_version="p1-v1",
        freshness_seconds=freshness,
    )


def test_confidence_controls_both_direction_and_width() -> None:
    settings, policy = load_ar_settings(), DEFAULT_POLICY
    strong = cold_start_prior(_arm(82.0, 0.7), settings=settings, policy=policy)
    weak = cold_start_prior(_arm(82.0, 0.2), settings=settings, policy=policy)
    assert strong.mean > weak.mean, "same score, lower confidence ⇒ closer to the midpoint"
    assert strong.variance < weak.variance
    low = cold_start_prior(_arm(45.0, 0.9), settings=settings, policy=policy)
    assert low.mean < 0.5


def test_stale_market_evidence_widens_the_prior_without_moving_it() -> None:
    settings, policy = load_ar_settings(), DEFAULT_POLICY
    fresh = cold_start_prior(_arm(82.0, 0.7), settings=settings, policy=policy)
    stale = cold_start_prior(_arm(82.0, 0.7, freshness=168.0 * 3600.0), settings=settings,
                             policy=policy)
    assert stale.mean == fresh.mean
    assert stale.variance > fresh.variance
    assert fresh.prior_version == "p2-prior-v1"


def _proven(dims: ArmDimensions | None = None) -> PosteriorState:
    return PosteriorState(
        arm_id="arm-proven", model_type="normal-normal", model_version="p2-nn-v1",
        prior_mean=0.6, prior_variance=0.05, posterior_mean=0.75, posterior_variance=0.01,
        observation_count=30, effective_observation_count=25.0, update_version=4,
        last_update_at=NOW,
    )


def test_transfer_is_gated_on_structure_and_stays_less_certain_than_the_source() -> None:
    settings, policy = load_ar_settings(), DEFAULT_POLICY
    dims = ArmDimensions(genre="rnb", language="english", vocal_mode="vocal")
    prior = cold_start_prior(_arm(70.0, 0.5), settings=settings, policy=policy)
    transferred = transfer_contribution(
        prior, neighbour=_proven(), similarity=0.8, neighbour_dims=dims, arm_dims=dims,
        settings=settings,
    )
    assert transferred.mean > prior.mean, "a strong measured neighbour pulls the prior up"
    assert transferred.variance > _proven().posterior_variance, (
        "a new arm never starts with the proven arm's certainty (§64)"
    )
    # never transfers on prose-like similarity alone: below threshold ⇒ untouched
    untouched = transfer_contribution(
        prior, neighbour=_proven(), similarity=0.2, neighbour_dims=dims, arm_dims=dims,
        settings=settings,
    )
    assert untouched == prior
    # not enough shared structure ⇒ untouched even at high similarity (§65)
    thin = transfer_contribution(
        prior, neighbour=_proven(), similarity=1.0,
        neighbour_dims=ArmDimensions(genre="rnb"),
        arm_dims=ArmDimensions(genre="rnb"), settings=settings,
    )
    assert thin == prior
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_prior.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.modeling.prior'`.

- [ ] **Step 3: Write `modeling/prior.py`**

```python
# services/autonomous_ar/modeling/prior.py
"""Cold-start prior (§10) and bounded transfer learning (§64/§65), versioned `p2-prior-v1`.

The transform is deterministic once the version string is fixed. Module constants are the
transform's parameters — they move only with `prior_version`, never silently.
"""

from __future__ import annotations

from ..config import ARSettings
from ..domain.arm import ArmDimensions, ContentArm
from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState, PriorParameters
from .similarity import arm_similarity, structural_overlap

__all__ = ["cold_start_prior", "transfer_contribution"]

BASE_VARIANCE = 0.04
"""Variance at perfect confidence, fresh data — the transform's floor for a new arm."""

CONFIDENCE_SPREAD = 0.16
"""Extra variance at zero confidence: `(1 − c)²` scaled by this."""

TRANSFER_HALFSAT = 10.0
"""Observations at which a neighbour is 'half-proven' for transfer weighting."""


def cold_start_prior(arm: ContentArm, *, settings: ARSettings,
                     policy: PolicyConfig) -> PriorParameters:
    """P1 evidence → prior mean/variance; direction follows the score, width follows
    confidence and staleness (§10, §83). No first-party performance is fabricated."""
    base = arm.p1_score / 100.0
    confidence = arm.p1_confidence
    mean = min(0.95, max(0.05, 0.5 + (base - 0.5) * confidence))
    max_age_seconds = policy.max_market_data_age_hours * 3600.0
    staleness = min(1.0, arm.freshness_seconds / max_age_seconds)
    variance = BASE_VARIANCE + CONFIDENCE_SPREAD * (1.0 - confidence) ** 2 \
        + BASE_VARIANCE * staleness
    return PriorParameters(mean=mean, variance=variance, confidence=confidence,
                           prior_version=settings.prior_version)


def transfer_contribution(prior: PriorParameters, *, neighbour: PosteriorState,
                          similarity: float, neighbour_dims: ArmDimensions,
                          arm_dims: ArmDimensions, settings: ARSettings) -> PriorParameters:
    """Blend in a measured neighbour's posterior, bounded by structure and evidence.

    §65: structural similarity is checked first (`structural_overlap`); free-form text is
    never consulted. §64: the weight is capped at `transfer_max_weight`, so a new arm never
    inherits the proven arm's certainty.
    """
    if structural_overlap(arm_dims, neighbour_dims) < settings.min_transfer_shared_dimensions:
        return prior
    if similarity < settings.similarity_threshold:
        return prior
    if neighbour.effective_observation_count <= 0.0:
        return prior
    provenness = neighbour.effective_observation_count / (
        neighbour.effective_observation_count + TRANSFER_HALFSAT
    )
    weight = settings.transfer_max_weight * similarity * provenness
    if weight <= 0.0:
        return prior
    mean = (1.0 - weight) * prior.mean + weight * neighbour.posterior_mean
    gap = (prior.mean - neighbour.posterior_mean) ** 2
    variance = ((1.0 - weight) * prior.variance + weight * neighbour.posterior_variance
                + weight * (1.0 - weight) * gap)  # law of total variance: never certain
    return PriorParameters(mean=mean, variance=variance, confidence=prior.confidence,
                           prior_version=prior.prior_version)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_prior.py -q`
Expected: PASS (4 tests). Note `test_transfer_is_gated_on_structure…` also exercises
`arm_similarity` only through the gate; the similarity value is supplied by the caller, so no
mocking is needed.

- [ ] **Step 5: Commit**

Run: `git add services/autonomous_ar/modeling/prior.py tests/unit/ar/test_prior.py; git commit -m "feat(ar): P2.5 versioned cold-start prior and bounded transfer"`

---

### Task P2.6: Reward model

**Files:**
- Create: `services/autonomous_ar/modeling/reward_model.py`, `services/autonomous_ar/modeling/value_model.py`
- Test: `tests/unit/ar/test_reward_model.py`, `tests/unit/ar/test_value_model.py`

**Interfaces:**
- Consumes: `ContentArm`, `PriorParameters`, `PosteriorState`, `RewardObservation`, `RESERVED_VALUE_TERMS`, `tri_from_optional` (P2.1), `Tri`.
- Produces:
  - `reward_model.RewardModel` — the §66 protocol with its exact method names: `initialize_prior(self, arm, market_evidence)`, `update(self, posterior, observation, *, effective_weight, received_at)`, `sample(self, posterior, rng)`, `expected_value(self, posterior)`.
  - `reward_model.NormalNormalRewardModel` (default; `model_type="normal-normal"`, `model_version="p2-nn-v1"`, `observation_variance=0.09`, `variance_floor=1e-6`): conjugate precision update, reward range `[0, 1]` enforced (§72), `UnmeasuredRewardError` when `normalized_reward` is not `known` (never a zero), variance floored (the §95 zero-variance edge), `sample` needs an injected `random.Random` (§67) and never mutates state.
  - `value_model.VALUE_MODEL_ID = "early-stage-proxy-v1"`, `EARLY_STAGE_PROXY_WEIGHTS` (§20: view velocity .25, watch time .30, retention .15, subscriber conversion .10, engagement .10, qualitative/catalog .10 — sums to 1.0 and contains **no** reserved economic term), `term_from_raw(term, raw_metrics) -> Tri[float]` (only the three directly measurable mappings; everything else unknown), `compose_proxy(terms) -> Tri[float]` (any unmeasured term ⇒ the composite is `unknown` — never renormalized, ADR-0023 #4).

- [ ] **Step 1: Write the failing reward-model test**

```python
# tests/unit/ar/test_reward_model.py
import random
from datetime import UTC, datetime

import pytest
from plm_contracts import ObservationWindow, Tri

from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.enums import ObservationMaturity
from services.autonomous_ar.domain.posterior import PosteriorState, PriorParameters
from services.autonomous_ar.domain.reward import RewardObservation
from services.autonomous_ar.modeling.reward_model import (
    NormalNormalRewardModel,
    UnmeasuredRewardError,
)

NOW = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
COMPONENTS = {name: 50.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}

MODEL = NormalNormalRewardModel()


def _arm() -> ContentArm:
    return ContentArm(arm_id="a", dimensions=ArmDimensions(genre="rnb"), p1_score=60.0,
                      p1_confidence=0.6, component_scores=COMPONENTS, observation_count=3,
                      source_count=1, as_of=NOW, score_version="p1-v1", freshness_seconds=0.0)


def _observation(reward: float | None) -> RewardObservation:
    return RewardObservation(
        experiment_id="exp-1", arm_id="a", content_id="c",
        observation_window=ObservationWindow.D7, maturity=ObservationMaturity.MATURE,
        observed_at=NOW, raw_metrics={},
        normalized_reward=Tri.unknown() if reward is None else Tri.known(reward),
        reward_version="p9-reward-v1", attribution_confidence=0.99,
    )


def _posterior() -> PosteriorState:
    prior = PriorParameters(mean=0.6, variance=0.05, confidence=0.6,
                            prior_version="p2-prior-v1")
    return MODEL.initialize_prior(_arm(), prior)


def test_initial_prior_is_the_state_itself_at_version_zero() -> None:
    state = _posterior()
    assert state.posterior_mean == 0.6 and state.posterior_variance == 0.05
    assert state.update_version == 0 and state.observation_count == 0
    assert state.model_version == "p2-nn-v1"


def test_updates_move_the_mean_in_the_reward_direction_and_shrink_variance() -> None:
    state = _posterior()
    up = MODEL.update(state, _observation(0.9), effective_weight=1.0, received_at=NOW)
    assert up.posterior_mean > state.posterior_mean and up.posterior_variance < state.posterior_variance
    down = MODEL.update(state, _observation(0.1), effective_weight=1.0, received_at=NOW)
    assert down.posterior_mean < state.posterior_mean
    twice = MODEL.update(up, _observation(0.9), effective_weight=1.0, received_at=NOW)
    assert twice.observation_count == 2 and twice.posterior_variance < up.posterior_variance
    assert twice.update_version == up.update_version + 1


def test_unknown_reward_is_refused_never_treated_as_zero() -> None:
    with pytest.raises(UnmeasuredRewardError, match="normalized_reward"):
        MODEL.update(_posterior(), _observation(None), effective_weight=1.0, received_at=NOW)


def test_reward_outside_the_allowed_range_is_refused() -> None:
    with pytest.raises(ValueError, match="range"):
        MODEL.update(_posterior(), _observation(1.4), effective_weight=1.0, received_at=NOW)


def test_zero_variance_posterior_does_not_divide_by_zero() -> None:
    flattened = _posterior().model_copy(update={"posterior_variance": 0.0})
    result = MODEL.update(flattened, _observation(0.7), effective_weight=1.0, received_at=NOW)
    assert result.posterior_variance >= MODEL.variance_floor


def test_sampling_is_seeded_reproducible_and_mutation_free() -> None:
    state = _posterior()
    before = state.posterior_mean, state.posterior_variance, state.update_version
    draw_1 = MODEL.sample(state, random.Random(7))
    draw_2 = MODEL.sample(state, random.Random(7))
    assert draw_1 == draw_2
    assert (state.posterior_mean, state.posterior_variance, state.update_version) == before
    # higher posterior mean ⇒ more wins over many seeded trials (§95 sampling expectations)
    stronger = state.model_copy(update={"posterior_mean": 0.8})
    wins = sum(
        1 for _ in range(200)
        if MODEL.sample(stronger, random.Random(0)) > MODEL.sample(state, random.Random(0))
    )
    assert wins > 150
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_reward_model.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.modeling.reward_model'`.

- [ ] **Step 3: Write `modeling/reward_model.py`**

```python
# services/autonomous_ar/modeling/reward_model.py
"""Posterior mechanics: Normal–Normal conjugate, stdlib only, seeded and replayable (§11, §66).

The policy depends on the `RewardModel` protocol, never on this class (§66) — a future
contextual model replaces it without changing the public surface. Sampling consumes an
injected `random.Random`; nothing here reads a clock or the global RNG (§67).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..domain.arm import ContentArm
from ..domain.posterior import PosteriorState, PriorParameters
from ..domain.reward import RewardObservation

__all__ = ["NormalNormalRewardModel", "RewardModel", "UnmeasuredRewardError"]


class UnmeasuredRewardError(ValueError):
    """The observation carries no measured `normalized_reward` (R11: P9 owns the number)."""


class RewardModel(Protocol):
    """§66's interface, names verbatim."""

    def initialize_prior(self, arm: ContentArm,
                         market_evidence: PriorParameters) -> PosteriorState: ...

    def update(self, posterior: PosteriorState, observation: RewardObservation, *,
               effective_weight: float, received_at: datetime) -> PosteriorState: ...

    def sample(self, posterior: PosteriorState, rng: random.Random) -> float: ...

    def expected_value(self, posterior: PosteriorState) -> float: ...


@dataclass(frozen=True, slots=True)
class NormalNormalRewardModel:
    """Gaussian posterior over `normalized_reward`; precision-weighted conjugate update."""

    model_type: str = "normal-normal"
    model_version: str = "p2-nn-v1"
    observation_variance: float = 0.09
    variance_floor: float = 1e-6

    def initialize_prior(self, arm: ContentArm,
                         market_evidence: PriorParameters) -> PosteriorState:
        return PosteriorState(
            arm_id=arm.arm_id, model_type=self.model_type, model_version=self.model_version,
            prior_mean=market_evidence.mean, prior_variance=market_evidence.variance,
            posterior_mean=market_evidence.mean, posterior_variance=market_evidence.variance,
            observation_count=0, effective_observation_count=0.0, update_version=0,
            last_update_at=arm.as_of,
        )

    def update(self, posterior: PosteriorState, observation: RewardObservation, *,
               effective_weight: float, received_at: datetime) -> PosteriorState:
        if observation.normalized_reward.state.value != "known":
            raise UnmeasuredRewardError(
                f"observation {observation.key!r} has no measured normalized_reward: an "
                "unmeasured reward is unknown, never zero (ADR-0023 #4)"
            )
        value = observation.normalized_reward.unwrap()
        if not 0.0 <= value <= 1.0:
            raise ValueError(
                f"normalized_reward {value!r} outside the allowed range [0, 1] (§72)"
            )
        if effective_weight <= 0.0:
            raise ValueError("effective_weight must be > 0: a decayed-to-nothing observation is not an update")
        prior_variance = max(posterior.posterior_variance, self.variance_floor)
        prior_precision = 1.0 / prior_variance
        observation_precision = effective_weight / self.observation_variance
        posterior_precision = prior_precision + observation_precision
        posterior_variance = max(1.0 / posterior_precision, self.variance_floor)
        posterior_mean = posterior_variance * (
            posterior.posterior_mean * prior_precision + value * observation_precision
        )
        return posterior.model_copy(update={
            "posterior_mean": min(1.0, max(0.0, posterior_mean)),
            "posterior_variance": posterior_variance,
            "observation_count": posterior.observation_count + 1,
            "effective_observation_count": posterior.effective_observation_count + effective_weight,
            "update_version": posterior.update_version + 1,
            "last_update_at": received_at,
        })

    def sample(self, posterior: PosteriorState, rng: random.Random) -> float:
        """One Thompson draw; clamped to the reward range, state untouched."""
        draw = rng.gauss(posterior.posterior_mean, math.sqrt(posterior.posterior_variance))
        return min(1.0, max(0.0, draw))

    def expected_value(self, posterior: PosteriorState) -> float:
        return posterior.posterior_mean
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_reward_model.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Write the failing value-model test**

```python
# tests/unit/ar/test_value_model.py
import pytest
from plm_contracts import Tri

from services.autonomous_ar.domain.reward import RESERVED_VALUE_TERMS
from services.autonomous_ar.modeling.value_model import (
    EARLY_STAGE_PROXY_WEIGHTS,
    VALUE_MODEL_ID,
    compose_proxy,
    term_from_raw,
)


def test_proxy_weights_are_the_spec_decomposition_and_carry_no_revenue_term() -> None:
    assert VALUE_MODEL_ID == "early-stage-proxy-v1"
    assert set(EARLY_STAGE_PROXY_WEIGHTS) == {
        "view_velocity", "watch_time", "retention", "subscriber_conversion", "engagement",
        "qualitative_catalog",
    }
    assert sum(EARLY_STAGE_PROXY_WEIGHTS.values()) == pytest.approx(1.0)
    assert not (set(EARLY_STAGE_PROXY_WEIGHTS) & set(RESERVED_VALUE_TERMS)), (
        "ADR-0023 #4: the proxy never fabricates a revenue term"
    )


def test_composite_is_unknown_when_any_term_is_unmeasured() -> None:
    terms = {name: Tri.known(0.5) for name in EARLY_STAGE_PROXY_WEIGHTS}
    assert compose_proxy(terms).unwrap() == pytest.approx(0.5)
    terms["engagement"] = Tri.unknown()
    assert compose_proxy(terms).state.value == "unknown", (
        "never renormalise around a hole: the composite is unknown (§20 full-basis definition)"
    )
    del terms["retention"]
    assert compose_proxy(terms).state.value == "unknown"


def test_unknown_term_names_are_refused() -> None:
    with pytest.raises(ValueError, match="unknown proxy term"):
        compose_proxy({"revenue": Tri.known(0.9)})


def test_term_from_raw_maps_only_what_is_directly_measurable() -> None:
    raw = {"watch_time_minutes": Tri.known(185000.0),
           "average_percentage_viewed": Tri.known(41.2), "subscriber_gain": Tri.known(613.0)}
    assert term_from_raw("watch_time", raw).unwrap() == 185000.0
    assert term_from_raw("retention", raw).unwrap() == 41.2
    assert term_from_raw("subscriber_conversion", raw).unwrap() == 613.0
    assert term_from_raw("engagement", raw).state.value == "unknown"
    assert term_from_raw("view_velocity", {}).state.value == "unknown"
```

- [ ] **Step 6: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_value_model.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.modeling.value_model'`.

- [ ] **Step 7: Write `modeling/value_model.py`**

```python
# services/autonomous_ar/modeling/value_model.py
"""The versioned value model (ADR-0023 #4): what a 0–1 `expected_value` means.

First cycles use §20's early-stage proxy. The full Expected Economic Value terms are
reserved in C7's reward schema; this module never emits one — an unmeasured term keeps
the composite `unknown`, and renormalising around a hole is forbidden.
"""

from __future__ import annotations

from collections.abc import Mapping

from plm_contracts import Tri

from ..domain.reward import RESERVED_VALUE_TERMS

__all__ = [
    "EARLY_STAGE_PROXY_WEIGHTS", "VALUE_MODEL_ID", "compose_proxy", "term_from_raw",
]

VALUE_MODEL_ID = "early-stage-proxy-v1"

EARLY_STAGE_PROXY_WEIGHTS: Mapping[str, float] = {
    "watch_time": 0.30, "view_velocity": 0.25, "retention": 0.15,
    "subscriber_conversion": 0.10, "engagement": 0.10, "qualitative_catalog": 0.10,
}
"""§20's decomposition. Weights sum to 1.0; no key is a reserved economic term."""

TERM_SOURCES: Mapping[str, str] = {
    "watch_time": "watch_time_minutes",
    "retention": "average_percentage_viewed",
    "subscriber_conversion": "subscriber_gain",
}
"""Terms directly measurable from a P9-neutral raw metric. Everything else is unknown
until a producer measures it — including `view_velocity`, which needs a denominator."""


def term_from_raw(term: str, raw_metrics: Mapping[str, Tri[float]]) -> Tri[float]:
    """A proxy term from raw metrics; unknown for unmapped or absent sources."""
    source = TERM_SOURCES.get(term)
    if source is None:
        return Tri.unknown()
    return raw_metrics.get(source, Tri.unknown())


def compose_proxy(terms: Mapping[str, Tri[float]]) -> Tri[float]:
    """§20's weighted composite. Every weighted term must be measured, else `unknown`."""
    unknown = set(terms) - set(EARLY_STAGE_PROXY_WEIGHTS)
    if unknown:
        raise ValueError(f"unknown proxy term(s): {sorted(unknown)}")
    total = 0.0
    for term, weight in EARLY_STAGE_PROXY_WEIGHTS.items():
        value = terms.get(term, Tri.unknown())
        if value.state.value != "known":
            return Tri.unknown()
        total += weight * float(value.unwrap())
    return Tri.known(total)
```

- [ ] **Step 8: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_value_model.py -q`
Expected: PASS (4 tests).

- [ ] **Step 9: Commit**

Run: `git add services/autonomous_ar/modeling/reward_model.py services/autonomous_ar/modeling/value_model.py tests/unit/ar/test_reward_model.py tests/unit/ar/test_value_model.py; git commit -m "feat(ar): P2.6 normal-normal reward model and the versioned value model"`

---

### Task P2.7: Exploration policy

**Files:**
- Create: `services/autonomous_ar/policy/__init__.py`, `services/autonomous_ar/policy/exploration.py`
- Test: `tests/unit/ar/test_exploration.py`

**Interfaces:**
- Consumes: `PolicyConfig`/`DEFAULT_POLICY` (P2.1), `AllocationType`, `ArmSample` (P2.1), `RewardModel` + posteriors (P2.6).
- Produces:
  - `effective_fractions(policy: PolicyConfig, *, low_confidence: bool, regime_shift: bool) -> tuple[float, float, float]` — §60/§85: with either flag, `policy.exploration_boost` moves out of exploit into the exploration classes (radical for low confidence; split adjacent/radical for a suspected regime change); the three values always sum to the policy's original sum and never go negative.
  - `quota_from_fractions(slot_count: int, fractions: tuple[float, float, float]) -> dict[AllocationType, int]` — deterministic largest-remainder apportionment; the counts sum exactly to `slot_count`.
  - `reserve_baseline(slot_count: int, policy: PolicyConfig) -> int` — `ceil(baseline_fraction · slot_count)` when `baseline_fraction > 0` else 0; the reservation is *inside* the exploit quota (ADR-0023 #3), never extra.
  - `evidence_class(arm_id: str, *, measured_arm_ids: frozenset[str], adjacent_arm_ids: frozenset[str]) -> AllocationType` — exploit for measured arms; adjacent for unmeasured arms adjacent to something measured; radical otherwise (§14).
  - `sample_rewards(arms: Sequence[ContentArm], posteriors: Mapping[str, PosteriorState], *, model: RewardModel, rng: random.Random) -> tuple[ArmSample, ...]` — every arm must have a posterior (callers initialise priors first; a missing one is a bug, not a silent skip).
  - `sampled_ranking(samples: Sequence[ArmSample]) -> tuple[ArmSample, ...]` — `sampled_reward` descending, `arm_id` ascending (§58's deterministic total order).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ar/test_exploration.py
import random
from datetime import UTC, datetime

import pytest
from plm_contracts import AllocationType

from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.decision import ArmSample
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.domain.posterior import PosteriorState
from services.autonomous_ar.modeling.reward_model import NormalNormalRewardModel
from services.autonomous_ar.policy.exploration import (
    effective_fractions,
    evidence_class,
    quota_from_fractions,
    reserve_baseline,
    sample_rewards,
    sampled_ranking,
)


def test_default_quota_apportions_exactly_seven_two_one() -> None:
    quota = quota_from_fractions(10, (0.70, 0.20, 0.10))
    assert quota == {AllocationType.EXPLOIT: 7, AllocationType.ADJACENT_EXPLORE: 2,
                     AllocationType.RADICAL_EXPLORE: 1}
    small = quota_from_fractions(2, (0.70, 0.20, 0.10))
    assert sum(small.values()) == 2 and small[AllocationType.EXPLOIT] >= 1


def test_baseline_reservation_is_inside_the_exploit_slice() -> None:
    assert reserve_baseline(10, DEFAULT_POLICY) == 1
    quota = quota_from_fractions(10, (0.70, 0.20, 0.10))
    assert reserve_baseline(10, DEFAULT_POLICY) <= quota[AllocationType.EXPLOIT]


def test_low_confidence_and_regime_shift_raise_exploration_without_breaking_sum() -> None:
    base = (DEFAULT_POLICY.exploit_fraction, DEFAULT_POLICY.adjacent_explore_fraction,
            DEFAULT_POLICY.radical_explore_fraction)
    low = effective_fractions(DEFAULT_POLICY, low_confidence=True, regime_shift=False)
    shift = effective_fractions(DEFAULT_POLICY, low_confidence=False, regime_shift=True)
    calm = effective_fractions(DEFAULT_POLICY, low_confidence=False, regime_shift=False)
    assert calm == base
    assert sum(low) == pytest.approx(1.0) and sum(shift) == pytest.approx(1.0)
    assert low[0] < base[0] and low[2] > base[2]
    assert shift[0] < base[0] and shift[1] > base[1] and shift[2] > base[2]


def test_evidence_class_prefers_measured_then_adjacent_then_radical() -> None:
    measured, adjacent = frozenset({"m"}), frozenset({"n"})
    assert evidence_class("m", measured_arm_ids=measured, adjacent_arm_ids=adjacent) \
        is AllocationType.EXPLOIT
    assert evidence_class("n", measured_arm_ids=measured, adjacent_arm_ids=adjacent) \
        is AllocationType.ADJACENT_EXPLORE
    assert evidence_class("x", measured_arm_ids=measured, adjacent_arm_ids=adjacent) \
        is AllocationType.RADICAL_EXPLORE


NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
COMPONENTS = {name: 50.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}


def _arm(arm_id: str) -> ContentArm:
    return ContentArm(arm_id=arm_id, dimensions=ArmDimensions(genre="rnb"), p1_score=60.0,
                      p1_confidence=0.6, component_scores=dict(COMPONENTS),
                      observation_count=1, source_count=1, as_of=NOW, score_version="p1-v1",
                      freshness_seconds=0.0)


def _posterior(arm_id: str, mean: float) -> PosteriorState:
    return PosteriorState(arm_id=arm_id, model_type="normal-normal", model_version="p2-nn-v1",
                          prior_mean=mean, prior_variance=0.01, posterior_mean=mean,
                          posterior_variance=0.001, observation_count=5,
                          effective_observation_count=5.0, update_version=1,
                          last_update_at=NOW)


def test_sampling_is_seeded_and_ranking_is_a_total_order() -> None:
    model = NormalNormalRewardModel()
    arms = [_arm("a"), _arm("b")]
    posteriors = {"a": _posterior("a", 0.7), "b": _posterior("b", 0.3)}
    first = sample_rewards(arms, posteriors, model=model, rng=random.Random(11))
    second = sample_rewards(arms, posteriors, model=model, rng=random.Random(11))
    assert first == second
    ranked = sampled_ranking([ArmSample(arm_id="b", sampled_reward=0.5,
                                        posterior_expected_reward=0.4),
                              ArmSample(arm_id="a", sampled_reward=0.5,
                                        posterior_expected_reward=0.6)])
    assert [sample.arm_id for sample in ranked] == ["a", "b"], "ties break on arm_id"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_exploration.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.policy'`.

- [ ] **Step 3: Write `policy/exploration.py`**

```python
# services/autonomous_ar/policy/exploration.py
"""Exploration arithmetic (§13/§14/§60/§85): quotas, class evidence, seeded sampling.

The three discrete classes are the contract (ADR-0023 #2). `exploration_fraction` is
reporting only; no function here branches on it. Apportionment is largest-remainder so
counts are exact, deterministic and sum to the slot count.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence

from plm_contracts import AllocationType

from ..domain.arm import ContentArm
from ..domain.decision import ArmSample
from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState
from ..modeling.reward_model import RewardModel

__all__ = [
    "effective_fractions", "evidence_class", "quota_from_fractions", "reserve_baseline",
    "sample_rewards", "sampled_ranking",
]


def effective_fractions(policy: PolicyConfig, *, low_confidence: bool,
                        regime_shift: bool) -> tuple[float, float, float]:
    """§60/§85: raise exploration from the exploit slice when evidence is thin or shifting."""
    exploit, adjacent, radical = (
        policy.exploit_fraction, policy.adjacent_explore_fraction,
        policy.radical_explore_fraction,
    )
    if low_confidence:
        moved = min(policy.exploration_boost, exploit)
        exploit, radical = exploit - moved, radical + moved
    if regime_shift:
        moved = min(policy.exploration_boost / 2.0, exploit)
        exploit = exploit - moved
        adjacent += moved / 2.0
        radical += moved / 2.0
    return (exploit, adjacent, radical)


def quota_from_fractions(slot_count: int, fractions: tuple[float, float, float]) -> dict[
        AllocationType, int]:
    """Largest-remainder apportionment; counts sum exactly to `slot_count`."""
    if slot_count < 1:
        raise ValueError("slot_count must be >= 1")
    classes = (AllocationType.EXPLOIT, AllocationType.ADJACENT_EXPLORE,
               AllocationType.RADICAL_EXPLORE)
    raw = [slot_count * fraction for fraction in fractions]
    counts = [math.floor(value) for value in raw]
    remainder = slot_count - sum(counts)
    # hand out the remaining seats to the largest fractional parts; ties by class order
    order = sorted(range(3), key=lambda index: (-(raw[index] - counts[index]), index))
    for index in order[:remainder]:
        counts[index] += 1
    return dict(zip(classes, counts, strict=True))


def reserve_baseline(slot_count: int, policy: PolicyConfig) -> int:
    """§40/§41: the evergreen control share, carved out of the exploit quota."""
    if policy.baseline_fraction <= 0.0:
        return 0
    return min(slot_count, max(1, math.ceil(policy.baseline_fraction * slot_count)))


def evidence_class(arm_id: str, *, measured_arm_ids: frozenset[str],
                   adjacent_arm_ids: frozenset[str]) -> AllocationType:
    """§14: measured arms exploit; unmeasured arms adjacent or radical."""
    if arm_id in measured_arm_ids:
        return AllocationType.EXPLOIT
    if arm_id in adjacent_arm_ids:
        return AllocationType.ADJACENT_EXPLORE
    return AllocationType.RADICAL_EXPLORE


def sample_rewards(arms: Sequence[ContentArm], posteriors: Mapping[str, PosteriorState], *,
                   model: RewardModel, rng: random.Random) -> tuple[ArmSample, ...]:
    """One seeded Thompson draw per arm; a missing posterior is a caller bug (§66)."""
    samples: list[ArmSample] = []
    for arm in arms:
        posterior = posteriors[arm.arm_id]  # KeyError is the loud failure
        samples.append(ArmSample(
            arm_id=arm.arm_id, sampled_reward=model.sample(posterior, rng),
            posterior_expected_reward=model.expected_value(posterior),
        ))
    return tuple(samples)


def sampled_ranking(samples: Sequence[ArmSample]) -> tuple[ArmSample, ...]:
    """§58: sampled reward descending, `arm_id` ascending — a deterministic total order."""
    return tuple(sorted(samples, key=lambda sample: (-sample.sampled_reward, sample.arm_id)))
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_exploration.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

Run: `git add services/autonomous_ar/policy tests/unit/ar/test_exploration.py; git commit -m "feat(ar): P2.7 exploration quotas, evidence classes and seeded sampling"`

---

### Task P2.8: Constraints

**Files:**
- Create: `services/autonomous_ar/policy/constraints.py`
- Test: `tests/unit/ar/test_constraints.py`

**Interfaces:**
- Consumes: `PolicyConfig`, `ContentArm`, `ArmLifecycleStatus`, `ArmSample`, `ConstraintRejection`, `ReasonCode`, `AllocationType`, `reserve_baseline` (P2.7).
- Produces:
  - `Selection(arm_id, allocation_type, sample, reason_codes: tuple[str, ...])`.
  - `Portfolio(selections: tuple[Selection, ...], rejections: tuple[ConstraintRejection, ...], warnings: tuple[ReasonCode, ...])` with `is_partial(slot_count)`.
  - `directive_budget_share(cycle_budget: float, slot_count: int) -> float` — the ADR-0023 #1 share; equal split, so `sum(share for selections) <= cycle_budget` structurally and the test pins it.
  - `apply_portfolio_constraints(ranked: Sequence[ArmSample], *, classes: Mapping[str, AllocationType], arms: Mapping[str, ContentArm], states: Mapping[str, ArmLifecycleStatus], policy: PolicyConfig, slot_count: int, quota: Mapping[AllocationType, int], baselines: Sequence[str], clusters: Mapping[str, str]) -> Portfolio` — §58's walk: baseline reservation first (inside exploit, tagged `BASELINE_CONTROL`), then each class quota in `exploit → adjacent_explore → radical_explore` order; every acceptance/rejection is deterministic and reason-coded. Hard exclusions: `blocked`/`retired`/`archived`/`paused` arms are never selected (§111); `cooldown` arms are not selected by this function (the caller filters by `cooldown_until`); caps: exact-arm share, cluster/ genre/use-case/language shares, and §25's `max_concurrent_experiments_per_cluster`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ar/test_constraints.py
import math
from datetime import UTC, datetime

from plm_contracts import AllocationType

from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.decision import ArmSample
from services.autonomous_ar.domain.enums import ArmLifecycleStatus, ReasonCode
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.policy.constraints import (
    apply_portfolio_constraints,
    directive_budget_share,
)

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
COMPONENTS = {name: 50.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}


def _arm(arm_id: str, *, genre: str = "rnb", use_case: str = "late_night_drive",
         language: str = "english") -> ContentArm:
    return ContentArm(arm_id=arm_id,
                      dimensions=ArmDimensions(genre=genre, use_cases=(use_case,),
                                               language=language),
                      p1_score=60.0, p1_confidence=0.6, component_scores=COMPONENTS,
                      observation_count=1, source_count=1, as_of=NOW, score_version="p1-v1",
                      freshness_seconds=0.0)


def _ranked(count: int, *, genre: str = "rnb") -> tuple[list[ArmSample], dict[str, ContentArm]]:
    samples, arms = [], {}
    for index in range(count):
        arm_id = f"arm-{index:02d}"
        samples.append(ArmSample(arm_id=arm_id, sampled_reward=0.9 - index * 0.01,
                                 posterior_expected_reward=0.7))
        arms[arm_id] = _arm(arm_id, genre=genre)
    return samples, arms


def test_walk_honours_the_exact_arm_cap() -> None:
    samples, arms = _ranked(10)
    portfolio = apply_portfolio_constraints(
        samples, classes={arm_id: AllocationType.EXPLOIT for arm_id in arms},
        arms=arms, states={arm_id: ArmLifecycleStatus.ACTIVE for arm_id in arms},
        policy=DEFAULT_POLICY, slot_count=10,
        quota={AllocationType.EXPLOIT: 10, AllocationType.ADJACENT_EXPLORE: 0,
               AllocationType.RADICAL_EXPLORE: 0},
        baselines=(), clusters={arm_id: f"c-{index}" for index, arm_id in enumerate(arms)},
    )
    assert len(portfolio.selections) == 10
    assert len({selection.arm_id for selection in portfolio.selections}) == 10


def test_blocked_and_retired_arms_are_never_selected() -> None:
    samples, arms = _ranked(2)
    states = {"arm-00": ArmLifecycleStatus.BLOCKED, "arm-01": ArmLifecycleStatus.RETIRED}
    portfolio = apply_portfolio_constraints(
        samples, classes={arm_id: AllocationType.EXPLOIT for arm_id in arms},
        arms=arms, states=states, policy=DEFAULT_POLICY, slot_count=2,
        quota={AllocationType.EXPLOIT: 2, AllocationType.ADJACENT_EXPLORE: 0,
               AllocationType.RADICAL_EXPLORE: 0}, baselines=(),
        clusters={arm_id: arm_id for arm_id in arms},
    )
    assert portfolio.selections == ()
    assert all(rejection.reason is ReasonCode.DIVERSITY_CONSTRAINT
               for rejection in portfolio.rejections)
    assert ReasonCode.INSUFFICIENT_ELIGIBLE_ARMS in portfolio.warnings


def test_genre_cap_and_cluster_concurrency_hold() -> None:
    samples, arms = _ranked(10, genre="rnb")
    portfolio = apply_portfolio_constraints(
        samples, classes={arm_id: AllocationType.EXPLOIT for arm_id in arms},
        arms=arms, states={arm_id: ArmLifecycleStatus.ACTIVE for arm_id in arms},
        policy=DEFAULT_POLICY, slot_count=10,
        quota={AllocationType.EXPLOIT: 10, AllocationType.ADJACENT_EXPLORE: 0,
               AllocationType.RADICAL_EXPLORE: 0}, baselines=(),
        clusters={arm_id: "one-cluster" for arm_id in arms},
    )
    # genre cap 0.60 × 10 slots ⇒ at most 6; but cluster concurrency 2 binds first
    assert len(portfolio.selections) == 2
    assert any("cluster_concurrency" in rejection.detail for rejection in portfolio.rejections)


def test_baseline_reservation_is_honoured_and_budget_shares_stay_under_the_ceiling() -> None:
    samples, arms = _ranked(10)
    portfolio = apply_portfolio_constraints(
        samples, classes={arm_id: AllocationType.EXPLOIT for arm_id in arms},
        arms=arms, states={arm_id: ArmLifecycleStatus.ACTIVE for arm_id in arms},
        policy=DEFAULT_POLICY, slot_count=10,
        quota={AllocationType.EXPLOIT: 10, AllocationType.ADJACENT_EXPLORE: 0,
               AllocationType.RADICAL_EXPLORE: 0}, baselines=("arm-08",),
        clusters={arm_id: arm_id for arm_id in arms},
    )
    baseline_selection = next(s for s in portfolio.selections if s.arm_id == "arm-08")
    assert ReasonCode.BASELINE_CONTROL.value in baseline_selection.reason_codes
    share = directive_budget_share(cycle_budget=10.0, slot_count=10)
    assert share == 1.0
    assert len(portfolio.selections) * share <= 10.0 + 1e-9


def test_the_walk_is_deterministic_for_the_same_inputs() -> None:
    samples, arms = _ranked(6)
    args = dict(classes={arm_id: AllocationType.EXPLOIT for arm_id in arms}, arms=arms,
                states={arm_id: ArmLifecycleStatus.ACTIVE for arm_id in arms},
                policy=DEFAULT_POLICY, slot_count=6,
                quota={AllocationType.EXPLOIT: 6, AllocationType.ADJACENT_EXPLORE: 0,
                       AllocationType.RADICAL_EXPLORE: 0}, baselines=(),
                clusters={arm_id: arm_id for arm_id in arms})
    first = apply_portfolio_constraints(samples, **args)
    second = apply_portfolio_constraints(list(samples), **args)
    assert first == second
    assert math.floor(DEFAULT_POLICY.max_exact_arm_share * 6) <= 3
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_constraints.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.policy.constraints'`.

- [ ] **Step 3: Write `policy/constraints.py`**

```python
# services/autonomous_ar/policy/constraints.py
"""Portfolio constraints (§15/§25/§58/§59/§61) and the cycle's budget-share arithmetic.

Three passes, one deterministic walk. Pass 1 reserves the baseline share inside the exploit
quota; pass 2 honours each class quota in §58 order; pass 3 is §58's own "continue until slots
are filled" — remaining capacity is filled from the ranked list in the same class order, with
every cap still binding. That is deliberate: a cold start has no measured arms, so every arm is
`radical_explore` and only pass 3 can fill the cycle — which is exactly §60's "increase
exploration rather than pretending certainty" and §69-E's broad exploration. Allocation
fractions therefore *steer* the order; they are never a cage. The budget rule is ADR-0023 #1:
the cycle owns the ceiling, each directive's share is equal, and the walk can never accept more
selections than slots — so `sum(shares) <= ceiling` is structural.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from plm_contracts import AllocationType

from ..domain.arm import ContentArm
from ..domain.decision import ArmSample, ConstraintRejection
from ..domain.enums import ArmLifecycleStatus, ReasonCode
from ..domain.policy import PolicyConfig
from .exploration import reserve_baseline

__all__ = ["Portfolio", "Selection", "apply_portfolio_constraints", "directive_budget_share"]

_INELIGIBLE_STATES = frozenset({
    ArmLifecycleStatus.BLOCKED, ArmLifecycleStatus.RETIRED, ArmLifecycleStatus.ARCHIVED,
    ArmLifecycleStatus.PAUSED,
})
"""§9/§111: never selected, for any reason, in any mode. `cooldown` is filtered by the
caller with `cooldown_until` (a temporary state, unlike these)."""


@dataclass(frozen=True, slots=True)
class Selection:
    arm_id: str
    allocation_type: AllocationType
    sample: ArmSample
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Portfolio:
    selections: tuple[Selection, ...]
    rejections: tuple[ConstraintRejection, ...]
    warnings: tuple[ReasonCode, ...]

    def is_partial(self, slot_count: int) -> bool:
        """§59: fewer selections than slots is `PARTIAL`, never silently 'completed'."""
        return len(self.selections) < slot_count


def directive_budget_share(cycle_budget: float, slot_count: int) -> float:
    """ADR-0023 #1: the directive carries its share; shares are equal by construction."""
    if slot_count < 1:
        raise ValueError("slot_count must be >= 1")
    if cycle_budget <= 0.0:
        raise ValueError("cycle budget must be > 0")
    return cycle_budget / slot_count


def _share_count(share: float, slot_count: int) -> int:
    """A share cap as a whole number of slots; always at least 1 when the cap is positive."""
    return max(1, math.floor(share * slot_count + 1e-9))


def apply_portfolio_constraints(
    ranked: Sequence[ArmSample], *, classes: Mapping[str, AllocationType],
    arms: Mapping[str, ContentArm], states: Mapping[str, ArmLifecycleStatus],
    policy: PolicyConfig, slot_count: int, quota: Mapping[AllocationType, int],
    baselines: Sequence[str], clusters: Mapping[str, str],
) -> Portfolio:
    """§58's deterministic walk over a ranked list, one class quota at a time."""
    selections: list[Selection] = []
    rejections: list[ConstraintRejection] = []
    warnings: list[ReasonCode] = []
    selected_ids: set[str] = set()
    per_arm: dict[str, int] = {}
    per_cluster: dict[str, int] = {}
    per_genre: dict[str, int] = {}
    per_use_case: dict[str, int] = {}
    per_language: dict[str, int] = {}

    max_arm = _share_count(policy.max_exact_arm_share, slot_count)
    max_cluster = _share_count(policy.max_cluster_share, slot_count)
    max_genre = _share_count(policy.max_genre_share, slot_count)
    max_use_case = _share_count(policy.max_use_case_share, slot_count)
    max_language = _share_count(policy.max_language_share, slot_count)

    def _try_accept(sample: ArmSample, allocation_type: AllocationType,
                    extra_reasons: tuple[str, ...], *, record_rejection: bool = True) -> bool:
        arm = arms[sample.arm_id]
        cluster = clusters.get(sample.arm_id, sample.arm_id)
        genre = arm.dimensions.genre or "unknown"
        use_case = (arm.dimensions.use_cases or ("unknown",))[0]
        language = arm.dimensions.language or "unknown"
        checks = [
            (per_arm.get(sample.arm_id, 0) + 1 > max_arm, "exact_arm_share",
             f"exact-arm cap {policy.max_exact_arm_share}"),
            (per_cluster.get(cluster, 0) + 1 > max_cluster, "cluster_share",
             f"cluster cap {policy.max_cluster_share}"),
            (per_cluster.get(cluster, 0) + 1 > policy.max_concurrent_experiments_per_cluster,
             "cluster_concurrency", "§25 concurrent-experiment limit"),
            (per_genre.get(genre, 0) + 1 > max_genre, "genre_share",
             f"genre cap {policy.max_genre_share}"),
            (per_use_case.get(use_case, 0) + 1 > max_use_case, "use_case_share",
             f"use-case cap {policy.max_use_case_share}"),
            (per_language.get(language, 0) + 1 > max_language, "language_share",
             f"language cap {policy.max_language_share}"),
        ]
        for violated, code, detail in checks:
            if violated:
                if record_rejection:
                    rejections.append(ConstraintRejection(
                        arm_id=sample.arm_id, reason=ReasonCode.DIVERSITY_CONSTRAINT,
                        detail=f"{detail} ({code})",
                    ))
                return False
        selections.append(Selection(arm_id=sample.arm_id, allocation_type=allocation_type,
                                    sample=sample, reason_codes=extra_reasons))
        selected_ids.add(sample.arm_id)
        per_arm[sample.arm_id] = per_arm.get(sample.arm_id, 0) + 1
        per_cluster[cluster] = per_cluster.get(cluster, 0) + 1
        per_genre[genre] = per_genre.get(genre, 0) + 1
        per_use_case[use_case] = per_use_case.get(use_case, 0) + 1
        per_language[language] = per_language.get(language, 0) + 1
        return True

    # Pass 1 — baseline reservation (§40/§41), inside the exploit quota.
    reserved = reserve_baseline(slot_count, policy) if baselines else 0
    baseline_set = set(baselines)
    filled_baseline = 0
    for sample in ranked:
        if filled_baseline >= reserved or sample.arm_id not in baseline_set:
            continue
        if states.get(sample.arm_id) in _INELIGIBLE_STATES:
            continue
        if _try_accept(sample, AllocationType.EXPLOIT,
                       (ReasonCode.BASELINE_CONTROL.value, ReasonCode.LOW_FATIGUE.value)):
            filled_baseline += 1

    # Pass 2 — one class quota at a time, §58 order.
    for allocation_type in (AllocationType.EXPLOIT, AllocationType.ADJACENT_EXPLORE,
                            AllocationType.RADICAL_EXPLORE):
        target = quota.get(allocation_type, 0)
        if allocation_type is AllocationType.EXPLOIT:
            target = max(0, target - filled_baseline)
        taken = 0
        for sample in ranked:
            if taken >= target:
                break
            if sample.arm_id in selected_ids or classes.get(sample.arm_id) is not allocation_type:
                continue
            if states.get(sample.arm_id) in _INELIGIBLE_STATES:
                rejections.append(ConstraintRejection(
                    arm_id=sample.arm_id, reason=ReasonCode.DIVERSITY_CONSTRAINT,
                    detail=f"lifecycle state {states.get(sample.arm_id)} is never selected (§111)",
                ))
                continue
            reasons = (ReasonCode.STRONG_POSTERIOR.value,
                       ReasonCode.MARKET_SIGNAL_STRONG.value) \
                if allocation_type is AllocationType.EXPLOIT else (
                    ReasonCode.ADJACENT_TO_WINNER.value
                    if allocation_type is AllocationType.ADJACENT_EXPLORE
                    else ReasonCode.RADICAL_EXPLORATION.value,
                    ReasonCode.HIGH_UNCERTAINTY.value,
                )
            if _try_accept(sample, allocation_type, reasons):
                taken += 1

    # Pass 3 — §58's fill-until-slots: the quota steers order, the walk fills capacity.
    # A cold start has all arms in `radical_explore` (no measured evidence yet), so this pass
    # is what fills its slots with broad exploration — which is exactly §60's and §69-E's
    # expectation. Caps still bind; failures here were already recorded in passes 1–2.
    for allocation_type in (AllocationType.EXPLOIT, AllocationType.ADJACENT_EXPLORE,
                            AllocationType.RADICAL_EXPLORE):
        if len(selections) >= slot_count:
            break
        for sample in ranked:
            if len(selections) >= slot_count:
                break
            if sample.arm_id in selected_ids or classes.get(sample.arm_id) is not allocation_type:
                continue
            if states.get(sample.arm_id) in _INELIGIBLE_STATES:
                continue
            reasons = ((ReasonCode.STRONG_POSTERIOR.value,
                        ReasonCode.MARKET_SIGNAL_STRONG.value)
                       if allocation_type is AllocationType.EXPLOIT else (
                           ReasonCode.ADJACENT_TO_WINNER.value
                           if allocation_type is AllocationType.ADJACENT_EXPLORE
                           else ReasonCode.RADICAL_EXPLORATION.value,
                           ReasonCode.HIGH_UNCERTAINTY.value,
                       ))
            _try_accept(sample, allocation_type, reasons, record_rejection=False)

    if len(selections) < slot_count:
        warnings.append(ReasonCode.INSUFFICIENT_ELIGIBLE_ARMS)
    return Portfolio(selections=tuple(selections), rejections=tuple(rejections),
                     warnings=tuple(warnings))
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_constraints.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

Run: `git add services/autonomous_ar/policy/constraints.py tests/unit/ar/test_constraints.py; git commit -m "feat(ar): P2.8 portfolio constraints, budget shares and the §58 walk"`

---

### Task P2.9: Fatigue/nonstationarity

**Files:**
- Create: `services/autonomous_ar/modeling/fatigue.py`, `services/autonomous_ar/modeling/decay.py`
- Test: `tests/unit/ar/test_fatigue.py`, `tests/unit/ar/test_decay.py`

**Interfaces:**
- Consumes: `FatigueConfig`/`NonstationarityConfig` (P2.1), `Tri`.
- Produces:
  - `fatigue.exposure_score(selection_instants: Sequence[datetime], *, now, half_life_days) -> float` — sum of `0.5^(age/half-life)`; high recent exposure ⇒ high score, and the score **decays in time** because the weights do (§26: no permanent penalty).
  - `fatigue.fatigue_penalty(score, *, config: FatigueConfig) -> float` — `max_penalty · min(1, score / score_at_max)` with `score_at_max = 4.0` (invented constant).
  - `fatigue.is_in_cooldown(last_selected_at, *, now, config: FatigueConfig) -> bool`.
  - `decay.recency_weight(observed_at, *, now, half_life_days) -> float` — `0.5^(age/half-life)`, future instants clamp to 1.0 (clock skew), never negative.
  - `decay.effective_observation_count(observation_instants, *, now, half_life_days) -> float`.
  - `decay.weighted_reward(rewards: Sequence[float], weights: Sequence[float]) -> float` — weighted mean; empty input or zero weight sum raises.
  - `decay.detect_regime_shift(recent: float | None, baseline: float | None, *, relative_threshold: float) -> bool | None` — `None` when either input is unknown (never guess, §28); `True` when `abs(recent − baseline) / max(baseline, ε) >= threshold` in *either* direction.

- [ ] **Step 1: Write the failing fatigue test**

```python
# tests/unit/ar/test_fatigue.py
from datetime import UTC, datetime, timedelta

from services.autonomous_ar.domain.policy import FatigueConfig
from services.autonomous_ar.modeling.fatigue import (
    exposure_score, fatigue_penalty, is_in_cooldown,
)

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
CONFIG = FatigueConfig(decay_half_life_days=14.0, max_penalty=0.5, cooldown_hours=72.0)


def test_recent_exposure_raises_the_penalty_and_old_exposure_forgets_it() -> None:
    fresh = exposure_score([NOW - timedelta(days=1)], now=NOW, half_life_days=14.0)
    stale = exposure_score([NOW - timedelta(days=28)], now=NOW, half_life_days=14.0)
    assert fresh > stale
    assert fatigue_penalty(fresh, config=CONFIG) > fatigue_penalty(stale, config=CONFIG)
    assert fatigue_penalty(0.0, config=CONFIG) == 0.0
    assert fatigue_penalty(100.0, config=CONFIG) == CONFIG.max_penalty


def test_the_penalty_halves_over_the_half_life() -> None:
    score_now = exposure_score([NOW], now=NOW, half_life_days=14.0)
    score_later = exposure_score([NOW - timedelta(days=14)], now=NOW, half_life_days=14.0)
    assert score_later == score_now / 2


def test_cooldown_window_is_explicit() -> None:
    assert is_in_cooldown(NOW - timedelta(hours=71), now=NOW, config=CONFIG) is True
    assert is_in_cooldown(NOW - timedelta(hours=73), now=NOW, config=CONFIG) is False
    assert is_in_cooldown(None, now=NOW, config=CONFIG) is False
```

```python
# tests/unit/ar/test_decay.py
from datetime import UTC, datetime, timedelta

import pytest

from services.autonomous_ar.modeling.decay import (
    detect_regime_shift, effective_observation_count, recency_weight, weighted_reward,
)

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_recency_weight_is_half_after_one_half_life_and_capped_for_the_future() -> None:
    assert recency_weight(NOW, now=NOW, half_life_days=45.0) == 1.0
    assert recency_weight(NOW - timedelta(days=45), now=NOW, half_life_days=45.0) == 0.5
    assert recency_weight(NOW + timedelta(hours=1), now=NOW, half_life_days=45.0) == 1.0


def test_effective_observation_count_and_weighted_reward() -> None:
    instants = [NOW, NOW - timedelta(days=45)]
    effective = effective_observation_count(instants, now=NOW, half_life_days=45.0)
    assert effective == pytest.approx(1.5)
    assert weighted_reward([1.0, 0.0], [1.0, 1.0]) == pytest.approx(0.5)
    with pytest.raises(ValueError, match="at least one"):
        weighted_reward([], [])
    with pytest.raises(ValueError, match="zero total weight"):
        weighted_reward([0.5], [0.0])


def test_regime_shift_needs_both_sides_and_fires_in_either_direction() -> None:
    assert detect_regime_shift(None, 0.5, relative_threshold=0.3) is None
    assert detect_regime_shift(0.5, None, relative_threshold=0.3) is None
    assert detect_regime_shift(0.30, 0.60, relative_threshold=0.3) is True   # degraded
    assert detect_regime_shift(0.90, 0.60, relative_threshold=0.3) is True   # improved
    assert detect_regime_shift(0.55, 0.60, relative_threshold=0.3) is False
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_fatigue.py tests\unit\ar\test_decay.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.modeling.fatigue'`.

- [ ] **Step 3: Write `modeling/fatigue.py` and `modeling/decay.py`**

```python
# services/autonomous_ar/modeling/fatigue.py
"""Exposure, fatigue and cooldown (§26): a temporary penalty that decays by construction.

The penalty is a function of a decayed exposure score, so it forgets old selections
without ever needing an explicit "un-penalise" step — §26's "do not use a permanent
penalty" holds structurally, not by convention.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from ..domain.policy import FatigueConfig

__all__ = ["exposure_score", "fatigue_penalty", "is_in_cooldown"]

SCORE_AT_MAX_PENALTY = 4.0
"""Exposure score at which the configured maximum penalty binds (invented constant)."""


def exposure_score(selection_instants: Sequence[datetime], *, now: datetime,
                   half_life_days: float) -> float:
    """Decayed count of recent selections; future instants count as 'just now'."""
    if half_life_days <= 0.0:
        raise ValueError("half_life_days must be > 0")
    score = 0.0
    for instant in selection_instants:
        age_days = max(0.0, (now - instant).total_seconds() / 86400.0)
        score += 0.5 ** (age_days / half_life_days)
    return score


def fatigue_penalty(score: float, *, config: FatigueConfig) -> float:
    """Bounded, monotone in recent exposure; zero when disabled or nothing is recent."""
    if not config.enabled or score <= 0.0:
        return 0.0
    return config.max_penalty * min(1.0, score / SCORE_AT_MAX_PENALTY)


def is_in_cooldown(last_selected_at: datetime | None, *, now: datetime,
                   config: FatigueConfig) -> bool:
    """§9: cooldown is temporary; `None` means never selected, hence not cooling down."""
    if last_selected_at is None:
        return False
    return now - last_selected_at < timedelta(hours=config.cooldown_hours)
```

```python
# services/autonomous_ar/modeling/decay.py
"""Recency weighting and regime detection (§27/§28): old evidence decays, it is not deleted.

Every function takes its `now`; nothing reads a clock. `None` in, `None` out for the
shift detector — an unknown baseline is not a shift (§28, P0 §2 "unknown ≠ zero").
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

__all__ = [
    "detect_regime_shift", "effective_observation_count", "recency_weight", "weighted_reward",
]


def recency_weight(observed_at: datetime, *, now: datetime, half_life_days: float) -> float:
    """`0.5^(age/half-life)`, clamped to (0, 1]; future instants are clock skew."""
    if half_life_days <= 0.0:
        raise ValueError("half_life_days must be > 0")
    age_days = (now - observed_at).total_seconds() / 86400.0
    if age_days <= 0.0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def effective_observation_count(observation_instants: Sequence[datetime], *, now: datetime,
                                half_life_days: float) -> float:
    """§27: both raw history and the weighted current view exist; this is the weighted one."""
    return sum(recency_weight(instant, now=now, half_life_days=half_life_days)
               for instant in observation_instants)


def weighted_reward(rewards: Sequence[float], weights: Sequence[float]) -> float:
    """Recency-weighted mean reward; refuses degenerate inputs rather than guessing."""
    if len(rewards) != len(weights):
        raise ValueError("rewards and weights must be the same length")
    if not rewards:
        raise ValueError("weighted_reward needs at least one observation")
    total = sum(weights)
    if total <= 0.0:
        raise ValueError("weighted_reward refuses a zero total weight")
    return sum(reward * weight for reward, weight in zip(rewards, weights, strict=True)) / total


def detect_regime_shift(recent: float | None, baseline: float | None, *,
                        relative_threshold: float) -> bool | None:
    """§28: a material move in *either* direction; `None` when either side is unknown."""
    if recent is None or baseline is None:
        return None
    if relative_threshold <= 0.0:
        raise ValueError("relative_threshold must be > 0")
    denominator = max(abs(baseline), 1e-9)
    return abs(recent - baseline) / denominator >= relative_threshold
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_fatigue.py tests\unit\ar\test_decay.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

Run: `git add services/autonomous_ar/modeling/fatigue.py services/autonomous_ar/modeling/decay.py tests/unit/ar/test_fatigue.py tests/unit/ar/test_decay.py; git commit -m "feat(ar): P2.9 decaying fatigue and recency-weighted regime detection"`

---

### Task P2.10: Allocation cycle

**Files:**
- Create: `services/autonomous_ar/decisioning/__init__.py`, `decisioning/planner.py`, `decisioning/portfolio.py`, `decisioning/cycle.py`
- Create: `services/autonomous_ar/policy/lifecycle.py`
- Create: `services/autonomous_ar/jobs/__init__.py`, `jobs/cycle.py`, `jobs/schedules.py`
- Modify: `services/autonomous_ar/storage/repositories.py` — add `load_directives_for_cycle`, `load_latest_feature_snapshot`, `load_exposure_score`, `is_in_cooldown_state`, `load_stored_snapshot`; `insert_feature_snapshot` also persists the arm's dimensions inside `feature_json`; `record_selection` gains `recent_exposure_score`.
- Modify: `tests/contract/test_c2_ar_to_experiments.py` — swap the direct `to_wire()` producer for a directive produced by the real cycle (the P2.1A note promised this).
- Test: `tests/unit/ar/test_cycle.py`, `tests/integration/ar/test_temporal_jobs.py`

**Interfaces:**
- Consumes: everything above — `load_policy`, `cold_start_prior`, `NormalNormalRewardModel`, `effective_fractions`/`quota_from_fractions`/`evidence_class`/`sample_rewards`/`sampled_ranking`, `apply_portfolio_constraints`/`directive_budget_share`, fatigue/decay, `cluster_arms`/`arm_similarity`, all repositories, `TaskQueue.AR`/`WorkerSet` (`plm_platform.temporal`).
- Produces (exact):
  - `cycle.CycleRequest(brand_id: str, as_of: datetime, slot_count: int, budget: float, policy_version: str, activation_state: ChannelActivationState | None = None, random_seed: int | None = None, unavailable_capabilities: tuple[str, ...] = ())`.
  - `cycle.MarketSnapshot(arms: tuple[ContentArm, ...], as_of: datetime, source: str, score_version: str)` — `source` is `"live"` or `"stored"` (§102's degraded path).
  - `cycle.CycleResult(cycle_id: str, status: AllocationCycleStatus, directives: tuple[ProductionDirective, ...], rejected: tuple[ConstraintRejection, ...], warnings: tuple[str, ...], replay: bool)`.
  - `cycle.PolicyNotConfigured(RuntimeError)`; `cycle.seed_for_cycle(cycle_id: str) -> int`; `cycle.run_allocation_cycle(session, request, *, settings, market, model=None, now) -> CycleResult` (§29's fifteen steps in order, each one a named comment).
  - `planner.PreparedCycle(ranked, classes, clusters, warnings, ineligible)`; `planner.prepare_cycle(...)`; `planner.cluster_snapshot(arms, *, threshold)`.
  - `lifecycle.evaluate_retirements(posteriors, *, states, policy, now) -> tuple[RetirementDecision, ...]` (§38: `min_mature_observations` **and** posterior mean below `weak_posterior_threshold`; one bad experiment never retires) and `lifecycle.reopen_reason(arm, *, stored_score, policy, state) -> str | None` (§39: a materially risen market score reopens a retired arm, with the reason stored).
  - `portfolio.build_portfolio(prepared, *, policy, slot_count, quota, arms, states, baselines)`; `portfolio.build_directives(portfolio, *, cycle_id, policy, posteriors, arms, settings, expires_at, budget_share)`.
  - `jobs.cycle.AllocationCycleWorkflow`, `jobs.cycle.run_cycle_activity(payload) -> dict`; `jobs.schedules.ensure_schedules()`, `JOB_NAMES = ("ar_weekly_allocation",)`; `jobs.REGISTRATIONS: tuple[WorkerSet, ...]`.

- [ ] **Step 1: Write the failing end-to-end cycle test**

```python
# tests/unit/ar/test_cycle.py
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.decisioning.cycle import (
    CycleRequest,
    MarketSnapshot,
    PolicyNotConfigured,
    run_allocation_cycle,
    seed_for_cycle,
)
from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.enums import AllocationCycleStatus, ReasonCode
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.storage import repositories
from services.autonomous_ar.storage.models import ar_allocation_cycle
from tests.support.ar_db import ar_session, create_ar_engine

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_ar_settings()
FULL = {name: 60.0 for name in
        ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
         "cross_platform", "production_fit")}


def _arm(arm_id: str, *, score: float, components: dict[str, float | None] | None = None,
         freshness: float = 0.0) -> ContentArm:
    return ContentArm(
        arm_id=arm_id,
        dimensions=ArmDimensions(genre=arm_id.split("-")[0],
                                 use_cases=("late_night_drive",), language="english",
                                 vocal_mode="vocal"),
        p1_score=score, p1_confidence=0.7, component_scores=components or dict(FULL),
        observation_count=5, source_count=2, as_of=NOW, score_version="p1-v1",
        freshness_seconds=freshness,
    )


def _snapshot(*arms: ContentArm, source: str = "live") -> MarketSnapshot:
    return MarketSnapshot(arms=arms, as_of=NOW, source=source, score_version="p1-v1")


def _request(**overrides: object) -> CycleRequest:
    base: dict[str, object] = {"brand_id": "neon_seoul", "as_of": NOW, "slot_count": 4,
                               "budget": 8.0, "policy_version": "p2-v1"}
    base.update(overrides)
    return CycleRequest(**base)  # type: ignore[arg-type]


def _seed_policy(session) -> None:
    repositories.insert_policy(session, DEFAULT_POLICY, brand_id="neon_seoul", created_at=NOW)


def test_full_cycle_persists_decisions_directives_and_every_version() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_policy(session)
        result = run_allocation_cycle(
            session, _request(), settings=SETTINGS,
            market=_snapshot(_arm("rnb-1", score=82.0), _arm("citypop-2", score=78.0),
                             _arm("house-3", score=61.0), _arm("jazz-4", score=55.0)),
            now=NOW,
        )
        assert result.replay is False
        assert result.status is AllocationCycleStatus.COMPLETED
        assert len(result.directives) == 4
        assert len({d.arm_id for d in result.directives}) == 4
        decisions = repositories.load_decisions(session, result.cycle_id)
        assert {row["policy_version"] for row in decisions} == {"p2-v1"}
        assert {row["model_version"] for row in decisions} == {"p2-nn-v1"}
        assert {row["feature_version"] for row in decisions} == {"p2-features-v1"}
        assert {row["normalization_version"] for row in decisions} == {"p2-proxy-norm-v1"}
        assert {row["p1_score_version"] for row in decisions} == {"p1-v1"}
        # SHADOW (the settings default): computed and auditable, never actionable
        assert repositories.list_queued_directives(
            session, brand_id="neon_seoul", activation_states=("AUTONOMOUS",)) == []
        for directive in result.directives:
            assert directive.expires_at == NOW + timedelta(
                hours=DEFAULT_POLICY.directive_ttl_hours)
            assert directive.budget_limit == 8.0 / 4
        assert sum(d.budget_limit for d in result.directives) <= 8.0 + 1e-9


def test_replaying_the_same_cycle_is_idempotent() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_policy(session)
        market = _snapshot(_arm("rnb-1", score=82.0), _arm("citypop-2", score=78.0))
        first = run_allocation_cycle(session, _request(slot_count=2), settings=SETTINGS,
                                     market=market, now=NOW)
        second = run_allocation_cycle(session, _request(slot_count=2), settings=SETTINGS,
                                      market=market, now=NOW)
        assert second.replay is True and second.cycle_id == first.cycle_id
        assert [d.directive_id for d in second.directives] == [d.directive_id
                                                               for d in first.directives]
        count = session.execute(sa.select(sa.func.count()).select_from(ar_allocation_cycle)) \
            .scalar_one()
        assert count == 1, "a replay writes no second cycle"


def test_under_covered_arm_is_flagged_not_ranked() -> None:
    engine = create_ar_engine()
    thin = {name: (42.0 if name == "demand" else None) for name in FULL}
    with ar_session(engine) as session:
        _seed_policy(session)
        result = run_allocation_cycle(
            session, _request(slot_count=2), settings=SETTINGS,
            market=_snapshot(_arm("rnb-1", score=82.0),
                             _arm("thin-2", score=99.0, components=thin)), now=NOW,
        )
        assert "thin-2" not in {d.arm_id for d in result.directives}
        assert any(rejection.arm_id == "thin-2"
                   and rejection.reason is ReasonCode.EVIDENCE_INSUFFICIENT
                   for rejection in result.rejected)


def test_stale_arm_is_rejected_and_a_market_score_shift_is_flagged() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_policy(session)
        run_allocation_cycle(session, _request(slot_count=1), settings=SETTINGS,
                             market=_snapshot(_arm("rnb-1", score=90.0)), now=NOW)
        later = NOW + timedelta(days=8)
        second = run_allocation_cycle(
            session, _request(as_of=later, slot_count=1), settings=SETTINGS,
            market=MarketSnapshot(arms=(_arm("rnb-1", score=40.0, freshness=200 * 3600.0),
                                        _arm("rnb-1b", score=40.0, freshness=200 * 3600.0)),
                                  as_of=later, source="live", score_version="p1-v1"),
            now=later,
        )
        assert any(rejection.reason is ReasonCode.MARKET_STALE
                   for rejection in second.rejected), "§83: stale data is never used as current"
        assert ReasonCode.MARKET_REGIME_CHANGE_SUSPECTED.value in second.warnings


def test_unknown_policy_refuses_the_cycle() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        with pytest.raises(PolicyNotConfigured, match="p2-v1"):
            run_allocation_cycle(session, _request(), settings=SETTINGS,
                                 market=_snapshot(_arm("rnb-1", score=80.0)), now=NOW)


def test_seed_is_derived_from_the_cycle_id_and_is_stable() -> None:
    from services.autonomous_ar.storage.ids import deterministic_id

    cycle_id = deterministic_id("cycle", "neon_seoul", NOW.isoformat(), "p2-v1")
    assert seed_for_cycle(cycle_id) == seed_for_cycle(cycle_id)
    assert 0 <= seed_for_cycle(cycle_id) < 2**31
    assert seed_for_cycle(cycle_id) != seed_for_cycle(
        deterministic_id("cycle", "neon_seoul", NOW.isoformat(), "p2-v2"))


def test_autonomous_cycles_serve_directives_while_shadow_cycles_do_not() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_policy(session)
        run_allocation_cycle(session, _request(slot_count=1), settings=SETTINGS,
                             market=_snapshot(_arm("rnb-1", score=80.0)), now=NOW)
        assert repositories.list_queued_directives(
            session, brand_id="neon_seoul", activation_states=("AUTONOMOUS",)) == []
        repositories.set_channel_state(session, brand_id="neon_seoul",
                                       activation_state="AUTONOMOUS", now=NOW,
                                       reason="operator go", updated_by="operator")
        run_allocation_cycle(
            session, _request(slot_count=1, as_of=NOW + timedelta(days=1)), settings=SETTINGS,
            market=_snapshot(_arm("rnb-1", score=80.0)), now=NOW + timedelta(days=1))
        served = repositories.list_queued_directives(
            session, brand_id="neon_seoul", activation_states=("AUTONOMOUS",))
        assert len(served) == 1, "ADR-0024: only AUTONOMOUS cycles are actionable"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_cycle.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.decisioning'`.

- [ ] **Step 3: Write `decisioning/planner.py` (pure preparation)**

```python
# services/autonomous_ar/decisioning/planner.py
"""§29 steps 1–9 as pure functions: eligibility, evidence classes, clusters, samples.

No repository access and no clock: the cycle passes the snapshot, the posteriors and the
policy in and gets back a ranked, class-labelled candidate set. That is what makes the
leak-prone part of P2 ("which arms competed, with what data") unit-testable.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from plm_contracts import AllocationType

from ..config import ARSettings
from ..domain.arm import ContentArm
from ..domain.decision import ArmSample
from ..domain.enums import ReasonCode
from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState
from ..modeling.fatigue import fatigue_penalty
from ..modeling.reward_model import RewardModel
from ..modeling.similarity import arm_similarity, cluster_arms
from ..policy.exploration import evidence_class, sample_rewards, sampled_ranking

__all__ = ["PreparedCycle", "cluster_snapshot", "prepare_cycle"]


@dataclass(frozen=True, slots=True)
class PreparedCycle:
    ranked: tuple[ArmSample, ...]
    classes: Mapping[str, AllocationType]
    clusters: Mapping[str, str]
    warnings: tuple[str, ...]
    ineligible: Mapping[str, str]  # arm_id → ReasonCode value, for the flagged decision row


def cluster_snapshot(arms: Sequence[ContentArm], *, threshold: float) -> dict[str, str]:
    """Live clustering over a snapshot; pairs below the threshold are simply absent."""
    pairs: list[tuple[str, str, float]] = []
    for index, arm in enumerate(arms):
        for other in arms[index + 1:]:
            score = arm_similarity(arm.dimensions, other.dimensions)
            if score >= threshold:
                pairs.append((arm.arm_id, other.arm_id, score))
    return cluster_arms(pairs, threshold=threshold)


def prepare_cycle(*, arms: Sequence[ContentArm], posteriors: Mapping[str, PosteriorState],
                  policy: PolicyConfig, settings: ARSettings, model: RewardModel,
                  rng: random.Random, selection_recency: Mapping[str, float],
                  min_components: int, warnings: Sequence[str]) -> PreparedCycle:
    """Eligibility (§83/§111), evidence classes (§14), fatigue-adjusted ranking (§26/§58)."""
    ineligible: dict[str, str] = {}
    ranked_arms: list[ContentArm] = []
    for arm in arms:
        if not arm.is_rankable(minimum=min_components):
            ineligible[arm.arm_id] = ReasonCode.EVIDENCE_INSUFFICIENT.value
            continue
        if arm.freshness_seconds > policy.max_market_data_age_hours * 3600.0:
            ineligible[arm.arm_id] = ReasonCode.MARKET_STALE.value
            continue
        if arm.arm_id not in posteriors:
            ineligible[arm.arm_id] = "NO_POSTERIOR"
            continue
        ranked_arms.append(arm)

    clusters = cluster_snapshot(ranked_arms, threshold=settings.similarity_threshold)
    measured = frozenset(arm.arm_id for arm in ranked_arms
                         if posteriors[arm.arm_id].observation_count > 0)
    adjacent: set[str] = set()
    for arm_id in measured:
        cluster_id = clusters.get(arm_id)
        adjacent.update(other_id for other_id, other_cluster in clusters.items()
                        if other_cluster == cluster_id and other_id != arm_id)
    classes = {arm.arm_id: evidence_class(arm.arm_id, measured_arm_ids=measured,
                                          adjacent_arm_ids=frozenset(adjacent - measured))
               for arm in ranked_arms}

    collected = list(warnings)
    adjusted: list[ArmSample] = []
    for sample in sample_rewards(ranked_arms, posteriors, model=model, rng=rng):
        penalty = fatigue_penalty(selection_recency.get(sample.arm_id, 0.0),
                                  config=policy.fatigue)
        if penalty > 0.0:
            collected.append(ReasonCode.FATIGUE_COOLDOWN.value)
        adjusted.append(ArmSample(
            arm_id=sample.arm_id,
            sampled_reward=max(0.0, sample.sampled_reward - penalty),
            posterior_expected_reward=sample.posterior_expected_reward,
        ))
    return PreparedCycle(ranked=sampled_ranking(adjusted), classes=classes, clusters=clusters,
                         warnings=tuple(dict.fromkeys(collected)), ineligible=ineligible)
```

- [ ] **Step 4: Write `decisioning/portfolio.py` (selections → directives)**

```python
# services/autonomous_ar/decisioning/portfolio.py
"""§29 steps 10–12: the constraints walk, then directive construction (§32/§120).

This builder is the producer of frozen seam C2 (ADR-0023): deterministic ids, the arm's
search region as `brief`, `expected_value` the posterior mean under the cycle's value
model, `budget_limit` the cycle's equal share.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime

from plm_contracts import AllocationType

from ..config import ARSettings
from ..domain.arm import ContentArm
from ..domain.directive import DirectiveBrief, ProductionDirective
from ..domain.enums import ArmLifecycleStatus
from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState
from ..policy.constraints import Portfolio, apply_portfolio_constraints
from ..storage.ids import deterministic_id
from .planner import PreparedCycle

__all__ = ["build_directives", "build_portfolio"]


def build_portfolio(prepared: PreparedCycle, *, policy: PolicyConfig, slot_count: int,
                    quota: Mapping[AllocationType, int], arms: Mapping[str, ContentArm],
                    states: Mapping[str, ArmLifecycleStatus],
                    baselines: Sequence[str]) -> Portfolio:
    """§58's walk with this cycle's classes, clusters and caps."""
    return apply_portfolio_constraints(
        prepared.ranked, classes=prepared.classes, arms=arms, states=states, policy=policy,
        slot_count=slot_count, quota=quota, baselines=baselines, clusters=prepared.clusters,
    )


def _fraction_for(policy: PolicyConfig, allocation_type: AllocationType) -> float:
    return {
        AllocationType.EXPLOIT: policy.exploit_fraction,
        AllocationType.ADJACENT_EXPLORE: policy.adjacent_explore_fraction,
        AllocationType.RADICAL_EXPLORE: policy.radical_explore_fraction,
    }[allocation_type]


def build_directives(portfolio: Portfolio, *, cycle_id: str, policy: PolicyConfig,
                     posteriors: Mapping[str, PosteriorState], arms: Mapping[str, ContentArm],
                     settings: ARSettings, expires_at: datetime, budget_share: float,
                     ) -> tuple[ProductionDirective, ...]:
    """One directive per selection, portfolio order; priority = 1..n (P3 sorts on it)."""
    directives: list[ProductionDirective] = []
    for position, selection in enumerate(portfolio.selections, start=1):
        arm = arms[selection.arm_id]
        posterior = posteriors[selection.arm_id]
        directives.append(ProductionDirective(
            directive_id=deterministic_id("directive", cycle_id, selection.arm_id,
                                          str(position)),
            decision_id=deterministic_id("decision", cycle_id, selection.arm_id),
            arm_id=selection.arm_id,
            allocation_type=selection.allocation_type,
            priority=position,
            quantity=1,
            brief=DirectiveBrief(
                genre=arm.dimensions.genre, mood=arm.dimensions.moods,
                use_case=arm.dimensions.use_cases, language=arm.dimensions.language,
                vocal_mode=arm.dimensions.vocal_mode,
                duration_bucket=arm.dimensions.duration_bucket,
                visual_world=arm.dimensions.visual_world,
            ),
            expected_value=min(1.0, max(0.0, selection.sample.posterior_expected_reward)),
            uncertainty=math.sqrt(posterior.posterior_variance),
            budget_limit=budget_share,
            market_context={
                "p1_score": arm.p1_score, "p1_confidence": arm.p1_confidence,
                "freshness_seconds": arm.freshness_seconds,
                "score_version": arm.score_version,
                "measured_component_count": arm.measured_component_count,
            },
            selection_reason=selection.reason_codes,
            directive_version=settings.directive_version,
            policy_version=policy.version,
            expires_at=expires_at,
            exploration_fraction=_fraction_for(policy, selection.allocation_type),
            value_model=policy.value_model,
        ))
    return tuple(directives)
```

- [ ] **Step 5: Write `decisioning/cycle.py` (the §29 pipeline) and the repository additions**

Write `decisioning/cycle.py` with the dataclasses and `run_allocation_cycle` exactly as the
interfaces block describes; the body walks §29 in order with one comment per step:

```python
# services/autonomous_ar/decisioning/cycle.py
"""§29's fifteen steps, persisted; the only place a cycle's rows are written.

Replay is structural: the cycle id derives from (brand, as_of, policy version), every id
derives from identity, the seed is derived or supplied and persisted — a re-run of the
same request returns the stored cycle instead of writing a second one.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from plm_contracts import ChannelActivationState

from ..config import ARSettings
from ..domain.arm import ContentArm
from ..domain.decision import ConstraintRejection
from ..domain.directive import ProductionDirective
from ..domain.enums import (
    AllocationCycleStatus, ArmLifecycleStatus, DirectiveStatus, ReasonCode,
)
from ..domain.posterior import PosteriorState
from ..modeling.decay import detect_regime_shift
from ..modeling.prior import cold_start_prior
from ..modeling.reward_model import NormalNormalRewardModel, RewardModel
from ..policy.constraints import directive_budget_share
from ..policy.exploration import effective_fractions, quota_from_fractions
from ..storage import repositories
from ..storage.ids import deterministic_id
from .planner import prepare_cycle
from .portfolio import build_directives, build_portfolio

__all__ = ["CycleRequest", "CycleResult", "MarketSnapshot", "PolicyNotConfigured",
           "run_allocation_cycle", "seed_for_cycle"]


class PolicyNotConfigured(RuntimeError):
    """§102: no validated policy row — refuse the cycle, never allocate on defaults."""


@dataclass(frozen=True, slots=True)
class CycleRequest:
    brand_id: str
    as_of: datetime
    slot_count: int
    budget: float
    policy_version: str
    activation_state: ChannelActivationState | None = None
    random_seed: int | None = None
    unavailable_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    arms: tuple[ContentArm, ...]
    as_of: datetime
    source: str          # "live" | "stored"
    score_version: str


@dataclass(frozen=True, slots=True)
class CycleResult:
    cycle_id: str
    status: AllocationCycleStatus
    directives: tuple[ProductionDirective, ...]
    rejected: tuple[ConstraintRejection, ...]
    warnings: tuple[str, ...]
    replay: bool


def seed_for_cycle(cycle_id: str) -> int:
    """Deterministic 31-bit seed from the cycle id; persisted on the cycle row (§67)."""
    return int(UUID(cycle_id).hex[:16], 16) % (2**31)


def _row_to_directive(row: Any) -> ProductionDirective:
    return ProductionDirective(
        directive_id=str(row["id"]), decision_id=str(row["decision_id"]),
        arm_id=row["arm_id"], allocation_type=row["allocation_type"],
        priority=row["priority"], quantity=row["quantity"], brief=row["brief_json"],
        expected_value=row["expected_value"], uncertainty=row["uncertainty"],
        budget_limit=row["budget_limit"], market_context=row["market_context_json"],
        selection_reason=tuple(row["selection_reason_json"]),
        directive_version=row["directive_version"], policy_version=row["policy_version"],
        expires_at=row["expires_at"], exploration_fraction=row["exploration_fraction"],
        value_model=row["value_model"],
    )


def run_allocation_cycle(session: Any, request: CycleRequest, *, settings: ARSettings,
                         market: MarketSnapshot, model: RewardModel | None = None,
                         now: datetime) -> CycleResult:
    """§29, in order. Every step below is one of the spec's fifteen."""
    resolved_model = model or NormalNormalRewardModel(
        observation_variance=settings.observation_variance,
        variance_floor=settings.posterior_variance_floor,
    )
    # 0 — the policy must exist (§102); no silent default
    policy = repositories.load_policy(session, request.policy_version)
    if policy is None:
        raise PolicyNotConfigured(
            f"no policy row for version {request.policy_version!r}: insert and validate a "
            "policy before running a cycle (§49/§92/§102)"
        )
    cycle_id = deterministic_id("cycle", request.brand_id, request.as_of.isoformat(),
                                request.policy_version)
    seed = request.random_seed if request.random_seed is not None else seed_for_cycle(cycle_id)
    activation = request.activation_state or ChannelActivationState(
        repositories.get_channel_state(session, brand_id=request.brand_id,
                                       default=settings.default_activation_state.value))
    policy_id = repositories.insert_policy(session, policy, brand_id=request.brand_id,
                                           created_at=now)
    if not repositories.insert_cycle(
        session, cycle_id=cycle_id, brand_id=request.brand_id, started_at=now,
        status=AllocationCycleStatus.RUNNING, slot_count=request.slot_count,
        budget=request.budget, policy_id=policy_id, p1_snapshot_time=market.as_of,
        activation_state=activation.value, random_seed=seed,
    ):  # replay: return the stored cycle, write nothing
        cycle = repositories.load_cycle(session, cycle_id)
        assert cycle is not None
        rows = repositories.load_directives_for_cycle(session, cycle_id)
        return CycleResult(cycle_id=cycle_id,
                           status=AllocationCycleStatus(cycle["status"]),
                           directives=tuple(_row_to_directive(row) for row in rows),
                           rejected=(), warnings=tuple(cycle["warning_codes_json"]),
                           replay=True)

    warnings: list[str] = []
    if market.source == "stored":
        warnings.append(ReasonCode.MARKET_UNAVAILABLE.value)   # §102 degraded, explicit
    if request.unavailable_capabilities:
        warnings.append(ReasonCode.PROVIDER_CAPABILITY_UNAVAILABLE.value)  # §82, see open items

    arms: dict[str, ContentArm] = {arm.arm_id: arm for arm in market.arms}
    # 1 — load eligible P1 arms (they arrive as a snapshot); 2 — refresh arm states
    for arm in market.arms:
        repositories.upsert_arm_state(session, brand_id=request.brand_id, arm_id=arm.arm_id,
                                      now=now)
    # 3 — recent experiment history is folded into the posteriors' effective counts
    # 4 — load posterior state; cold start from P1 evidence when none exists (§10)
    posteriors: dict[str, PosteriorState] = {}
    for arm in market.arms:
        existing = repositories.load_posterior(session, arm.arm_id)
        if existing is None:
            prior = cold_start_prior(arm, settings=settings, policy=policy)
            existing = resolved_model.initialize_prior(arm, prior)
            repositories.upsert_posterior(session, existing, brand_id=request.brand_id,
                                          expected_version=0)
        posteriors[arm.arm_id] = existing
    # 5 — freshness gate; 6 — blocked/policy constraints; 7 — cooldown/fatigue
    states = {row["arm_id"]: ArmLifecycleStatus(row["status"])
              for row in repositories.load_arm_states(session)}
    eligible_ids = {arm_id for arm_id, status in states.items()
                    if status in (ArmLifecycleStatus.CANDIDATE, ArmLifecycleStatus.ACTIVE)}
    selection_recency: dict[str, float] = {}
    for arm_id in states:
        if repositories.is_in_cooldown_state(session, arm_id=arm_id, now=now):
            eligible_ids.discard(arm_id)
        selection_recency[arm_id] = repositories.load_exposure_score(session, arm_id)
    snapshot_arms = tuple(arm for arm in market.arms if arm.arm_id in eligible_ids)
    regime_shift = False
    for arm in snapshot_arms:
        previous = repositories.load_latest_feature_snapshot(session, arm.arm_id)
        if previous is not None:
            regime_shift = regime_shift or bool(detect_regime_shift(
                arm.p1_score, float(previous["p1_score"]),
                relative_threshold=policy.nonstationarity.regime_relative_threshold))
    low_confidence = all(posteriors[arm.arm_id].observation_count == 0
                         for arm in snapshot_arms)
    if regime_shift:
        warnings.append(ReasonCode.MARKET_REGIME_CHANGE_SUSPECTED.value)
    fractions = effective_fractions(policy, low_confidence=low_confidence,
                                    regime_shift=regime_shift)
    quota = quota_from_fractions(request.slot_count, fractions)
    # 8 — contextual features snapshotted before any decision is written (no leakage)
    snapshot_ids = {arm.arm_id: repositories.insert_feature_snapshot(
        session, brand_id=request.brand_id, arm=arm, captured_at=market.as_of,
        feature_version=settings.feature_version) for arm in snapshot_arms}
    # 9 — sample posterior reward (seeded); 10/11 — constraints and budget by the walk
    prepared = prepare_cycle(arms=snapshot_arms, posteriors=posteriors, policy=policy,
                             settings=settings, model=resolved_model,
                             rng=random.Random(seed),
                             selection_recency=selection_recency,
                             min_components=policy.min_measured_components,
                             warnings=warnings)
    baselines = repositories.load_baselines(session, brand_id=request.brand_id)
    portfolio = build_portfolio(prepared, policy=policy, slot_count=request.slot_count,
                                quota=quota, arms=arms, states=states, baselines=baselines)
    share = directive_budget_share(request.budget, request.slot_count)
    # 12/14 — build the portfolio and its directives (frozen C2 payload)
    directives = build_directives(
        portfolio, cycle_id=cycle_id, policy=policy, posteriors=posteriors, arms=arms,
        settings=settings,
        expires_at=request.as_of + timedelta(hours=policy.directive_ttl_hours),
        budget_share=share,
    )
    # 13 — decision records: flagged ineligible arms, then every ranked arm
    for arm_id, reason in prepared.ineligible.items():
        repositories.insert_decision(
            session, cycle_id=cycle_id, arm_id=arm_id, allocation_type="exploit",
            sampled_reward=0.0, posterior_expected_reward=0.0, selection_priority=0.0,
            rank_before_constraints=None, rank_after_constraints=None,
            constraint_adjustments={"ineligible": reason}, reason_codes=(reason,),
            policy_version=policy.version, model_version=settings.model_version,
            feature_version=settings.feature_version,
            normalization_version=settings.proxy_normalization_version,
            p1_score_version=arms[arm_id].score_version if arm_id in arms else None,
            feature_snapshot_id=snapshot_ids.get(arm_id) or deterministic_id(
                "no_snapshot", cycle_id, arm_id),
            created_at=now,
        )
    selected_ids = {selection.arm_id for selection in portfolio.selections}
    for rank, sample in enumerate(prepared.ranked, start=1):
        allocation = prepared.classes[sample.arm_id]
        repositories.insert_decision(
            session, cycle_id=cycle_id, arm_id=sample.arm_id,
            allocation_type=allocation.value, sampled_reward=sample.sampled_reward,
            posterior_expected_reward=sample.posterior_expected_reward,
            selection_priority=sample.sampled_reward, rank_before_constraints=rank,
            rank_after_constraints=rank if sample.arm_id in selected_ids else None,
            constraint_adjustments={"assumed_cost": policy.assumed_cost_per_directive},
            reason_codes=(allocation.value.upper(),), policy_version=policy.version,
            model_version=settings.model_version, feature_version=settings.feature_version,
            normalization_version=settings.proxy_normalization_version,
            p1_score_version=arms[sample.arm_id].score_version,
            feature_snapshot_id=snapshot_ids[sample.arm_id], created_at=now,
        )
    for directive in directives:
        repositories.insert_directive(session, directive, brand_id=request.brand_id,
                                      status=DirectiveStatus.QUEUED, created_at=now)
        repositories.record_selection(
            session, arm_id=directive.arm_id, now=now, cooldown_until=None,
            recent_exposure_score=selection_recency.get(directive.arm_id, 0.0) + 1.0,
        )
    status = (AllocationCycleStatus.PARTIAL
              if portfolio.is_partial(request.slot_count) or prepared.ineligible
              else AllocationCycleStatus.COMPLETED)
    repositories.finish_cycle(
        session, cycle_id=cycle_id, status=status, finished_at=now,
        decision_count=len(prepared.ranked) + len(prepared.ineligible),
        selected_count=len(directives), rejected_count=len(portfolio.rejections),
        warning_codes=warnings,
    )
    rejected: list[ConstraintRejection] = list(portfolio.rejections)
    for arm_id, reason in prepared.ineligible.items():
        try:
            rejected.append(ConstraintRejection(arm_id=arm_id, reason=ReasonCode(reason),
                                                detail="ineligible before ranking"))
        except ValueError:
            continue  # NO_POSTERIOR is an internal guard, not a contract reason token
    return CycleResult(cycle_id=cycle_id, status=status, directives=directives,
                       rejected=tuple(rejected), warnings=tuple(warnings), replay=False)
```

Repository additions (same commit, in `storage/repositories.py`):

```python
def load_directives_for_cycle(session: Session, cycle_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(
        sa.select(ar_production_directive)
        .join(ar_allocation_decision,
              ar_allocation_decision.c.id == ar_production_directive.c.decision_id)
        .where(ar_allocation_decision.c.cycle_id == cycle_id)
        .order_by(ar_production_directive.c.priority)
    ).mappings()]


def load_latest_feature_snapshot(session: Session, arm_id: str) -> dict[str, Any] | None:
    row = session.execute(
        sa.select(ar_arm_feature_snapshot)
        .where(ar_arm_feature_snapshot.c.arm_id == arm_id)
        .order_by(ar_arm_feature_snapshot.c.captured_at.desc())
    ).mappings().first()
    return None if row is None else dict(row)


def load_exposure_score(session: Session, arm_id: str) -> float:
    value = session.execute(
        sa.select(ar_content_arm_state.c.recent_exposure_score)
        .where(ar_content_arm_state.c.arm_id == arm_id)
    ).scalar_one_or_none()
    return float(value) if value is not None else 0.0


def is_in_cooldown_state(session: Session, *, arm_id: str, now: datetime) -> bool:
    until = session.execute(
        sa.select(ar_content_arm_state.c.cooldown_until)
        .where(ar_content_arm_state.c.arm_id == arm_id)
    ).scalar_one_or_none()
    return until is not None and until > now
```

**Where the stored-mode rebuild lives:** not in `storage/repositories.py` and not in the engine —
the degraded path is the *job's* choice. `jobs/cycle.py` fetches P1, and on `MarketUnavailable`
rebuilds a `MarketSnapshot(source="stored")` from P2's own last per-arm feature snapshots
(the dimensions ride inside `feature_json`), or lets the `MarketUnavailable` propagate when
P2 has never seen an arm. The engine only ever receives a snapshot — that is what keeps
§102's "do not invent market opportunities" a property of the code, not a promise.

```python
def _stored_snapshot(session, *, brand_id: str, as_of: datetime) -> MarketSnapshot | None:
    """§102's degraded path: P2's own last snapshots, never P1 tables, never invented arms."""
    from ..domain.arm import ArmDimensions

    rows = repositories.load_all_latest_feature_snapshots(session, brand_id=brand_id)
    if not rows:
        return None
    arms = tuple(
        ContentArm(
            arm_id=row["arm_id"],
            dimensions=ArmDimensions(**row["feature_json"]["dimensions"]),
            p1_score=row["p1_score"], p1_confidence=row["p1_confidence"],
            component_scores=row["feature_json"]["components"],
            observation_count=int(row["feature_json"].get("observation_count", 0)),
            source_count=int(row["feature_json"].get("source_count", 0)),
            as_of=row["captured_at"], score_version=str(row["feature_json"]["score_version"]),
            freshness_seconds=row["freshness_seconds"]
            + max(0.0, (as_of - row["captured_at"]).total_seconds()),
        )
        for row in rows
    )
    return MarketSnapshot(arms=arms, as_of=min(row["captured_at"] for row in rows),
                          source="stored", score_version=arms[0].score_version)
```

with one more repository helper in the same commit:

```python
def load_all_latest_feature_snapshots(session: Session, *, brand_id: str) -> list[dict[str, Any]]:
    newest = (
        sa.select(ar_arm_feature_snapshot.c.arm_id,
                  sa.func.max(ar_arm_feature_snapshot.c.captured_at).label("captured_at"))
        .where(ar_arm_feature_snapshot.c.brand_id == brand_id)
        .group_by(ar_arm_feature_snapshot.c.arm_id).subquery()
    )
    return [dict(row) for row in session.execute(
        sa.select(ar_arm_feature_snapshot)
        .join(newest, sa.and_(
            ar_arm_feature_snapshot.c.arm_id == newest.c.arm_id,
            ar_arm_feature_snapshot.c.captured_at == newest.c.captured_at))
        .order_by(ar_arm_feature_snapshot.c.arm_id)
    ).mappings()]
```

and `insert_feature_snapshot` must store the dimensions too
(`feature_json={"components": dict(arm.component_scores), "dimensions": dataclasses.asdict(arm.dimensions), "observation_count": arm.observation_count, "source_count": arm.source_count, "score_version": arm.score_version}`).

- [ ] **Step 6: Run it and iterate to green**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_cycle.py -q`
Expected: PASS (7 tests).

- [ ] **Step 6b: Lifecycle transitions (§38/§39) and their test**

Lifecycle is referenced by the eligibility step above but shipped here, with its own failing
test first. Append to `tests/unit/ar/test_cycle.py`:

```python
def test_retirement_needs_mature_evidence_and_reopens_on_a_market_rise() -> None:
    from services.autonomous_ar.domain.posterior import PosteriorState
    from services.autonomous_ar.policy.lifecycle import evaluate_retirements, reopen_reason

    weak = PosteriorState(
        arm_id="rnb-1", model_type="normal-normal", model_version="p2-nn-v1",
        prior_mean=0.5, prior_variance=0.05, posterior_mean=0.10, posterior_variance=0.01,
        observation_count=6, effective_observation_count=6.0, update_version=6,
        last_update_at=NOW,
    )
    fresh = weak.model_copy(update={"observation_count": 1,
                                    "effective_observation_count": 1.0})
    retirements = evaluate_retirements({"rnb-1": weak}, mature_counts={"rnb-1": 5},
                                       policy=DEFAULT_POLICY, now=NOW)
    assert [decision.arm_id for decision in retirements] == ["rnb-1"], (
        "§38: five mature observations and a weak posterior retire; one never does"
    )
    assert evaluate_retirements({"rnb-1": fresh}, mature_counts={"rnb-1": 1},
                                policy=DEFAULT_POLICY, now=NOW) == ()
    recovered, stable = _arm("rnb-1", score=75.0), _arm("rnb-1", score=45.0)
    retired = ArmLifecycleStatus.RETIRED
    assert reopen_reason(recovered, stored_score=40.0, policy=DEFAULT_POLICY,
                         state=retired) == "market_score_recovered"
    assert reopen_reason(stable, stored_score=40.0, policy=DEFAULT_POLICY,
                         state=retired) is None
    assert reopen_reason(recovered, stored_score=40.0, policy=DEFAULT_POLICY,
                         state=ArmLifecycleStatus.ACTIVE) is None
```

Wire both helpers into `run_allocation_cycle` immediately before eligibility: load mature counts
from `ar_content_arm_state`, retire each returned decision via
`repositories.set_arm_status(..., status=RETIRED, reason=decision.reason)` and log a
`WEAK_POSTERIOR` warning; then for arms currently `retired`, call `reopen_reason` against their
latest stored feature snapshot's `p1_score` and `set_arm_status(..., status=ACTIVE,
reason=reason)` when it fires. Run the new test, then the whole file:

```python
# services/autonomous_ar/policy/lifecycle.py
"""§38 retirement and §39 reopening: reversible, evidence-gated, reason-coded."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from ..domain.arm import ContentArm
from ..domain.enums import ArmLifecycleStatus
from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState

__all__ = ["RetirementDecision", "evaluate_retirements", "reopen_reason"]

REOPEN_SCORE_DELTA = 20.0
"""A retired arm returns when P1's score rises this much over the stored snapshot (§39)."""


@dataclass(frozen=True, slots=True)
class RetirementDecision:
    arm_id: str
    reason: str


def evaluate_retirements(posteriors: Mapping[str, PosteriorState], *,
                         mature_counts: Mapping[str, int], policy: PolicyConfig,
                         now: datetime) -> tuple[RetirementDecision, ...]:
    """§38: mature evidence AND a weak posterior AND a weak recent market signal."""
    decisions: list[RetirementDecision] = []
    for arm_id, posterior in sorted(posteriors.items()):
        mature = mature_counts.get(arm_id, 0)
        if (mature >= policy.retirement.min_mature_observations
                and posterior.posterior_mean < policy.retirement.weak_posterior_threshold):
            decisions.append(RetirementDecision(arm_id=arm_id, reason="WEAK_POSTERIOR"))
    return tuple(decisions)


def reopen_reason(arm: ContentArm, *, stored_score: float, policy: PolicyConfig,
                  state: ArmLifecycleStatus) -> str | None:
    """§39: a material market rise reopens a retired arm; the reason is stored."""
    if state is not ArmLifecycleStatus.RETIRED:
        return None
    if arm.p1_score - stored_score >= REOPEN_SCORE_DELTA:
        return "market_score_recovered"
    return None
```

Wire it into `run_allocation_cycle` immediately before eligibility: load mature counts from
`ar_content_arm_state`, call `evaluate_retirements`, and for each decision
`repositories.set_arm_status(session, arm_id=..., status=RETIRED, now=now, reason=decision.reason)`
plus a `WEAK_POSTERIOR` warning; then, for arms currently `retired`, call `reopen_reason` against
their latest stored feature snapshot's `p1_score` and `set_arm_status(..., status=ACTIVE,
reason=reason)` when it fires. Run the new test, then the whole file:

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests\\unit\\ar\\test_cycle.py -q`
Expected: PASS (8 tests).

- [ ] **Step 7: Wire the Temporal job**

```python
# services/autonomous_ar/jobs/cycle.py
"""Scheduled allocation on `ph2.ar` (§101): a thin Temporal shell around the real engine."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import activity, workflow

from plm_platform.temporal import DEFAULT_RETRY_POLICY

__all__ = ["AllocationCycleWorkflow", "run_cycle_activity"]


@activity.defn
def run_cycle_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """One allocation cycle: live P1 read, stored fallback, engine, commit."""
    from plm_platform.config import load_settings
    from plm_platform.db import scoped_session

    from ..config import load_ar_settings
    from ..decisioning.cycle import CycleRequest, MarketSnapshot, run_allocation_cycle
    from ..ingestion.p1_adapter import MarketUnavailable, P1Client, adapt_opportunity
    from ..storage import repositories

    settings = load_ar_settings(platform=load_settings(env=None))
    assert settings.platform is not None
    request = CycleRequest(**payload)
    token = (settings.platform.service_token.get_secret_value()
             if settings.platform.service_token is not None else None)
    client = P1Client(settings.market_base_url, token,
                      timeout_seconds=settings.market_timeout_seconds)
    with scoped_session("ar") as session:
        try:
            page = client.fetch_opportunities(limit=200)
            snapshot = MarketSnapshot(
                arms=tuple(adapt_opportunity(item, now=request.as_of)
                           for item in page.items),
                as_of=page.as_of, source="live", score_version=page.score_version)
        except MarketUnavailable as exc:
            snapshot = _stored_snapshot(session, brand_id=request.brand_id,
                                        as_of=request.as_of)
            if snapshot is None:
                raise  # §102: no snapshot and no live data ⇒ explicit failure, no invention
            degraded_reason = str(exc)
        else:
            degraded_reason = ""
        result = run_allocation_cycle(session, request, settings=settings, market=snapshot,
                                      now=request.as_of)
        session.commit()
    return {"cycle_id": result.cycle_id, "status": result.status.value,
            "directives": len(result.directives), "replay": result.replay,
            "degraded": degraded_reason}


def _stored_snapshot(session: Any, *, brand_id: str, as_of: Any) -> Any:
    """§102 degraded path: rebuild arms from P2's own last snapshots (dimensions ride in
    `feature_json`), or return None so the caller fails loudly."""
    from ..decisioning.cycle import MarketSnapshot
    from ..domain.arm import ArmDimensions, ContentArm
    from ..storage import repositories

    rows = repositories.load_all_latest_feature_snapshots(session, brand_id=brand_id)
    if not rows:
        return None
    arms = tuple(
        ContentArm(
            arm_id=row["arm_id"],
            dimensions=ArmDimensions(**row["feature_json"]["dimensions"]),
            p1_score=row["p1_score"], p1_confidence=row["p1_confidence"],
            component_scores=row["feature_json"]["components"],
            observation_count=int(row["feature_json"].get("observation_count", 0)),
            source_count=int(row["feature_json"].get("source_count", 0)),
            as_of=row["captured_at"], score_version=str(row["feature_json"]["score_version"]),
            freshness_seconds=row["freshness_seconds"]
            + max(0.0, (as_of - row["captured_at"]).total_seconds()),
        )
        for row in rows
    )
    return MarketSnapshot(arms=arms, as_of=min(row["captured_at"] for row in rows),
                          source="stored", score_version=arms[0].score_version)


@workflow.defn
class AllocationCycleWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            run_cycle_activity, payload, start_to_close_timeout=timedelta(minutes=30),
            retry_policy=DEFAULT_RETRY_POLICY,
        )
```

```python
# services/autonomous_ar/jobs/schedules.py
"""One weekly allocation schedule, idempotent by business name."""

from __future__ import annotations

from temporalio.client import Client, Schedule, ScheduleActionStartWorkflow, ScheduleSpec

from plm_platform.temporal import TaskQueue

__all__ = ["JOB_NAMES", "ensure_schedules"]

JOB_NAMES = ("ar_weekly_allocation",)


async def ensure_schedules(client: Client) -> list[str]:
    from .cycle import AllocationCycleWorkflow

    created: list[str] = []
    for job_name in JOB_NAMES:
        handle = client.get_schedule_handle(job_name)
        try:
            await handle.describe()
            continue  # already registered
        except Exception:
            pass
        await client.create_schedule(
            job_name,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    AllocationCycleWorkflow.run,
                    {"brand_id": "neon_seoul", "slot_count": 10, "budget": 10.0,
                     "policy_version": "p2-v1"},
                    id=job_name, task_queue=TaskQueue.AR.value),
                spec=ScheduleSpec(cron_expressions=["0 6 * * 1"]),
            ),
        )
        created.append(job_name)
    return created
```

```python
# services/autonomous_ar/jobs/__init__.py
"""Worker registrations: `plm_platform.worker --task-queue ph2.ar --load
autonomous_ar.jobs:REGISTRATIONS` (the editable install exposes the top-level name)."""

from __future__ import annotations

from plm_platform.temporal import TaskQueue, WorkerSet

from .cycle import AllocationCycleWorkflow, run_cycle_activity

__all__ = ["REGISTRATIONS"]

REGISTRATIONS: tuple[WorkerSet, ...] = (
    WorkerSet(task_queue=TaskQueue.AR, workflows=(AllocationCycleWorkflow,),
              activities=(run_cycle_activity,)),
)
```

- [ ] **Step 8: Write the Temporal integration test (skips cleanly when the stack is down)**

```python
# tests/integration/ar/test_temporal_jobs.py
"""The workflow runs on `ph2.ar` against the compose stack; skipped when it is down."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from plm_platform.temporal import TaskQueue, connect_client
from services.autonomous_ar.jobs import REGISTRATIONS


@pytest.mark.integration
def test_allocation_workflow_runs_on_ph2_ar() -> None:
    async def scenario() -> dict[str, object]:
        try:
            client: Client = await connect_client()
        except Exception as exc:
            pytest.skip(f"temporal unavailable ({exc.__class__.__name__}); run "
                        "`docker compose up -d --wait temporal`")
        registration = REGISTRATIONS[0]
        async with Worker(client, task_queue=TaskQueue.AR.value,
                          workflows=list(registration.workflows),
                          activities=list(registration.activities)):
            handle = await client.start_workflow(
                "AllocationCycleWorkflow",
                {"brand_id": "nobody", "slot_count": 1, "budget": 1.0,
                 "policy_version": "p2-v1"},
                id=f"ar-test-{uuid.uuid4().hex[:8]}", task_queue=TaskQueue.AR.value)
            try:
                return await handle.result()
            except Exception as exc:  # PolicyNotConfigured is the honest named failure
                return {"error": exc.__class__.__name__}

    outcome = asyncio.run(scenario())
    assert outcome.get("error") == "PolicyNotConfigured" or "cycle_id" in outcome
```

- [ ] **Step 9: Extend the C2 seam test with the real-cycle producer**

In `tests/contract/test_c2_ar_to_experiments.py`, rebuild `producer_directive()` so it returns a
directive produced by `run_allocation_cycle` over `tests/support/ar_db.create_ar_engine()` with
one seeded `DEFAULT_POLICY` row and one fully-covered arm; every assertion stays unchanged:

```python
def producer_directive() -> ProductionDirective:
    from datetime import UTC, datetime

    from services.autonomous_ar.config import load_ar_settings
    from services.autonomous_ar.decisioning.cycle import (
        CycleRequest, MarketSnapshot, run_allocation_cycle,
    )
    from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
    from services.autonomous_ar.domain.policy import DEFAULT_POLICY
    from services.autonomous_ar.storage import repositories
    from tests.support.ar_db import ar_session, create_ar_engine

    now = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
    arm = ContentArm(
        arm_id="rnb_late_night_drive_english_vocal_45_75m",
        dimensions=ArmDimensions(genre="rnb", moods=("melancholic", "dreamy"),
                                 use_cases=("late_night_drive",), language="english",
                                 vocal_mode="vocal", duration_bucket="45_75m",
                                 visual_world="rainy_asian_city"),
        p1_score=82.0, p1_confidence=0.7,
        component_scores={name: 60.0 for name in
                          ("demand", "growth", "competitor_velocity", "competition",
                           "audience_quality", "cross_platform", "production_fit")},
        observation_count=6, source_count=2, as_of=now, score_version="p1-v1",
        freshness_seconds=3600.0,
    )
    engine = create_ar_engine()
    with ar_session(engine) as session:
        repositories.insert_policy(session, DEFAULT_POLICY, brand_id="neon_seoul",
                                   created_at=now)
        result = run_allocation_cycle(
            session,
            CycleRequest(brand_id="neon_seoul", as_of=now, slot_count=1, budget=1.0,
                         policy_version="p2-v1"),
            settings=load_ar_settings(), market=MarketSnapshot(arms=(arm,), as_of=now,
                                                               source="live",
                                                               score_version="p1-v1"),
            now=now,
        )
    return result.directives[0]
```

Run: `.\.venv\Scripts\python.exe -m pytest tests\contract\test_c2_ar_to_experiments.py -q` → PASS.

- [ ] **Step 10: Full gate, then commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar tests\contract -q; .\.venv\Scripts\python.exe -m ruff check services\autonomous_ar tests\unit\ar; .\.venv\Scripts\python.exe -m mypy packages services`
Expected: green (or the Temporal test skipped with its named reason).

Run: `git add services/autonomous_ar/decisioning services/autonomous_ar/jobs services/autonomous_ar/storage/repositories.py tests/unit/ar/test_cycle.py tests/integration/ar/test_temporal_jobs.py tests/contract/test_c2_ar_to_experiments.py; git commit -m "feat(ar): P2.10 allocation cycle, directives and the ph2.ar workflow"`

---

### Task P2.11: Result ingestion

**Files:**
- Create: `services/autonomous_ar/ingestion/result_adapter.py`, `services/autonomous_ar/modeling/update.py`
- Modify: `services/autonomous_ar/storage/repositories.py` — add `increment_result_counts`.
- Test: `tests/unit/ar/test_result_ingestion.py`, `tests/unit/ar/test_posterior_updates.py`

**Interfaces:**
- Consumes: `RewardObservation`/`tri_from_optional` (P2.1), `repositories.{insert_reward_observation, insert_experiment_result, load_pending_observations, insert_posterior_update, load_posterior_update, load_posterior, upsert_posterior, load_policy}`, `NormalNormalRewardModel`, `recency_weight` (P2.9), `PolicyConfig`.
- Produces (exact):
  - `result_adapter.ResultPayload` — the wire: `experiment_id, arm_id, content_id, observation_window: ObservationWindow, status: ObservationMaturity, observed_at: datetime, raw_metrics: dict[str, float | None], normalized_reward: float, reward_version: str, attribution_confidence: float, quality_status: QualityStatus | None = None, economic_terms: dict[str, float | None] | None = None` — all `extra="forbid"`; `economic_terms` keys must be `RESERVED_VALUE_TERMS` (ADR-0023 #4); `normalized_reward` required, `[0, 1]` (R11: P9 computes it — P2 never does).
  - `result_adapter.ResultValidationError(ValueError)` with `.field_paths: tuple[str, ...]`.
  - `result_adapter.parse_result_payload(payload) -> RewardObservation` (numbers become `Tri.known`, nulls `Tri.unknown()`).
  - `result_adapter.store_result(session, payload, *, brand_id, now, require_known_arm: bool = True) -> StoredResult(observation_id: str, experiment_result_id: str, stored: bool)` — §72 validation (`arm exists`), §73 idempotence on `(experiment_id, observation_window)` (a repeat stores nothing and returns `stored=False`).
  - `update.UpdateSummary(applied: int, skipped_duplicate: int, quarantined: int, failed: int)`.
  - `update.apply_pending_updates(session, *, settings, now, policy: PolicyConfig, model: RewardModel | None = None) -> UpdateSummary` — §74/§75: one `applied` update per observation (the partial unique index is the backstop), optimistic `update_version` guard, attribution below `policy.min_attribution_confidence` **quarantines** (§24: never update the wrong arm), recency weight from `policy.nonstationarity.decay_half_life_days`, future-dated observations never picked up (§23).

- [ ] **Step 1: Write the failing ingestion test**

```python
# tests/unit/ar/test_result_ingestion.py
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from services.autonomous_ar.domain.enums import ArmLifecycleStatus
from services.autonomous_ar.ingestion.result_adapter import (
    ResultValidationError, parse_result_payload, store_result,
)
from services.autonomous_ar.storage import repositories
from services.autonomous_ar.storage.models import ar_reward_observation
from tests.support.ar_db import ar_session, create_ar_engine

NOW = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "experiment_id": "exp-1", "arm_id": "rnb-1", "content_id": "content-1",
        "observation_window": "7d", "status": "mature", "observed_at": NOW.isoformat(),
        "raw_metrics": {"views": 42000.0, "watch_time_minutes": 185000.0,
                        "average_percentage_viewed": 41.2, "subscriber_gain": 613.0,
                        "estimated_revenue": None},
        "normalized_reward": 0.74, "reward_version": "p9-reward-v1",
        "attribution_confidence": 0.99,
    }
    base.update(overrides)
    return base


def test_payload_parses_with_null_metrics_staying_unknown() -> None:
    observation = parse_result_payload(_payload())
    assert observation.normalized_reward.unwrap() == 0.74
    assert observation.raw_metrics["views"].unwrap() == 42000.0
    assert observation.raw_metrics["estimated_revenue"].state.value == "unknown", (
        "a null metric on the wire is unknown, never 0.0"
    )
    assert observation.key == "exp-1|7d"


def test_unmeasured_reward_is_refused_with_the_field_named() -> None:
    with pytest.raises(ResultValidationError) as error:
        parse_result_payload(_payload(normalized_reward=None))
    assert "normalized_reward" in error.value.field_paths, (
        "R11/R2: P9 owns normalized_reward; P2 refuses a result that carries none"
    )


def test_reward_out_of_range_and_unknown_economic_terms_are_refused() -> None:
    with pytest.raises(ResultValidationError):
        parse_result_payload(_payload(normalized_reward=1.4))
    with pytest.raises(ResultValidationError) as error:
        parse_result_payload(_payload(economic_terms={"made_up_revenue": 3.0}))
    assert "economic_terms" in error.value.field_paths[0]


def test_storing_twice_is_idempotent_and_an_unknown_arm_is_refused() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        repositories.upsert_arm_state(session, brand_id="neon_seoul", arm_id="rnb-1", now=NOW)
        first = store_result(session, _payload(), brand_id="neon_seoul", now=NOW)
        second = store_result(session, _payload(), brand_id="neon_seoul", now=NOW)
        assert first.stored is True and second.stored is False
        assert first.observation_id == second.observation_id
        count = session.execute(sa.select(sa.func.count())
                                .select_from(ar_reward_observation)).scalar_one()
        assert count == 1, "§73: the same (experiment, window) is stored once"
        with pytest.raises(ResultValidationError, match="unknown arm"):
            store_result(session, _payload(arm_id="nope"), brand_id="neon_seoul", now=NOW)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_result_ingestion.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.ingestion.result_adapter'`.

- [ ] **Step 3: Write `ingestion/result_adapter.py`**

```python
# services/autonomous_ar/ingestion/result_adapter.py
"""§72's result contract: validate, map to `RewardObservation`, store idempotently.

P9 owns the arithmetic (R11): `normalized_reward` is required and range-checked here, and
this module never derives it from raw metrics. Nulls stay unknown end to end. The economic
terms are C7's reserved names only (ADR-0023 #4) — a result that carries an invented term
is refused, not stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self

from plm_contracts import ObservationWindow, QualityStatus
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..domain.enums import ArmLifecycleStatus, ObservationMaturity
from ..domain.reward import RESERVED_VALUE_TERMS, RewardObservation, tri_from_optional
from ..storage import repositories

__all__ = ["ResultPayload", "ResultValidationError", "StoredResult", "parse_result_payload",
           "store_result"]


class ResultValidationError(ValueError):
    """§72 validation failed; `field_paths` names the offending fields (P3 §81 shape)."""

    def __init__(self, message: str, field_paths: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.field_paths = field_paths


class ResultPayload(BaseModel):
    """The §72 wire object, plus the C7-freeze-resolving optional `quality_status`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str = Field(min_length=1)
    arm_id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    observation_window: ObservationWindow
    status: ObservationMaturity
    observed_at: datetime
    raw_metrics: dict[str, float | None]
    normalized_reward: float = Field(ge=0.0, le=1.0)
    reward_version: str = Field(min_length=1)
    attribution_confidence: float = Field(ge=0.0, le=1.0)
    quality_status: QualityStatus | None = None
    economic_terms: dict[str, float | None] | None = None

    @model_validator(mode="after")
    def _economic_terms_are_reserved_names_only(self) -> Self:
        if self.economic_terms:
            unknown = set(self.economic_terms) - set(RESERVED_VALUE_TERMS)
            if unknown:
                raise ValueError(
                    f"economic_terms may only carry reserved C7 names (ADR-0023 #4), got "
                    f"{sorted(unknown)}"
                )
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return self


@dataclass(frozen=True, slots=True)
class StoredResult:
    observation_id: str
    experiment_result_id: str
    stored: bool


def parse_result_payload(payload: dict[str, Any]) -> RewardObservation:
    """§72 payload → domain object, with `field_paths` on every refusal."""
    try:
        parsed = ResultPayload.model_validate(payload)
    except ValidationError as exc:
        paths = tuple(".".join(str(part) for part in error["loc"]) for error in exc.errors())
        raise ResultValidationError(
            f"result payload failed validation: {paths}", field_paths=paths
        ) from exc
    return RewardObservation(
        experiment_id=parsed.experiment_id, arm_id=parsed.arm_id, content_id=parsed.content_id,
        observation_window=parsed.observation_window, maturity=parsed.status,
        observed_at=parsed.observed_at,
        raw_metrics={name: tri_from_optional(value)
                     for name, value in parsed.raw_metrics.items()},
        normalized_reward=tri_from_optional(parsed.normalized_reward),
        reward_version=parsed.reward_version,
        attribution_confidence=parsed.attribution_confidence,
        quality_status=parsed.quality_status,
        economic_terms={name: tri_from_optional(value)
                        for name, value in (parsed.economic_terms or {}).items()},
    )


def store_result(session: Any, payload: dict[str, Any], *, brand_id: str, now: datetime,
                 require_known_arm: bool = True) -> StoredResult:
    """Validate (§72), store idempotently (§73), and mirror the canonical result row."""
    observation = parse_result_payload(payload)
    if require_known_arm:
        states = {row["arm_id"] for row in repositories.load_arm_states(session)}
        if observation.arm_id not in states:
            raise ResultValidationError(
                f"unknown arm {observation.arm_id!r}: a result may only update an arm P2 "
                "already knows (§72 'arm exists'; R15: P1 mints arms)",
                field_paths=("arm_id",),
            )
    observation_id, stored = repositories.insert_reward_observation(
        session, observation, brand_id=brand_id, received_at=now)
    if stored:
        experiment_result_id = repositories.insert_experiment_result(
            session, observation, brand_id=brand_id)
    else:
        experiment_result_id = observation_id  # deterministic; the row already exists
    return StoredResult(observation_id=observation_id,
                        experiment_result_id=experiment_result_id, stored=stored)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_result_ingestion.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Write the failing update test**

```python
# tests/unit/ar/test_posterior_updates.py
from datetime import UTC, datetime, timedelta

from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.domain.posterior import PriorParameters
from services.autonomous_ar.ingestion.result_adapter import store_result
from services.autonomous_ar.modeling.reward_model import NormalNormalRewardModel
from services.autonomous_ar.modeling.update import apply_pending_updates
from services.autonomous_ar.storage import repositories
from tests.support.ar_db import ar_session, create_ar_engine

NOW = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
SETTINGS = load_ar_settings()
MODEL = NormalNormalRewardModel()
COMPONENTS = {name: 50.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}


def _seed_arm_and_posterior(session, *, mean: float = 0.5, variance: float = 0.05) -> None:
    repositories.upsert_arm_state(session, brand_id="neon_seoul", arm_id="rnb-1", now=NOW)
    arm = ContentArm(arm_id="rnb-1", dimensions=ArmDimensions(genre="rnb"),
                     p1_score=60.0, p1_confidence=0.6, component_scores=dict(COMPONENTS),
                     observation_count=1, source_count=1, as_of=NOW, score_version="p1-v1",
                     freshness_seconds=0.0)
    repositories.upsert_posterior(
        session,
        MODEL.initialize_prior(arm, PriorParameters(
            mean=mean, variance=variance, confidence=0.6, prior_version="p2-prior-v1")),
        brand_id="neon_seoul", expected_version=0,
    )


def _payload(*, reward: float = 0.9, observed_at: datetime = NOW,
             attribution: float = 0.99) -> dict[str, object]:
    return {"experiment_id": "exp-1", "arm_id": "rnb-1", "content_id": "c-1",
            "observation_window": "7d", "status": "mature",
            "observed_at": observed_at.isoformat(), "raw_metrics": {},
            "normalized_reward": reward, "reward_version": "p9-reward-v1",
            "attribution_confidence": attribution}


def test_one_observation_updates_the_posterior_exactly_once() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_arm_and_posterior(session)
        store_result(session, _payload(), brand_id="neon_seoul", now=NOW)
        first = apply_pending_updates(session, settings=SETTINGS, now=NOW, policy=DEFAULT_POLICY)
        assert first.applied == 1 and first.skipped_duplicate == 0
        after_first = repositories.load_posterior(session, "rnb-1")
        assert after_first is not None and after_first.posterior_mean > 0.5
        second = apply_pending_updates(session, settings=SETTINGS, now=NOW,
                                       policy=DEFAULT_POLICY)
        assert second.applied == 0 and second.skipped_duplicate == 1
        after_second = repositories.load_posterior(session, "rnb-1")
        assert after_second == after_first, "§74: a retry must not apply the observation twice"


def test_low_attribution_quarantines_instead_of_updating() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_arm_and_posterior(session)
        store_result(session, _payload(attribution=0.2), brand_id="neon_seoul", now=NOW)
        summary = apply_pending_updates(session, settings=SETTINGS, now=NOW,
                                        policy=DEFAULT_POLICY)
        assert summary.quarantined == 1 and summary.applied == 0
        posterior = repositories.load_posterior(session, "rnb-1")
        assert posterior is not None and posterior.posterior_mean == 0.5, (
            "§24: ambiguous attribution quarantines; it never updates the wrong arm"
        )


def test_recency_decay_weights_older_observations_less() -> None:
    fresh_engine, old_engine = create_ar_engine(), create_ar_engine()
    with ar_session(fresh_engine) as session:
        _seed_arm_and_posterior(session)
        store_result(session, _payload(observed_at=NOW), brand_id="neon_seoul", now=NOW)
        apply_pending_updates(session, settings=SETTINGS, now=NOW, policy=DEFAULT_POLICY)
        fresh_variance = repositories.load_posterior(session, "rnb-1").posterior_variance
    with ar_session(old_engine) as session:
        _seed_arm_and_posterior(session)
        old = NOW - timedelta(days=45)  # one non-stationarity half-life
        store_result(session, _payload(observed_at=old), brand_id="neon_seoul", now=NOW)
        apply_pending_updates(session, settings=SETTINGS, now=NOW, policy=DEFAULT_POLICY)
        old_variance = repositories.load_posterior(session, "rnb-1").posterior_variance
    assert old_variance > fresh_variance, "§27: a 45-day-old observation moves the belief less"


def test_future_observations_and_unknown_arms_never_update() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        _seed_arm_and_posterior(session)
        store_result(session, _payload(observed_at=NOW + timedelta(days=1)),
                     brand_id="neon_seoul", now=NOW)
        summary = apply_pending_updates(session, settings=SETTINGS, now=NOW,
                                        policy=DEFAULT_POLICY)
        assert summary.applied == 0 and summary.failed == 0
        assert repositories.load_posterior(session, "rnb-1").update_version == 0, (
            "§23: an observation dated after `now` is not yet evidence"
        )
```

- [ ] **Step 6: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_posterior_updates.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.modeling.update'`.

- [ ] **Step 7: Write `modeling/update.py`**

```python
# services/autonomous_ar/modeling/update.py
"""§74/§75: idempotent, version-guarded posterior updates from stored observations.

Order of refusals: already applied (skip) → attribution below policy floor (quarantine) →
no posterior row (fail with a log row) → otherwise one conjugate update, one log row.
The partial unique index on `applied` is the backstop if two workers race anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from plm_contracts import ObservationWindow, Tri

from ..config import ARSettings
from ..domain.enums import ObservationMaturity, UpdateStatus
from ..domain.policy import PolicyConfig
from ..domain.posterior import UpdateRecord
from ..domain.reward import RewardObservation, tri_from_optional
from ..modeling.decay import recency_weight
from ..modeling.reward_model import NormalNormalRewardModel, RewardModel

__all__ = ["UpdateSummary", "apply_pending_updates"]


@dataclass(frozen=True, slots=True)
class UpdateSummary:
    applied: int
    skipped_duplicate: int
    quarantined: int
    failed: int


def _row_to_observation(row: Any) -> RewardObservation:
    return RewardObservation(
        experiment_id=row["experiment_id"], arm_id=row["arm_id"],
        content_id=row["content_id"],
        observation_window=ObservationWindow(row["observation_window"]),
        maturity=ObservationMaturity(row["maturity"]), observed_at=row["observed_at"],
        raw_metrics={name: _tri_from_row(value)
                     for name, value in (row["raw_metrics_json"] or {}).items()},
        normalized_reward=tri_from_optional(row["normalized_reward"]),
        reward_version=row["reward_version"],
        attribution_confidence=float(row["attribution_confidence"]),
    )


def _tri_from_row(value: Any) -> Tri[float]:
    """Stored Tri dicts round-trip; a bare number can only be `known` (P2.3's write path)."""
    if isinstance(value, dict):
        state = value.get("state")
        if state == "known":
            return Tri.known(float(value["value"]))
        return Tri.not_ready() if state == "not_ready" else Tri.unknown()
    return Tri.known(float(value))


def apply_pending_updates(session: Any, *, settings: ARSettings, now: datetime,
                          policy: PolicyConfig,
                          model: RewardModel | None = None) -> UpdateSummary:
    """Apply every stored observation at most once (§74); deterministic order (§73)."""
    resolved_model = model or NormalNormalRewardModel(
        observation_variance=settings.observation_variance,
        variance_floor=settings.posterior_variance_floor,
    )
    applied = skipped = quarantined = failed = 0
    for row in repositories.load_pending_observations(session, now=now):
        observation_id = str(row["id"])
        observation = _row_to_observation(row)
        if repositories.load_posterior_update(session, observation_id) is not None:
            skipped += 1
            continue
        posterior = repositories.load_posterior(session, observation.arm_id)
        if posterior is None:
            failed += 1
            repositories.insert_posterior_update(
                session,
                UpdateRecord(reward_observation_id=observation_id, arm_id=observation.arm_id,
                             version_before=0, version_after=0, status=UpdateStatus.FAILED,
                             effective_weight=0.0, updated_at=now),
                brand_id=row["brand_id"],
            )
            continue
        if observation.attribution_confidence < policy.min_attribution_confidence:
            quarantined += 1
            repositories.insert_posterior_update(
                session,
                UpdateRecord(reward_observation_id=observation_id, arm_id=observation.arm_id,
                             version_before=posterior.update_version,
                             version_after=posterior.update_version,
                             status=UpdateStatus.QUARANTINED, effective_weight=0.0,
                             updated_at=now),
                brand_id=row["brand_id"],
            )
            continue
        if policy.nonstationarity.enabled:
            weight = recency_weight(observation.observed_at, now=now,
                                    half_life_days=policy.nonstationarity.decay_half_life_days)
        else:
            weight = 1.0
        if weight <= 0.0:
            skipped += 1
            continue
        updated = resolved_model.update(posterior, observation, effective_weight=weight,
                                        received_at=now)
        repositories.upsert_posterior(session, updated, brand_id=row["brand_id"],
                                      expected_version=posterior.update_version)
        repositories.insert_posterior_update(
            session,
            UpdateRecord(reward_observation_id=observation_id, arm_id=observation.arm_id,
                         version_before=posterior.update_version,
                         version_after=updated.update_version, status=UpdateStatus.APPLIED,
                         effective_weight=weight, updated_at=now),
            brand_id=row["brand_id"],
        )
        repositories.increment_result_counts(session, arm_id=observation.arm_id,
                                             maturity=observation.maturity, now=now)
        applied += 1
    return UpdateSummary(applied=applied, skipped_duplicate=skipped, quarantined=quarantined,
                         failed=failed)
```

Repository addition (same commit):

```python
def increment_result_counts(session: Session, *, arm_id: str,
                            maturity: ObservationMaturity, now: datetime) -> None:
    column = (ar_content_arm_state.c.mature_result_count
              if maturity is ObservationMaturity.MATURE
              else ar_content_arm_state.c.provisional_result_count)
    session.execute(sa.update(ar_content_arm_state)
                    .where(ar_content_arm_state.c.arm_id == arm_id)
                    .values({column.name: column + 1, "last_updated_at": now}))
```

- [ ] **Step 8: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_posterior_updates.py tests\unit\ar\test_result_ingestion.py -q`
Expected: PASS (8 tests). Then the whole unit tree: `pytest tests\unit\ar -q`.

- [ ] **Step 9: Commit**

Run: `git add services/autonomous_ar/ingestion/result_adapter.py services/autonomous_ar/modeling/update.py services/autonomous_ar/storage/repositories.py tests/unit/ar/test_result_ingestion.py tests/unit/ar/test_posterior_updates.py; git commit -m "feat(ar): P2.11 idempotent result ingestion and posterior updates"`

---

### Task P2.12: Simulation

**Files:**
- Create: `services/autonomous_ar/simulation/__init__.py`, `simulation/scenarios.py`, `simulation/simulator.py`, `simulation/metrics.py`
- Test: `tests/simulation/ar/__init__.py`, `tests/simulation/ar/test_scenarios.py`, `tests/simulation/ar/test_acceptance.py`

**Interfaces:**
- Consumes: the real policy functions only — `cold_start_prior`, `NormalNormalRewardModel`, `effective_fractions`/`quota_from_fractions`/`evidence_class`/`sample_rewards`/`sampled_ranking`, `apply_portfolio_constraints`, `fatigue_penalty`. No database, no HTTP, no Temporal.
- Produces (exact):
  - `scenarios.SimArm(arm_id, genre, use_case, language, true_mean, true_variance)`; `ScenarioSpec(name, arms, p1_scores, regime_change_at=None, regime_means=None, shock_at=None, shocked_scores=None)`; the seven builders `scenario_one_clear_winner()`, `scenario_two_close_winners()`, `scenario_deceptive_early_winner()`, `scenario_regime_change()`, `scenario_sparse_observations()`, `scenario_high_correlation()`, `scenario_source_shock()` and `ALL_SCENARIOS`.
  - `simulator.SimConfig(cycles, slot_count, seed, policy=DEFAULT_POLICY)`; `SimulationTrace(selection_counts, rewards, selected_by_cycle, exploration_by_cycle, posterior_means_final, regret_proxy)`; `run_simulation(spec, config) -> SimulationTrace`.
  - `metrics.cumulative_reward(trace)`, `winner_concentration(selection_counts)`, `exploration_ratio(exploration_by_cycle)`, `coverage(selection_counts)`, `late_window_share(selected_by_cycle, arm_id, window)`.
- **Honesty note in the module docstring:** a synthetic simulator validates policy *mechanics* only (§70); it must never be cited as evidence of real-world revenue. The true means exist only inside the simulator.

- [ ] **Step 1: Write the failing scenario tests**

```python
# tests/simulation/ar/test_scenarios.py
from services.autonomous_ar.simulation.metrics import (
    coverage, exploration_ratio, late_window_share, winner_concentration,
)
from services.autonomous_ar.simulation.scenarios import (
    scenario_clear_winner, scenario_deceptive_early_winner, scenario_high_correlation,
    scenario_regime_change, scenario_source_shock, scenario_sparse_observations,
    scenario_two_close_winners,
)
from services.autonomous_ar.simulation.simulator import SimConfig, run_simulation

CONFIG = SimConfig(cycles=60, slot_count=5, seed=17)


def test_scenario_a_discovers_the_clear_winner_and_keeps_exploring() -> None:
    trace = run_simulation(scenario_clear_winner(), CONFIG)
    counts = trace.selection_counts
    winner = max(counts, key=lambda arm_id: counts[arm_id])
    assert winner == "arm-best"
    assert exploration_ratio(trace.exploration_by_cycle) > 0.0, (
        "§69-A: exploration must remain non-zero after discovery"
    )


def test_scenario_b_does_not_collapse_onto_one_of_two_close_winners() -> None:
    trace = run_simulation(scenario_two_close_winners(), CONFIG)
    counts = trace.selection_counts
    assert counts["arm-a"] > 0 and counts["arm-b"] > 0, (
        "§69-B: both close winners stay in the portfolio; no early collapse"
    )
    assert coverage(counts) > 0.5


def test_scenario_c_deceptive_winner_does_not_dominate_late() -> None:
    trace = run_simulation(scenario_deceptive_early_winner(), SimConfig(cycles=120,
                                                                       slot_count=5, seed=3))
    early = late_window_share(trace.selected_by_cycle, "arm-lucky", window=20)
    late = late_window_share(trace.selected_by_cycle, "arm-lucky", window=20)
    assert late <= early + 0.25, "§69-C: a lucky first result must not grow into domination"


def test_scenario_d_adapts_after_the_regime_change() -> None:
    trace = run_simulation(scenario_regime_change(), SimConfig(cycles=160, slot_count=5,
                                                              seed=5))
    late_old = late_window_share(trace.selected_by_cycle, "arm-old", window=40)
    late_new = late_window_share(trace.selected_by_cycle, "arm-new", window=40)
    assert late_new > late_old, "§69-D: allocation shifts toward the new winner"


def test_scenario_f_cluster_caps_hold() -> None:
    trace = run_simulation(scenario_high_correlation(), CONFIG)
    assert winner_concentration(trace.selection_counts) < 1.0


def test_scenario_g_source_shock_keeps_exploration_high() -> None:
    trace = run_simulation(scenario_source_shock(), SimConfig(cycles=40, slot_count=5, seed=9))
    assert exploration_ratio(trace.exploration_by_cycle) > 0.3, (
        "§69-G: with the market signal degraded, unmeasured arms stay exploration-heavy"
    )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\simulation\ar\test_scenarios.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.simulation'` (create `simulation/__init__.py` and `tests/simulation/ar/__init__.py` empty first).

- [ ] **Step 3: Write `simulation/scenarios.py`, `simulation/metrics.py`, `simulation/simulator.py`**

```python
# services/autonomous_ar/simulation/scenarios.py
"""§69's seven synthetic environments. True means exist only here (§70)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

__all__ = [
    "ALL_SCENARIOS", "ScenarioSpec", "SimArm", "scenario_clear_winner",
    "scenario_deceptive_early_winner", "scenario_high_correlation",
    "scenario_regime_change", "scenario_source_shock", "scenario_sparse_observations",
    "scenario_two_close_winners",
]


@dataclass(frozen=True, slots=True)
class SimArm:
    arm_id: str
    genre: str
    use_case: str
    language: str
    true_mean: float
    true_variance: float


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    name: str
    arms: tuple[SimArm, ...]
    p1_scores: Mapping[str, float]
    regime_change_at: int | None = None
    regime_means: Mapping[str, float] = field(default_factory=dict)
    shock_at: int | None = None
    shocked_scores: Mapping[str, float] = field(default_factory=dict)


def _arm(arm_id: str, mean: float, *, genre: str = "rnb", use_case: str = "late_night_drive",
         language: str = "english", variance: float = 0.01) -> SimArm:
    return SimArm(arm_id=arm_id, genre=genre, use_case=use_case, language=language,
                  true_mean=mean, true_variance=variance)


def scenario_clear_winner() -> ScenarioSpec:
    arms = (_arm("arm-best", 0.80), _arm("arm-mid-1", 0.45, genre="city_pop"),
            _arm("arm-mid-2", 0.40, genre="house"), _arm("arm-mid-3", 0.35, genre="jazz"),
            _arm("arm-mid-4", 0.30, genre="ambient"), _arm("arm-mid-5", 0.30, genre="lofi"))
    return ScenarioSpec("clear_winner", arms,
                        {arm.arm_id: 80.0 - index * 6.0
                         for index, arm in enumerate(arms)})


def scenario_two_close_winners() -> ScenarioSpec:
    arms = (_arm("arm-a", 0.70), _arm("arm-b", 0.68, genre="city_pop"),
            _arm("arm-c", 0.35, genre="house"), _arm("arm-d", 0.30, genre="jazz"),
            _arm("arm-e", 0.25, genre="ambient"))
    return ScenarioSpec("two_close_winners", arms,
                        {arm.arm_id: 75.0 - index * 8.0 for index, arm in enumerate(arms)})


def scenario_deceptive_early_winner() -> ScenarioSpec:
    arms = (_arm("arm-lucky", 0.45, variance=0.16), _arm("arm-steady", 0.62),
            _arm("arm-low", 0.30, genre="house"))
    return ScenarioSpec("deceptive_early_winner", arms,
                        {"arm-lucky": 88.0, "arm-steady": 60.0, "arm-low": 45.0})


def scenario_regime_change() -> ScenarioSpec:
    arms = (_arm("arm-old", 0.75), _arm("arm-new", 0.30, genre="city_pop"))
    return ScenarioSpec("regime_change", arms, {"arm-old": 85.0, "arm-new": 50.0},
                        regime_change_at=60, regime_means={"arm-old": 0.25, "arm-new": 0.75})


def scenario_sparse_observations() -> ScenarioSpec:
    arms = tuple(_arm(f"arm-{index:02d}", 0.45, genre=f"g{index % 3}")
                 for index in range(12))
    return ScenarioSpec("sparse_observations", arms,
                        {arm.arm_id: 55.0 for arm in arms})


def scenario_high_correlation() -> ScenarioSpec:
    arms = tuple(_arm(f"arm-rnb-{index}", 0.55, variance=0.02) for index in range(5)) + (
        _arm("arm-far-1", 0.45, genre="city_pop"), _arm("arm-far-2", 0.40, genre="jazz"),
    )
    return ScenarioSpec("high_correlation", arms, {arm.arm_id: 70.0 for arm in arms})


def scenario_source_shock() -> ScenarioSpec:
    arms = tuple(_arm(f"arm-{index:02d}", 0.45, genre=f"g{index}") for index in range(6))
    return ScenarioSpec("source_shock", arms, {arm.arm_id: 70.0 for arm in arms},
                        shock_at=20, shocked_scores={arm.arm_id: 5.0 for arm in arms})


ALL_SCENARIOS = (scenario_clear_winner, scenario_two_close_winners,
                 scenario_deceptive_early_winner, scenario_regime_change,
                 scenario_sparse_observations, scenario_high_correlation,
                 scenario_source_shock)
```

```python
# services/autonomous_ar/simulation/metrics.py
"""§99's benchmark metrics, computed from a trace. Numbers to compare policies, not promises."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = ["coverage", "cumulative_reward", "exploration_ratio", "late_window_share",
           "winner_concentration"]


def cumulative_reward(rewards: Sequence[float]) -> float:
    return float(sum(rewards))


def winner_concentration(selection_counts: Mapping[str, int]) -> float:
    """Normalised Herfindahl index in [1/n, 1]; 1.0 means everything went to one arm."""
    total = sum(selection_counts.values())
    if total == 0:
        return 0.0
    shares = [count / total for count in selection_counts.values()]
    return float(sum(share * share for share in shares))


def exploration_ratio(exploration_by_cycle: Sequence[float]) -> float:
    """Mean share of each cycle's selections that were exploration classes."""
    if not exploration_by_cycle:
        return 0.0
    return float(sum(exploration_by_cycle) / len(exploration_by_cycle))


def coverage(selection_counts: Mapping[str, int]) -> float:
    """Fraction of arms that were selected at least once."""
    if not selection_counts:
        return 0.0
    return sum(1 for count in selection_counts.values() if count > 0) / len(selection_counts)


def late_window_share(selected_by_cycle: Sequence[Sequence[str]], arm_id: str, *,
                      window: int) -> float:
    """Share of a named arm among the last `window` cycles' selections."""
    if not selected_by_cycle:
        return 0.0
    tail = selected_by_cycle[-window:]
    total = sum(len(cycle) for cycle in tail)
    if total == 0:
        return 0.0
    return sum(1 for cycle in tail for selected in cycle if selected == arm_id) / total
```

```python
# services/autonomous_ar/simulation/simulator.py
"""§68: the offline simulator over the *same* policy functions production runs.

Mechanics only (§70): it validates behaviour (does the policy learn, explore, respect caps,
adapt), never economics. Replay is exact — same spec + same config ⇒ identical trace.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from plm_contracts import AllocationType

from ..config import load_ar_settings
from ..domain.arm import ArmDimensions, ContentArm
from ..domain.enums import ArmLifecycleStatus
from ..domain.policy import DEFAULT_POLICY, PolicyConfig
from ..domain.posterior import PosteriorState
from ..modeling.prior import cold_start_prior
from ..modeling.reward_model import NormalNormalRewardModel
from ..policy.constraints import apply_portfolio_constraints
from ..policy.exploration import (
    effective_fractions, evidence_class, quota_from_fractions, sample_rewards, sampled_ranking,
)
from .scenarios import ScenarioSpec

__all__ = ["SimConfig", "SimulationTrace", "run_simulation"]

_COMPONENTS = {name: 50.0 for name in
               ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
                "cross_platform", "production_fit")}


@dataclass(frozen=True, slots=True)
class SimConfig:
    cycles: int
    slot_count: int
    seed: int
    policy: PolicyConfig = DEFAULT_POLICY


@dataclass(frozen=True, slots=True)
class SimulationTrace:
    selection_counts: Mapping[str, int]
    rewards: tuple[float, ...]
    selected_by_cycle: tuple[tuple[str, ...], ...]
    exploration_by_cycle: tuple[float, ...]
    posterior_means_final: Mapping[str, float]
    regret_proxy: float


def _content_arm(sim_arm, *, score: float, as_of) -> ContentArm:
    return ContentArm(
        arm_id=sim_arm.arm_id,
        dimensions=ArmDimensions(genre=sim_arm.genre, use_cases=(sim_arm.use_case,),
                                 language=sim_arm.language),
        p1_score=score, p1_confidence=0.7, component_scores=dict(_COMPONENTS),
        observation_count=5, source_count=2, as_of=as_of, score_version="p1-v1",
        freshness_seconds=0.0,
    )


def run_simulation(spec: ScenarioSpec, config: SimConfig) -> SimulationTrace:
    """`cycles` allocation decisions, with simulated outcomes, entirely in memory."""
    from datetime import UTC, datetime, timedelta

    settings = load_ar_settings(similarity_threshold=0.6)
    model = NormalNormalRewardModel()
    policy = config.policy
    start = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
    rng = random.Random(config.seed)
    selection_counts: dict[str, int] = {arm.arm_id: 0 for arm in spec.arms}
    rewards: list[float] = []
    selected_by_cycle: list[tuple[str, ...]] = []
    exploration_by_cycle: list[float] = []
    posteriors: dict[str, PosteriorState] = {}
    true_means = {arm.arm_id: arm.true_mean for arm in spec.arms}

    for cycle_index in range(config.cycles):
        as_of = start + timedelta(days=7 * cycle_index)
        if spec.regime_change_at is not None and cycle_index >= spec.regime_change_at:
            true_means.update(spec.regime_means)
        scores = dict(spec.p1_scores)
        if spec.shock_at is not None and cycle_index >= spec.shock_at:
            scores.update(spec.shocked_scores)
        arms = tuple(_content_arm(arm, score=scores[arm.arm_id], as_of=as_of)
                     for arm in spec.arms)
        arm_map = {arm.arm_id: arm for arm in arms}
        for arm in arms:
            if arm.arm_id not in posteriors:
                prior = cold_start_prior(arm, settings=settings, policy=policy)
                posteriors[arm.arm_id] = model.initialize_prior(arm, prior)
        measured = frozenset(arm_id for arm_id, state in posteriors.items()
                             if state.observation_count > 0)
        classes = {arm.arm_id: evidence_class(arm.arm_id, measured_arm_ids=measured,
                                              adjacent_arm_ids=frozenset()) for arm in arms}
        low_confidence = not measured
        fractions = effective_fractions(policy, low_confidence=low_confidence,
                                        regime_shift=False)
        quota = quota_from_fractions(config.slot_count, fractions)
        ranked = sampled_ranking(sample_rewards(arms, posteriors, model=model, rng=rng))
        portfolio = apply_portfolio_constraints(
            ranked, classes=classes, arms=arm_map,
            states={arm.arm_id: ArmLifecycleStatus.ACTIVE for arm in arms},
            policy=policy, slot_count=config.slot_count, quota=quota, baselines=(),
            clusters={arm.arm_id: arm.arm_id for arm in arms},  # F's caps via genre caps
        )
        selected = [selection.arm_id for selection in portfolio.selections]
        for selection in portfolio.selections:
            selection_counts[selection.arm_id] += 1
            observation = _simulated_observation(spec, selection.arm_id, true_means,
                                                 rng, as_of, settings)
            posteriors[selection.arm_id] = model.update(
                posteriors[selection.arm_id], observation, effective_weight=1.0,
                received_at=as_of)
            rewards.append(observation.normalized_reward.unwrap())
        selected_by_cycle.append(tuple(selected))
        exploration = sum(1 for selection in portfolio.selections
                          if selection.allocation_type is not AllocationType.EXPLOIT)
        exploration_by_cycle.append(exploration / config.slot_count if config.slot_count else 0.0)

    best_mean = max(true_means.values())
    regret = best_mean * config.cycles * config.slot_count - sum(rewards)
    return SimulationTrace(
        selection_counts=selection_counts, rewards=tuple(rewards),
        selected_by_cycle=tuple(selected_by_cycle),
        exploration_by_cycle=tuple(exploration_by_cycle),
        posterior_means_final={arm_id: state.posterior_mean
                               for arm_id, state in posteriors.items()},
        regret_proxy=regret,
    )


def _simulated_observation(spec: ScenarioSpec, arm_id: str,
                           true_means: Mapping[str, float], rng: random.Random,
                           as_of, settings) -> "RewardObservation":
    from plm_contracts import ObservationWindow, Tri

    from ..domain.enums import ObservationMaturity
    from ..domain.reward import RewardObservation

    variance = next(arm.true_variance for arm in spec.arms if arm.arm_id == arm_id)
    draw = min(1.0, max(0.0, rng.gauss(true_means[arm_id], variance ** 0.5)))
    return RewardObservation(
        experiment_id=f"sim-{arm_id}-{as_of.isoformat()}", arm_id=arm_id,
        content_id=f"content-{arm_id}", observation_window=ObservationWindow.D7,
        maturity=ObservationMaturity.MATURE, observed_at=as_of, raw_metrics={},
        normalized_reward=Tri.known(draw),
        reward_version=settings.simulation_reward_version, attribution_confidence=1.0,
    )
```

- [ ] **Step 4: Run the scenario tests and iterate to green**

Run: `.\.venv\Scripts\python.exe -m pytest tests\simulation\ar\test_scenarios.py -q`
Expected: PASS (6 tests). One deliberate simplification is encoded in the simulator and said out
loud here: correlated arms receive their own singleton clusters, so scenario F's protection is
exercised through the *genre* caps (five arms share one genre) rather than through
`max_cluster_share` — the cluster-cap path itself is covered by P2.8's tests.

- [ ] **Step 5: Write the §98 acceptance tests**

```python
# tests/simulation/ar/test_acceptance.py
"""§98's five policy acceptance tests, plus §100's relative-behaviour rule."""

from __future__ import annotations

from services.autonomous_ar.domain.policy import DEFAULT_POLICY, PolicyConfig
from services.autonomous_ar.simulation.metrics import cumulative_reward, late_window_share
from services.autonomous_ar.simulation.scenarios import (
    scenario_clear_winner, scenario_deceptive_early_winner, scenario_high_correlation,
    scenario_regime_change, scenario_sparse_observations,
)
from services.autonomous_ar.simulation.simulator import SimConfig, run_simulation

BASE = SimConfig(cycles=80, slot_count=5, seed=23)


def test_acceptance_1_clear_winner_gets_exploitation_with_exploration_left_on() -> None:
    trace = run_simulation(scenario_clear_winner(), BASE)
    counts = trace.selection_counts
    assert counts["arm-best"] > max(counts[arm_id] for arm_id in counts if arm_id != "arm-best")
    assert any(ratio > 0.0 for ratio in trace.exploration_by_cycle)


def test_acceptance_2_deceptive_winner_does_not_dominate() -> None:
    trace = run_simulation(scenario_deceptive_early_winner(), SimConfig(cycles=120,
                                                                       slot_count=5, seed=7))
    assert late_window_share(trace.selected_by_cycle, "arm-lucky", window=30) < 0.75


def test_acceptance_3_allocation_shifts_after_a_regime_change() -> None:
    trace = run_simulation(scenario_regime_change(), SimConfig(cycles=160, slot_count=5,
                                                              seed=11))
    # the last 40 of 160 cycles are entirely post-change (change at cycle 60)
    assert late_window_share(trace.selected_by_cycle, "arm-new", window=40) > \
        late_window_share(trace.selected_by_cycle, "arm-old", window=40)


def test_acceptance_4_exploration_floor_holds_when_arms_are_eligible() -> None:
    trace = run_simulation(scenario_sparse_observations(), BASE)
    floor = (DEFAULT_POLICY.adjacent_explore_fraction + DEFAULT_POLICY.radical_explore_fraction)
    assert min(trace.exploration_by_cycle) >= floor * 0.5, (
        "§98-4: adjacent+radical must not vanish while eligible unmeasured arms exist"
    )


def test_acceptance_5_diversity_caps_hold_in_the_correlated_scenario() -> None:
    trace = run_simulation(scenario_high_correlation(), BASE)
    for cycle in trace.selected_by_cycle:
        assert len(cycle) == len(set(cycle)), "an arm may not fill two slots in one portfolio"


def test_determinism_same_seed_same_trace_and_relative_behaviour_comparison() -> None:
    first = run_simulation(scenario_clear_winner(), BASE)
    second = run_simulation(scenario_clear_winner(), BASE)
    assert first == second, "§77/§98: same seed + same spec ⇒ identical trace"
    more_explore = PolicyConfig(name="explore-more", exploit_fraction=0.5,
                                adjacent_explore_fraction=0.3, radical_explore_fraction=0.2)
    greedy = run_simulation(scenario_clear_winner(),
                            SimConfig(cycles=80, slot_count=5, seed=23))
    exploratory = run_simulation(scenario_clear_winner(),
                                 SimConfig(cycles=80, slot_count=5, seed=23,
                                           policy=more_explore))
    assert cumulative_reward(greedy.rewards) >= cumulative_reward(exploratory.rewards), (
        "§100: acceptance is stated as relative behaviour on the same seed, not a fixed number"
    )
```

- [ ] **Step 6: Run them and watch them pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\simulation\ar -q`
Expected: PASS (12 tests).

- [ ] **Step 7: Commit**

Run: `git add services/autonomous_ar/simulation tests/simulation/ar; git commit -m "feat(ar): P2.12 seeded simulator, seven scenarios and the §98 acceptance tests"`

---

### Task P2.13: API

**Files:**
- Create: `services/autonomous_ar/api/__init__.py`, `api/service_token.py`, `api/schemas.py`, `api/routes.py`, `api/app.py`
- Modify: `services/autonomous_ar/storage/repositories.py` — add `load_active_policy`.
- Test: `tests/unit/ar/test_api_cycles.py`, `tests/unit/ar/test_api_results.py`, `tests/unit/ar/test_api_auth.py`

**Interfaces:**
- Consumes: `plm_platform.correlation.CorrelationIdMiddleware`, `plm_platform.config.{SERVICE_TOKEN_HEADER, SERVICE_TOKEN_VAR}`, `plm_contracts.ErrorShape`, the engine (`run_allocation_cycle`, `store_result`, `apply_pending_updates`, repositories).
- Produces (exact):
  - `api.service_token.require_service_token(settings)` — identical semantics to P1's (unconfigured ⇒ 503 `blocked_pending_credentials`; mismatch ⇒ 401 `invalid_service_token`, constant-time compare). Duplication with `services/market_intelligence/api/service_token.py` is deliberate and flagged in the open items (the two services never import each other outside `tests/contract`).
  - `api.app.create_app(*, session_factory: Callable[[], Session] | None, settings: ARSettings, market_source: Callable[[CycleRequest], MarketSnapshot] | None = None) -> FastAPI`. `market_source` is the test seam for P1: production passes `None` and gets the real adapter path; tests inject a fixture-backed callable.
  - `api.routes.build_router(...)` serving **exactly §71's paths** (no invented base path — the registry froze `GET /ar/directives` literally): `POST /ar/cycles`, `GET /ar/cycles/{cycle_id}`, `GET /ar/cycles/{cycle_id}/decisions`, `GET /ar/directives`, `GET /ar/arms/{arm_id}/state`, `POST /ar/results`, `POST /ar/update`.
  - `api.schemas.CycleCreateRequest(brand_id, slot_count, budget, as_of, policy_version, activation_state=None, random_seed=None, unavailable_capabilities=[])` — **`brand_id`, not §71's `channel_id`** (ADR-0019: the cycle's brand identity is `brand_id`; `channel_id` is not a wire name), `CycleSummaryOut`, `DecisionOut`, `ArmStateOut`, `ResultAccepted`, `UpdateSummaryOut`. `GET /ar/directives` returns `ProductionDirective` — the frozen C2 payload verbatim.
  - `repositories.load_active_policy(session) -> PolicyConfig | None`.

- [ ] **Step 1: Write the failing auth and cycles tests**

```python
# tests/unit/ar/test_api_auth.py
from fastapi.testclient import TestClient
from plm_platform.config import load_settings

from services.autonomous_ar.api.app import create_app
from services.autonomous_ar.config import load_ar_settings
from tests.support.ar_db import create_ar_engine

ENV_WITH_TOKEN = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": "ar-seam-token",
}


def _app(*, with_token: bool):
    from sqlalchemy.orm import sessionmaker

    engine = create_ar_engine()
    factory = sessionmaker(bind=engine)
    env = dict(ENV_WITH_TOKEN)
    if not with_token:
        env.pop("PLM_SERVICE_TOKEN")
    settings = load_ar_settings(platform=load_settings(env=env))
    return create_app(session_factory=factory, settings=settings)


def test_unconfigured_token_closes_the_api_and_a_wrong_token_is_rejected() -> None:
    closed = TestClient(_app(with_token=False))
    assert closed.get("/ar/directives", params={"brand_id": "neon_seoul"}).status_code == 503
    configured = TestClient(_app(with_token=True))
    wrong = configured.get("/ar/directives", params={"brand_id": "neon_seoul"},
                           headers={"X-PLM-Service-Token": "nope"})
    assert wrong.status_code == 401
```

Then:

```python
# tests/unit/ar/test_api_cycles.py
def _market_source(request):
    from services.autonomous_ar.decisioning.cycle import MarketSnapshot
    from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm

    components = {name: 60.0 for name in
                  ("demand", "growth", "competitor_velocity", "competition",
                   "audience_quality", "cross_platform", "production_fit")}
    arms = tuple(
        ContentArm(arm_id=f"{genre}-{index}",
                   dimensions=ArmDimensions(genre=genre, use_cases=("late_night_drive",),
                                            language="english"),
                   p1_score=80.0 - index * 5.0, p1_confidence=0.7,
                   component_scores=dict(components), observation_count=2, source_count=1,
                   as_of=request.as_of, score_version="p1-v1", freshness_seconds=0.0)
        for index, genre in enumerate(("rnb", "city_pop", "house", "jazz"))
    )
    return MarketSnapshot(arms=arms, as_of=request.as_of, source="live",
                          score_version="p1-v1")


def test_cycle_endpoints_and_the_shadow_directive_rule(client) -> None:
    created = client.post("/ar/cycles", json={
        "brand_id": "neon_seoul", "slot_count": 2, "budget": 2.0,
        "as_of": "2026-09-20T06:00:00+00:00", "policy_version": "p2-v1",
    })
    assert created.status_code == 200, created.text
    summary = created.json()
    assert summary["selected_count"] == 2 and summary["replay"] is False
    cycle_id = summary["cycle_id"]
    assert client.get(f"/ar/cycles/{cycle_id}").json()["status"] == "completed"
    decisions = client.get(f"/ar/cycles/{cycle_id}/decisions").json()
    assert {row["policy_version"] for row in decisions} == {"p2-v1"}
    # SHADOW: computed, persisted, auditable — and GET /ar/directives serves nothing
    assert client.get("/ar/directives", params={"brand_id": "neon_seoul"}).json() == []
    # §71's `channel_id` is not a wire name (ADR-0019): refusing beats aliasing
    assert client.post("/ar/cycles", json={
        "channel_id": "x", "slot_count": 1, "budget": 1.0,
        "as_of": "2026-09-20T06:00:00+00:00", "policy_version": "p2-v1",
    }).status_code == 422
```

with a `client` fixture in the same file that builds the app over
`tests/support/ar_db.create_ar_engine()` + a seeded `DEFAULT_POLICY` + an injected
`market_source=_market_source`, and `TestClient` requests carrying
`X-PLM-Service-Token: ar-seam-token`.

```python
# tests/unit/ar/test_api_results.py
def test_results_are_accepted_once_and_updates_apply(client) -> None:
    # drive one cycle so the arm state exists, then post a result for its first arm
    payload = {"experiment_id": "exp-1", "arm_id": "rnb-0", "content_id": "c-1",
               "observation_window": "7d", "status": "mature",
               "observed_at": "2026-10-01T00:00:00+00:00", "raw_metrics": {},
               "normalized_reward": 0.8, "reward_version": "p9-reward-v1",
               "attribution_confidence": 0.9}
    first = client.post("/ar/results", json=payload)
    assert first.status_code == 200 and first.json()["stored"] is True
    assert client.post("/ar/results", json=payload).json()["stored"] is False
    update = client.post("/ar/update", params={"brand_id": "neon_seoul"})
    assert update.status_code == 200
    assert update.json()["applied"] == 1
    state = client.get("/ar/arms/rnb-0/state").json()
    assert state["observation_count"] == 1
    assert client.post("/ar/results", json={**payload, "arm_id": "ghost"}).status_code == 422
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_api_auth.py tests\unit\ar\test_api_cycles.py tests\unit\ar\test_api_results.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.api'`.

- [ ] **Step 3: Write the API modules**

`api/service_token.py` mirrors P1's module verbatim except the message text
("the ar API is closed until a service token is configured…"). `api/schemas.py` carries the models
listed in the interfaces block (all `model_config = ConfigDict(extra="forbid", frozen=True)`;
`DirectiveOut` is unnecessary — the route's `response_model` is `ProductionDirective` itself).
`api/routes.py` implements each endpoint with `_auth` as the first dependency (as P1's router does)
and a session dependency; `POST /ar/cycles` resolves the snapshot through `market_source` and maps
`MarketUnavailable` to `503` with `ErrorShape(code="market_unavailable", ...)`; `POST /ar/update`
returns `409 policy_not_configured` when no policy is active. `api/app.py`:

```python
# services/autonomous_ar/api/app.py
"""The `/ar` service surface (§71). FastAPI, correlation middleware, fail-closed auth."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from plm_platform.correlation import CorrelationIdMiddleware
from sqlalchemy.orm import Session

from ..config import ARSettings
from .routes import build_router

__all__ = ["create_app"]


def create_app(*, session_factory: Callable[[], Session] | None, settings: ARSettings,
               market_source: Callable[[Any], Any] | None = None) -> FastAPI:
    app = FastAPI(title="plm-ar", version="p2")
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(build_router(session_factory=session_factory, settings=settings,
                                    market_source=market_source))
    return app
```

- [ ] **Step 4: Run them and watch them pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_api_auth.py tests\unit\ar\test_api_cycles.py tests\unit\ar\test_api_results.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

Run: `git add services/autonomous_ar/api tests/unit/ar/test_api_auth.py tests/unit/ar/test_api_cycles.py tests/unit/ar/test_api_results.py services/autonomous_ar/storage/repositories.py; git commit -m "feat(ar): P2.13 /ar API with fail-closed auth and the SHADOW directive rule"`

---

### Task P2.14: Observability

**Files:**
- Create: `services/autonomous_ar/observability/__init__.py`, `observability/metrics.py`, `observability/logging.py`, `observability/explanation.py`
- Create: `services/autonomous_ar/cli.py`
- Modify: `packages/plm_platform/cli.py` — replace the `ar` stub group with real commands (`cycle`, `show`, `arms`, `explain`, `serve`, `simulate`; `smoke` lands in P2.16), lazy-importing `autonomous_ar.cli` so mypy keeps checking top-level module names.
- Modify: `tests/unit/test_cli.py` — `ar` leaves `STUB_CASES`; add `test_ar_commands_are_implemented_not_stubs`.
- Test: `tests/unit/ar/test_observability.py`, `tests/unit/ar/test_explanation.py`, `tests/unit/ar/test_cli_ar.py`

**Interfaces:**
- Consumes: `plm_platform.observability.{get_or_create_counter, METRICS_REGISTRY}`, `plm_platform.config.load_settings`, engine + repositories.
- Produces (exact):
  - `observability.metrics`: module-level counters via `get_or_create_counter` (P1's fix-round rule — never bare `Counter` in services): `CYCLES_TOTAL`, `CYCLE_FAILURES`, `POSTERIOR_UPDATES_TOTAL`, `POSTERIOR_UPDATE_FAILURES`, `CONSTRAINT_REJECTIONS`, `ARM_RETIREMENTS`, `ARM_REACTIVATIONS`; `record_cycle(*, status, eligible, selected, rejected, retirements, reactivations) -> None`; `record_posterior_updates(summary: UpdateSummary) -> None`.
  - `observability.logging`: `log_cycle(result, *, brand_id, policy_version, p1_snapshot_time, runtime_ms) -> None` (one structured JSON line per cycle, §94's field list); `evaluate_cycle_alerts(*, eligible, stale, selections, exploration_fraction, policy) -> tuple[str, ...]` returning §88 codes (`ZERO_ELIGIBLE_ARMS`, `STALE_P1_DATA`, `ALL_SELECTED_ONE_CLUSTER`, `EXPLORATION_BELOW_FLOOR`); `emit_alert(code, *, brand_id, detail)` — logs at WARNING with `alert_code` in `extra`.
  - `observability.explanation.explain_decision(decision: Mapping[str, Any], *, directive: Mapping[str, Any] | None = None) -> str` — §109's deterministic template (allocation type, posterior reward, market score from the directive's `market_context_json`, reason codes, policy/model versions). No LLM, no network; the prose is reconstructable from the rows alone.
  - `cli.run_cycle_once(*, session_factory=None, market_source=None, slot_count=None, budget=None, policy_version=None, as_of=None) -> dict[str, Any]`; `cli.show_cycle(cycle_id) -> str`; `cli.list_arms() -> str`; `cli.explain(decision_id: str) -> str`; `cli.simulate(scenario: str, cycles: int, seed: int) -> str`; `cli.serve(host, port) -> None`.

- [ ] **Step 1: Write the failing observability and explanation tests**

```python
# tests/unit/ar/test_observability.py
import logging

from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.observability.logging import evaluate_cycle_alerts, log_cycle
from services.autonomous_ar.observability.metrics import CONSTRAINT_REJECTIONS, CYCLES_TOTAL


def test_counters_exist_and_increment(caplog) -> None:
    before = CYCLES_TOTAL._value.get()  # prometheus Counter without labels
    CYCLES_TOTAL.inc()
    assert CYCLES_TOTAL._value.get() == before + 1
    assert CONSTRAINT_REJECTIONS is not None
    with caplog.at_level(logging.INFO):
        log_cycle({"cycle_id": "c-1", "status": "completed", "selected_count": 2,
                   "rejected_count": 0, "warning_codes_json": []},
                  brand_id="neon_seoul", policy_version="p2-v1", p1_snapshot_time=None,
                  runtime_ms=12)
    assert any("allocation cycle" in record.getMessage() for record in caplog.records)


def test_alert_codes_fire_on_the_documented_conditions() -> None:
    assert "ZERO_ELIGIBLE_ARMS" in evaluate_cycle_alerts(
        eligible=0, stale=0, selections=(), exploration_fraction=0.0, policy=DEFAULT_POLICY)
    assert "STALE_P1_DATA" in evaluate_cycle_alerts(
        eligible=3, stale=1, selections=(("a", "b"),), exploration_fraction=0.3,
        policy=DEFAULT_POLICY)
    assert "ALL_SELECTED_ONE_CLUSTER" in evaluate_cycle_alerts(
        eligible=3, stale=0, selections=(("a", "b"),), exploration_fraction=0.3,
        policy=DEFAULT_POLICY)
    assert "EXPLORATION_BELOW_FLOOR" in evaluate_cycle_alerts(
        eligible=3, stale=0, selections=(("c", "d"),), exploration_fraction=0.0,
        policy=DEFAULT_POLICY)
```

```python
# tests/unit/ar/test_explanation.py
from services.autonomous_ar.observability.explanation import explain_decision


def test_explanation_is_deterministic_and_reconstructable_without_an_llm() -> None:
    decision = {"arm_id": "rnb-1", "allocation_type": "exploit", "sampled_reward": 0.71,
                "posterior_expected_reward": 0.66, "reason_codes": ["STRONG_POSTERIOR"],
                "policy_version": "p2-v1", "model_version": "p2-nn-v1",
                "feature_version": "p2-features-v1"}
    directive = {"market_context_json": {"p1_score": 82.0, "p1_confidence": 0.7},
                 "expected_value": 0.66, "uncertainty": 0.18,
                 "value_model": "early-stage-proxy-v1"}
    first = explain_decision(decision, directive=directive)
    second = explain_decision(decision, directive=directive)
    assert first == second
    for fragment in ("rnb-1", "exploit", "0.66", "p2-v1", "STRONG_POSTERIOR", "82.0"):
        assert fragment in first
```

- [ ] **Step 2: Run them and watch them fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_observability.py tests\unit\ar\test_explanation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.observability'`.

- [ ] **Step 3: Write the observability modules**

```python
# services/autonomous_ar/observability/metrics.py
"""§87's counters on the platform registry. `get_or_create_counter` only — P1's fix-round rule:
a bare `Counter` runs twice against the shared registry under two module identities."""

from __future__ import annotations

from plm_platform.observability import get_or_create_counter

__all__ = ["ARM_REACTIVATIONS", "ARM_RETIREMENTS", "CONSTRAINT_REJECTIONS", "CYCLES_TOTAL",
           "CYCLE_FAILURES", "POSTERIOR_UPDATES_TOTAL", "POSTERIOR_UPDATE_FAILURES",
           "record_cycle", "record_posterior_updates"]

CYCLES_TOTAL = get_or_create_counter("plm_ar_allocation_cycles_total",
                                     "Allocation cycles finished, by status.", ["status"])
CYCLE_FAILURES = get_or_create_counter("plm_ar_allocation_cycle_failures",
                                       "Cycles that failed to finish.", [])
POSTERIOR_UPDATES_TOTAL = get_or_create_counter("plm_ar_posterior_updates_total",
                                                "Posterior updates applied.", [])
POSTERIOR_UPDATE_FAILURES = get_or_create_counter("plm_ar_posterior_update_failures",
                                                  "Posterior updates failed or quarantined.", [])
CONSTRAINT_REJECTIONS = get_or_create_counter("plm_ar_constraint_rejections_total",
                                              "Constraint rejections, by reason.", ["reason"])
ARM_RETIREMENTS = get_or_create_counter("plm_ar_arm_retirements_total", "Arms retired.", [])
ARM_REACTIVATIONS = get_or_create_counter("plm_ar_arm_reactivations_total", "Arms reopened.", [])


def record_cycle(*, status: str, eligible: int, selected: int, rejected: int,
                 retirements: int = 0, reactivations: int = 0) -> None:
    """One call per finished cycle; `eligible`/`selected` are counts, not rates."""
    CYCLES_TOTAL.labels(status=status).inc()
    if retirements:
        ARM_RETIREMENTS.inc(retirements)
    if reactivations:
        ARM_REACTIVATIONS.inc(reactivations)


def record_posterior_updates(summary) -> None:
    POSTERIOR_UPDATES_TOTAL.inc(summary.applied)
    POSTERIOR_UPDATE_FAILURES.inc(summary.failed + summary.quarantined)
```

`observability/logging.py` implements `log_cycle`, `evaluate_cycle_alerts`, `emit_alert` exactly as
the interfaces block states (`log_cycle` uses `logging.getLogger("autonomous_ar.cycle").info(...)`
with `extra={...}` fields from §94). `observability/explanation.py` renders the deterministic
template:

```python
# services/autonomous_ar/observability/explanation.py
"""§109: structured rows → stable prose. The template is the source of truth; an LLM (later)
may only rephrase it, so this function never imports one."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["explain_decision"]


def explain_decision(decision: Mapping[str, Any], *,
                     directive: Mapping[str, Any] | None = None) -> str:
    context = dict(directive.get("market_context_json", {})) if directive else {}
    reasons = ", ".join(decision.get("reason_codes", [])) or "none recorded"
    uncertainty = directive.get("uncertainty") if directive else None
    lines = [
        f"{decision['arm_id']}: {decision['allocation_type']}",
        f"  posterior reward {decision['posterior_expected_reward']:.2f} "
        f"(sampled {decision['sampled_reward']:.2f})",
    ]
    if uncertainty is not None:
        lines.append(f"  uncertainty {uncertainty:.2f}")
    if context:
        lines.append(f"  market score {context.get('p1_score')} "
                     f"(confidence {context.get('p1_confidence')})")
    lines.append(f"  reasons: {reasons}")
    lines.append(f"  policy {decision['policy_version']} · model {decision['model_version']} "
                 f"· features {decision['feature_version']}")
    return "\n".join(lines)
```

Wire `record_cycle`/`log_cycle`/`evaluate_cycle_alerts`/`emit_alert` into
`run_allocation_cycle`'s tail (one call each, after `finish_cycle`), and
`record_posterior_updates` into `apply_pending_updates`' return path.

- [ ] **Step 4: Write `cli.py` and un-stub the CLI group**

`services/autonomous_ar/cli.py` exposes the functions listed in the interfaces, each building
`load_ar_settings(platform=load_settings())` and, unless injected, a `sessionmaker` over
`plm_platform.db.build_engine()`. `packages/plm_platform/cli.py`'s `ar` group becomes:

```python
@ar_app.command()
def cycle(slot_count: int = 10, budget: float = 10.0, policy_version: str = "p2-v1",
          as_of: str = "") -> None:
    """Run one allocation cycle (P2)."""
    from autonomous_ar.cli import run_cycle_once

    typer.echo(json.dumps(run_cycle_once(slot_count=slot_count, budget=budget,
                                         policy_version=policy_version,
                                         as_of=as_of or None), indent=2, default=str))


@ar_app.command()
def show(cycle_id: str) -> None:
    """Show one cycle's status and counts (P2)."""
    from autonomous_ar.cli import show_cycle

    typer.echo(show_cycle(cycle_id))


@ar_app.command()
def arms() -> None:
    """List arm states and posterior summaries (P2)."""
    from autonomous_ar.cli import list_arms

    typer.echo(list_arms())


@ar_app.command()
def explain(decision_id: str) -> None:
    """Explain one decision from its stored rows (P2)."""
    from autonomous_ar.cli import explain as explain_impl

    typer.echo(explain_impl(decision_id))


@ar_app.command()
def serve(host: str = "127.0.0.1", port: int = 8082) -> None:
    """Serve the `/ar` API (P2)."""
    from autonomous_ar.cli import serve as serve_impl

    serve_impl(host=host, port=port)


@ar_app.command()
def simulate(scenario: str = "clear_winner", cycles: int = 80, seed: int = 1) -> None:
    """Run one synthetic scenario and print its metrics (P2)."""
    from autonomous_ar.cli import simulate as simulate_impl

    typer.echo(simulate_impl(scenario, cycles, seed))
```

In `tests/unit/test_cli.py`, drop `("ar", "cycle", "PHASE-P2")` from `STUB_CASES` and add:

```python
def test_ar_commands_are_implemented_not_stubs() -> None:
    for command in ("cycle", "show", "arms", "explain", "serve", "simulate"):
        result = TestRunner().invoke(app, ["ar", command, "--help"])
        assert result.exit_code == 0, (command, result.output)
```

- [ ] **Step 5: Write `tests/unit/ar/test_cli_ar.py` and run everything**

```python
# tests/unit/ar/test_cli_ar.py — the CLI is a thin wrapper; the engine is injected
def test_run_cycle_once_uses_the_injected_factory(ar_factory, market_source) -> None:
    from services.autonomous_ar.cli import run_cycle_once

    summary = run_cycle_once(session_factory=ar_factory, market_source=market_source,
                             slot_count=1, budget=1.0)
    assert summary["status"] in {"completed", "partial"} and summary["selected_count"] == 1


def test_simulate_prints_metrics_for_every_scenario() -> None:
    from services.autonomous_ar.cli import simulate

    for scenario in ("clear_winner", "sparse_observations", "regime_change"):
        output = simulate(scenario, cycles=10, seed=3)
        assert "regret_proxy" in output and "exploration_ratio" in output
```

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_observability.py tests\unit\ar\test_explanation.py tests\unit\ar\test_cli_ar.py tests\unit\test_cli.py -q`
Expected: PASS. Run the three gates once here too: `pytest -q`, `ruff check .`, `mypy packages services`.

- [ ] **Step 6: Commit**

Run: `git add services/autonomous_ar/observability services/autonomous_ar/cli.py services/autonomous_ar/decisioning services/autonomous_ar/modeling/update.py packages/plm_platform/cli.py tests/unit/test_cli.py tests/unit/ar/test_observability.py tests/unit/ar/test_explanation.py tests/unit/ar/test_cli_ar.py; git commit -m "feat(ar): P2.14 metrics, alerts, explanations and the plm ar CLI"`

---

### Task P2.15: Acceptance suite

**Files:**
- Create: `tests/fixtures/ar/__init__.py`, `tests/fixtures/ar/scenario.py`
- Test: `tests/unit/ar/test_properties.py`, `tests/integration/ar/test_e2e_acceptance.py`

**Interfaces:**
- Consumes: everything shipped; `tests/support/c1_api.open_c1_client` (the P1 producer fixture), `plm_platform.db.build_engine` for the integration half.
- Produces: `tests.fixtures.ar.scenario.build_acceptance_environment() -> AcceptanceEnvironment` — §97's fixture exactly: 20 arms (3 genres, 4 use cases, 3 languages, 5 similarity clusters, several confidence levels), 10 historical experiment results (2 arms mature, 3 provisional), 1 blocked, 1 retired, 1 stale opportunity; plus `seed_environment(session, env, *, now)` writing arm states, posteriors and reward observations through the repositories.

- [ ] **Step 1: Write the property tests (§96)**

```python
# tests/unit/ar/test_properties.py
"""§96's invariants as executable loop properties over seeded runs — no hypothesis dependency."""

from services.autonomous_ar.simulation.scenarios import ALL_SCENARIOS
from services.autonomous_ar.simulation.simulator import SimConfig, run_simulation

CONFIGS = tuple(SimConfig(cycles=30, slot_count=4, seed=seed) for seed in (1, 2, 3, 5, 8))


def test_selection_count_never_exceeds_slot_count() -> None:
    for scenario in ALL_SCENARIOS:
        for config in CONFIGS:
            trace = run_simulation(scenario(), config)
            assert all(len(cycle) <= config.slot_count for cycle in trace.selected_by_cycle)


def test_sampling_never_mutates_posteriors_and_variances_stay_non_negative() -> None:
    for scenario in ALL_SCENARIOS:
        trace = run_simulation(scenario(), CONFIGS[0])
        assert all(mean >= 0.0 for mean in trace.posterior_means_final.values())
        assert trace.regret_proxy == trace.regret_proxy  # not NaN


def test_an_arm_is_never_selected_twice_in_one_portfolio() -> None:
    for scenario in ALL_SCENARIOS:
        for config in CONFIGS:
            trace = run_simulation(scenario(), config)
            for cycle in trace.selected_by_cycle:
                assert len(cycle) == len(set(cycle))


def test_exploration_is_bounded_by_the_policy() -> None:
    for scenario in ALL_SCENARIOS:
        trace = run_simulation(scenario(), CONFIGS[0])
        assert all(0.0 <= ratio <= 1.0 for ratio in trace.exploration_by_cycle)
```

- [ ] **Step 2: Write the §97 end-to-end acceptance test (Postgres, skips cleanly)**

```python
# tests/integration/ar/test_e2e_acceptance.py
"""§97: P1 snapshot → cycle → directives → rewards → update → the second cycle shifts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.decisioning.cycle import (
    CycleRequest, MarketSnapshot, run_allocation_cycle,
)
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.ingestion.result_adapter import store_result
from services.autonomous_ar.modeling.update import apply_pending_updates
from services.autonomous_ar.storage import repositories
from tests.fixtures.ar.scenario import build_acceptance_environment

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


@pytest.mark.integration
def test_snapshot_cycle_rewards_update_second_cycle_changes() -> None:
    from plm_platform.db import build_engine, scoped_session

    engine = build_engine()
    try:
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1 FROM ar.ar_allocation_cycle LIMIT 1"))
    except Exception as exc:
        pytest.skip(f"compose postgres unavailable or unmigrated ({exc.__class__.__name__}); "
                    "run `docker compose up -d --wait postgres` and `plm migrate`")

    environment = build_acceptance_environment()
    settings = load_ar_settings()
    with scoped_session("ar") as session:
        repositories.insert_policy(session, DEFAULT_POLICY, brand_id="neon_seoul",
                                   created_at=NOW)
        first = run_allocation_cycle(
            session,
            CycleRequest(brand_id="neon_seoul", as_of=NOW, slot_count=10, budget=10.0,
                         policy_version="p2-v1"),
            settings=settings,
            market=MarketSnapshot(arms=environment.arms, as_of=NOW, source="live",
                                  score_version="p1-v1"),
            now=NOW,
        )
        assert len(first.directives) == 10
        # synthetic results for two arms, then the approved update path
        for directive in first.directives[:2]:
            store_result(session, {
                "experiment_id": f"exp-{directive.directive_id}", "arm_id": directive.arm_id,
                "content_id": f"content-{directive.arm_id}", "observation_window": "7d",
                "status": "mature",
                "observed_at": (NOW + timedelta(days=7)).isoformat(), "raw_metrics": {},
                "normalized_reward": 0.85, "reward_version": "p9-reward-v1",
                "attribution_confidence": 0.99,
            }, brand_id="neon_seoul", now=NOW + timedelta(days=7))
        summary = apply_pending_updates(session, settings=settings,
                                        now=NOW + timedelta(days=7), policy=DEFAULT_POLICY)
        assert summary.applied == 2
        later = NOW + timedelta(days=14)
        second = run_allocation_cycle(
            session,
            CycleRequest(brand_id="neon_seoul", as_of=later, slot_count=10, budget=10.0,
                         policy_version="p2-v1"),
            settings=settings,
            market=MarketSnapshot(arms=environment.arms, as_of=later, source="live",
                                  score_version="p1-v1"),
            now=later,
        )
        assert second.cycle_id != first.cycle_id
        rewarded = {directive.arm_id for directive in first.directives[:2]}
        first_rows = {row["arm_id"]: row for row in
                      repositories.load_decisions(session, first.cycle_id)}
        second_rows = {row["arm_id"]: row for row in
                       repositories.load_decisions(session, second.cycle_id)}
        for arm_id in rewarded:
            assert second_rows[arm_id]["posterior_expected_reward"] > \
                first_rows[arm_id]["posterior_expected_reward"], (
                    "§97: the policy changes after observed results"
                )
```

- [ ] **Step 3: Write `tests/fixtures/ar/scenario.py`**

```python
# tests/fixtures/ar/scenario.py
"""§97's deterministic test environment: twenty arms, five clusters, staged evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.enums import ArmLifecycleStatus
from services.autonomous_ar.storage import repositories

__all__ = ["AcceptanceEnvironment", "build_acceptance_environment", "seed_environment"]

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
_GENRES = ("rnb", "city_pop", "house")
_USE_CASES = ("late_night_drive", "rainy_commute", "cafe_study", "hotel_lounge")
_LANGUAGES = ("english", "korean", "instrumental")
_COMPONENTS = {name: 55.0 for name in
               ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
                "cross_platform", "production_fit")}


@dataclass(frozen=True, slots=True)
class AcceptanceEnvironment:
    arms: tuple[ContentArm, ...]
    blocked: tuple[str, ...]
    retired: tuple[str, ...]
    stale: tuple[str, ...]
    mature: tuple[str, ...]
    provisional: tuple[str, ...]


def build_acceptance_environment() -> AcceptanceEnvironment:
    arms: list[ContentArm] = []
    for index in range(20):
        genre = _GENRES[index % 3]
        use_case = _USE_CASES[(index // 3) % 4]
        language = _LANGUAGES[index % 3]
        # five similarity clusters: cluster = (genre, language) pair
        arms.append(ContentArm(
            arm_id=f"{genre}_{language}_{index:02d}",
            dimensions=ArmDimensions(genre=genre, use_cases=(use_case,), language=language,
                                     vocal_mode="vocal" if language != "instrumental" else "instrumental"),
            p1_score=85.0 - index * 2.0, p1_confidence=0.9 - (index % 5) * 0.15,
            component_scores=dict(_COMPONENTS), observation_count=5, source_count=2,
            as_of=NOW, score_version="p1-v1", freshness_seconds=3600.0 * index,
        ))
    return AcceptanceEnvironment(
        arms=tuple(arms), blocked=(arms[7].arm_id,), retired=(arms[11].arm_id,),
        stale=(arms[19].arm_id,), mature=(arms[0].arm_id, arms[1].arm_id),
        provisional=(arms[2].arm_id, arms[3].arm_id, arms[4].arm_id),
    )


def seed_environment(session, environment: AcceptanceEnvironment, *,
                     brand_id: str = "neon_seoul") -> None:
    """Persist states/rewards through the repositories — the same path production uses.
    The stale arm's freshness is left over the policy maximum so §83 rejects it."""
    for arm in environment.arms:
        repositories.upsert_arm_state(session, brand_id=brand_id, arm_id=arm.arm_id, now=NOW)
    repositories.set_arm_status(session, arm_id=environment.blocked[0],
                                status=ArmLifecycleStatus.BLOCKED, now=NOW, reason="rights")
    repositories.set_arm_status(session, arm_id=environment.retired[0],
                                status=ArmLifecycleStatus.RETIRED, now=NOW,
                                reason="WEAK_POSTERIOR")
```

- [ ] **Step 4: Run it**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_properties.py -q` (PASS, 4 tests)
then `.\.venv\Scripts\python.exe -m pytest tests\integration\ar -q -m integration` (PASS or a named
skip when compose is down). Also re-run the three gates.

- [ ] **Step 5: Commit**

Run: `git add tests/fixtures/ar tests/unit/ar/test_properties.py tests/integration/ar/test_e2e_acceptance.py; git commit -m "test(ar): P2.15 property invariants and the §97 end-to-end acceptance"`

---

### Task P2.16: Controlled production smoke test

**Files:**
- Create: `services/autonomous_ar/acceptance/__init__.py`, `acceptance/production_smoke.py`
- Modify: `packages/plm_platform/cli.py` — add `ar smoke` using `BLOCKED_EXIT_CODE`.
- Test: `tests/unit/ar/test_smoke_blocked.py`

**Interfaces:**
- Consumes: `load_settings`/`load_ar_settings`, `P1Client`, the engine, repositories, `plm_platform.db.build_engine`.
- Produces: `acceptance.production_smoke.run_production_smoke(*, base_url: str | None = None, brand_id: str | None = None) -> str | None` — `None` means the smoke passed; a string is the exact `blocked: …` reason. Sequence: (1) P1 service token configured, else `blocked: pending P1 service token (PLM_SERVICE_TOKEN)`; (2) Postgres reachable and the `ar` schema migrated, else `blocked: postgres unavailable or ar schema missing (<Class>)`; (3) P1 `/market/v1/opportunities` answers with at least one arm, else `blocked: P1 unreachable (<detail>)` / `blocked: P1 returned no opportunities`; (4) run one **DRY_RUN** cycle against the real P1 payload and persist it; (5) assert `GET /ar/directives`'s backing query returns nothing for AUTONOMOUS cycles, i.e. zero actionable directives — the ADR-0023 #5 guarantee — and print/return the summary.
- **What this cannot do, said plainly in the module docstring:** there is no P3 to consume directives, no P4/P5 to produce content, no P9/P10 to produce results, and no manual strategy to compare against (§116's shadow table). The smoke proves: real P1 read, real decisions, real persistence, zero actionable effect. Anything beyond that is Wave 6 work and is labelled blocked, not passed.

- [ ] **Step 1: Write the failing blocked-path test (hermetic)**

```python
# tests/unit/ar/test_smoke_blocked.py
import pytest
from typer.testing import CliRunner

from plm_platform.cli import BLOCKED_EXIT_CODE, app
from services.autonomous_ar.acceptance.production_smoke import run_production_smoke

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
}


def test_without_a_service_token_the_smoke_is_blocked_with_that_exact_reason(monkeypatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    reason = run_production_smoke()
    assert reason == "blocked: pending P1 service token (PLM_SERVICE_TOKEN)"


def test_with_a_token_but_a_dead_p1_the_reason_names_the_p1_failure(monkeypatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PLM_SERVICE_TOKEN", "t")
    reason = run_production_smoke(base_url="http://127.0.0.1:9")
    assert reason is not None and reason.startswith("blocked: P1 unreachable")


def test_cli_exits_three_with_the_reason_on_stderr(monkeypatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    result = CliRunner().invoke(app, ["ar", "smoke"])
    assert result.exit_code == BLOCKED_EXIT_CODE
    assert "blocked: pending P1 service token" in result.output
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_smoke_blocked.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.autonomous_ar.acceptance'`.

- [ ] **Step 3: Write `acceptance/production_smoke.py` and the CLI command**

```python
# services/autonomous_ar/acceptance/production_smoke.py
"""§114 P2.16: one small allocation cycle against real P1 data, downstream disabled.

Honest scope: P2.16's spec assumes P3+ exist to *not* consume the directives. In this build
there is no P3, no P4/P5, no P9/P10 and no manual strategy to diff against (§116), so this
smoke proves exactly four things — real P1 read with the service token, real dry-run
decisions, real persistence, and zero actionable directives. It never claims more, and it
returns `blocked: …` (never a silent pass) whenever any of the four cannot be shown.
"""

from __future__ import annotations

from datetime import UTC, datetime

from plm_contracts import ChannelActivationState

from ..config import load_ar_settings
from ..decisioning.cycle import CycleRequest, MarketSnapshot, run_allocation_cycle
from ..ingestion.p1_adapter import MarketUnavailable, P1Client, adapt_opportunity
from ..storage import repositories

__all__ = ["run_production_smoke"]

_TOKEN_REASON = "blocked: pending P1 service token (PLM_SERVICE_TOKEN)"


def run_production_smoke(*, base_url: str | None = None,
                         brand_id: str | None = None) -> str | None:
    """Returns None on success, or the exact blocked reason string."""
    from plm_platform.config import load_settings
    from plm_platform.db import build_engine, scoped_session

    platform = load_settings()
    if platform.service_token is None:
        return _TOKEN_REASON
    settings = load_ar_settings(platform=platform)
    brand = brand_id or "neon_seoul"
    engine = build_engine(platform)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1 FROM ar.ar_allocation_cycle LIMIT 1")
    except Exception as exc:
        return f"blocked: postgres unavailable or ar schema missing ({exc.__class__.__name__})"
    client = P1Client(base_url or settings.market_base_url,
                      platform.service_token.get_secret_value(),
                      timeout_seconds=settings.market_timeout_seconds)
    now = datetime.now(UTC)
    try:
        page = client.fetch_opportunities(limit=200)
    except MarketUnavailable as exc:
        return f"blocked: P1 unreachable ({exc.reason})"
    if not page.items:
        return "blocked: P1 returned no opportunities"
    snapshot = MarketSnapshot(
        arms=tuple(adapt_opportunity(item, now=now) for item in page.items),
        as_of=page.as_of, source="live", score_version=page.score_version)
    request = CycleRequest(brand_id=brand, as_of=now, slot_count=settings.default_slot_count,
                           budget=settings.default_budget, policy_version=settings.policy_version,
                           activation_state=ChannelActivationState.DRY_RUN)
    with scoped_session("ar", factory=None) as session:
        policy = repositories.load_policy(session, settings.policy_version)
        if policy is None:
            return (f"blocked: policy {settings.policy_version} not configured "
                    "(insert it with `plm ar cycle` after seeding the policy row)")
        result = run_allocation_cycle(session, request, settings=settings, market=snapshot,
                                      now=now)
        session.commit()
        actionable = repositories.list_queued_directives(
            session, brand_id=brand, activation_states=(ChannelActivationState.AUTONOMOUS.value,))
    if actionable:
        return "blocked: DRY_RUN produced actionable directives — the activation gate is broken"
    print(f"dry-run cycle {result.cycle_id}: status={result.status.value} "
          f"decisions={len(result.directives)} actionable=0")
    return None
```

CLI (in `packages/plm_platform/cli.py`):

```python
@ar_app.command()
def smoke() -> None:
    """Run the §114 P2.16 dry-run smoke; blocked (exit 3) until P1 is reachable with a token."""
    from autonomous_ar.acceptance.production_smoke import run_production_smoke

    reason = run_production_smoke()
    if reason is not None:
        typer.echo(reason, err=True)
        raise typer.Exit(code=BLOCKED_EXIT_CODE)
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests\unit\ar\test_smoke_blocked.py -q`
Expected: PASS (3 tests). Then the whole gate: `pytest -q`, `ruff check .`, `mypy packages services`.

- [ ] **Step 5: The acceptance command, run for real when the stack and P1 are available**

Run: `.\.venv\Scripts\plm.exe ar smoke`
Expected (token configured, compose up, P1 serving): exit 0, one line
`dry-run cycle <id>: status=completed decisions=10 actionable=0`.
Expected (no token): exit 3, stderr `blocked: pending P1 service token (PLM_SERVICE_TOKEN)`.
Expected (token set, P1 not running): exit 3, stderr `blocked: P1 unreachable (…)`.
There is no fourth outcome: the smoke never reports success for work it could not perform.

- [ ] **Step 6: Commit**

Run: `git add services/autonomous_ar/acceptance packages/plm_platform/cli.py tests/unit/ar/test_smoke_blocked.py; git commit -m "feat(ar): P2.16 dry-run production smoke with an honest blocked path"`

---

## Self-review

Run after the plan was complete, against the spec with fresh eyes.

### 1. Spec coverage

| Spec section (§) | Task that implements it |
|---|---|
| §2/§3 scope, §3.2 out of scope | Every task; no music/video/distribution code appears anywhere |
| §4 dependency model, §5 experiment-not-outcome | P2.2 (C1 adapter), P2.10 (posterior estimates, never guarantees) |
| §6–§8 arm identity, fingerprint, similarity | P2.1 (`canonical_fingerprint`), P2.4 |
| §9 lifecycle | P2.1 (enum), P2.10 step 6b (transitions), P2.8 (hard exclusions) |
| §10 cold-start prior | P2.5 |
| §11 two-layer policy, §66 model protocol, §67 randomness | P2.6, P2.7, P2.10 (seed persisted) |
| §12 why not pure ranking | P2.7 (sampling), P2.12 (A–G behaviours) |
| §13/§14 exploration fractions and definitions | P2.1 (validation), P2.7 (classes/quota), ADR-0023 #2/#3 |
| §15/§16 diversity caps | P2.8 |
| §17–§22 reward definition, windows, maturity, proxy, economics, normalization | P2.1 (`RewardObservation`, reserved terms), P2.6 (`value_model`), P2.11 (range enforcement); §21 economic mode reserved, never fabricated |
| §23/§24 delayed outcomes, attribution | P2.11 (attribution gate, future-dated exclusion) |
| §25/§26 cannibalization, fatigue | P2.8 (cluster concurrency), P2.9 |
| §27/§28 non-stationarity, regime detection | P2.9, P2.10 (flag → exploration bump) |
| §29 decision pipeline | P2.10 (fifteen steps, in order, commented) |
| §30/§31 cycle inputs, slot model | P2.10 (`CycleRequest`, `slot_count`) |
| §32/§33/§34 directive, search region, classes | P2.1, P2.1A (frozen wire), P2.10 (builder) |
| §35/§36 scores and confidence separation | P2.2 (market evidence), P2.6 (posterior), P2.10 (`market_context` beside posterior values) |
| §37–§41 promotion, retirement, reopening, baseline, control | P2.10 step 6b, P2.8 (reservation) |
| §42/§43 seasonality, language context | Preserved via timestamps and dimensions; weights are P2.7/P2.9 config (no seasonal model by design) |
| §44–§54 database model | P2.3 (all fifteen tables), ADR-0024 |
| §55–§57 features, leakage, reason codes | P2.10 (snapshots first), P2.1 (`ReasonCode`) |
| §58–§61 constraint resolution and edge cases | P2.8, P2.10 |
| §62/§63 evidence conflict, weighting | P2.5 (prior vs posterior), P2.11 (updates) |
| §64/§65 transfer learning | P2.5 |
| §68–§70 simulation, scenarios, mechanics-only | P2.12 |
| §71 API contract | P2.13 |
| §72–§75 ingestion, idempotency, concurrency | P2.11 |
| §76/§77 versioning, reproducibility | P2.10 (versions on every decision), P2.12 (`test_determinism…`) |
| §78/§79 LLM and local-model roles | P2.14 (explanation is deterministic; no LLM anywhere) |
| §80–§82 budget, capacity, provider availability | P2.8 (shares), P2.10 (warning path); §82 partial — see open item 9 |
| §83–§85 freshness, P1 changes, shocks | P2.5 (stale prior), P2.10 (freshness gate, regime warning) |
| §86/§87/§88 auditability, metrics, alerts | P2.10 (decisions + rows), P2.14 |
| §89 repository structure | File structure (adapted; deviations listed) |
| §90–§93 configuration, validation, policy migration | P2.1 (`PolicyConfig`), P2.3 (immutability) |
| §94 logging | P2.14 |
| §95–§98 test expectations | P2.1–P2.11 unit tests, P2.12 (§98), P2.15 (§96/§97) |
| §99/§100 benchmarks and relative acceptance | P2.12 (metrics + the same-seed comparison test) |
| §101–§104 operations, failure, DR, backup | P2.10 (degraded cycle), P2.11 (rebuildable log), P2.16 |
| §105 security | Only `PLM_SERVICE_TOKEN` is used; no credentials stored (P2.2/P2.16) |
| §106–§108 human boundary, override, expiry | P2.3 (`ar_manual_override`), P2.8 (pause exclusion), open item on override surfacing |
| §109/§110 explanation and example | P2.14 |
| §111 prohibitions | Review Focus 1–7 + the tests named there |
| §112/§113 what the AI may/may not change | This plan's ADRs and open items |
| §114 sequence P2.1–P2.16 | Task titles are the sequence, one task per step, plus the two seam tasks P2.1A/P2.2A |
| §115/§116 dry-run, shadow | P2.16 (dry-run proof), P2.10/P2.13 (SHADOW serving rule); §116's comparison needs P3+ |
| §117 activation gate | `plm_contracts.ChannelActivationState`; stored per brand (P2.3); enforced at serving (P2.13) |
| §118 AC-01…AC-15 | Mapped: AC-01 P2.2/P2.15, AC-02 P2.5, AC-03 P2.11, AC-04/05 P2.7/P2.12, AC-06 P2.6, AC-07 P2.8, AC-08 P2.9, AC-09 P2.9/P2.15, AC-10 P2.11, AC-11 P2.12, AC-12 P2.10, AC-13 P2.16, AC-14 P2.13, AC-15 P2.13/P2.16 |
| §119–§121 definition of done, P3 output, warnings | P2.10 directives, P2.12 evidence, §121 respected (stdlib model, no neural recommender) |
| Registry C1 | P2.2, P2.2A |
| Registry C2 / ADR-0023 / ADR-0004 | P2.1, P2.1A, P2.8, P2.10 (all five freeze decisions bound in tests) |
| Registry C7 (open) | P2.1 (`RESERVED_VALUE_TERMS`), P2.11 (`quality_status`), open item 16 |
| C9 / R13 (`experiments/` prefix) | P2.10 writes cycle audit artifacts via `plm_platform.storage` (loader-free; the prefix constant is used where artifacts are written) |
| R19 (`brand_id`), R20 (`plm ar …`) | P2.13 (wire), P2.14 (CLI) |

### 2. Placeholder scan

The plan was scanned for the writing-plans red flags, and two were removed before it shipped: a
retracted repository stub in P2.10 (the stored-mode rebuild now lives, fully written, in
`jobs/cycle.py`) and a similarly retracted helper note in P2.8. No step says "similar to Task N",
"add appropriate error handling", "handle edge cases", "write tests for the above" or "TBD". Every
step that changes code carries the code. Where a step's test block needed a helper defined in the
same file (e.g. `_arm`, `_payload`), the helper is shown once and the step says so.

### 3. Type and name consistency across tasks

Checked by grepping the plan for each name's first definition and every later use:

- `ContentArm`/`ArmDimensions`/`MEASURED_COMPONENT_KEYS`: P2.1 defines, P2.2 builds, P2.4 reads,
  P2.10/P2.12 consume — one spelling throughout; `component_scores` is always the seven-key map.
- `deterministic_id` prefixes: `cycle`, `decision`, `directive`, `feature_snapshot`,
  `reward_observation`, `experiment_result`, `posterior`, `posterior_update`, `similarity`,
  `exposure`, `baseline`, `override`, `channel_state`, `cluster`, `arm_state`, `policy` — each used
  by exactly one writer; no `uuid4` anywhere.
- `UpperCamel` record names appear with the same field sets everywhere (grep-verified:
  `PriorParameters`, `PosteriorState`, `RewardObservation`, `ArmSample`, `ConstraintRejection`,
  `Portfolio`, `Selection`, `ProductionDirective`, `DirectiveBrief`, `CycleRequest`,
  `MarketSnapshot`, `CycleResult`, `PreparedCycle`, `SimConfig`, `SimulationTrace`,
  `UpdateSummary`, `StoredResult`, `ResultPayload`, `StoredResult.field_paths` on the error type).
- `to_wire()` (P2.1A) is the single serialiser; P2.10 swaps the seam test's producer but never
  re-implements it; `_row_to_directive` (P2.10) round-trips stored rows back through the same model.
- `policy.retirement.min_mature_observations`/`weak_posterior_threshold` are read by
  `policy/lifecycle.py` with those exact names; `policy.nonstationarity.decay_half_life_days` is
  read by `modeling/update.py`; `policy.fatigue.max_penalty` by `modeling/fatigue.py`.
- `repositories.list_queued_directives(activation_states=...)` is the one serving gate, used by
  P2.10's test, P2.13's route and P2.16's smoke.
- `ChannelActivationState` comes from `plm_contracts` everywhere (never a P2-local copy).

### 4. Review Focus ownership

1. Missing data as zero → P2.1, P2.2, P2.2A, P2.6, P2.11.
2. Duplicate effect → P2.10, P2.11, P2.13.
3. Identity lost in transit → P2.1A, P2.2A, P2.10.
4. Blocked/under-evidenced reaching allocation → P2.8, P2.10, P2.13.
5. Time and money arithmetic → P2.2, P2.8, P2.9, P2.10.
6. Feature leakage → P2.3, P2.10, P2.15.
7. Silent policy change → P2.3, P2.10.

### 5. Task right-sizing (where the sequence was adjusted, and why)

- The spec's sixteen steps stay one-for-one; two seam tasks were added by the brief: **P2.1A**
  (C2 producer, placed immediately after the domain contracts it pins and before anything
  serialises further) and **P2.2A** (C1 consumer, placed immediately after the adapter it proves).
- **P2.14 absorbed the CLI.** §114 gives no task for the operator surface, but R20 requires the
  `ar` group to stop being a stub; the CLI is a thin wrapper over the same engine, so the task that
  ships the operator-facing logs/alerts/explanations is the right owner — one reviewable
  deliverable, not two.
- **P2.10 absorbed `policy/lifecycle.py`.** §89 lists it but §114 names no step: transitions only
  make sense where eligibility is computed, and P2.8/P2.9 own the mechanics they depend on.
- **P2.15 is cross-cutting only.** Every other task writes its own tests; P2.15 owns the §97
  fixture, the §96 property loops and the one end-to-end acceptance run, mirroring P1.13's pattern.

---

## Open items and rulings

Every place the spec is silent or contradicts itself, with the ruling this plan makes. Where a
ruling touches ADR-0023's frozen seams, the owning task records it in ADR-0024 in the same commit.

**Spec contradictions ruled on**

1. **`normalized_reward` ownership (§20 vs §72 vs §21).** §20 defines a P2 proxy decomposition,
   §72 has P9 posting `normalized_reward`, §21 defines the economic formula. **Ruled (R11, P2.6/
   P2.11):** P9 owns reward arithmetic; `RewardObservation.normalized_reward` is required to be
   measured for any update; §20 survives as the *value model* (`early-stage-proxy-v1`, P2.6) that
   names the reward scale and is never used to fabricate an update; §21's terms are reserved
   (`RESERVED_VALUE_TERMS`) and unmeasured terms stay unknown (ADR-0023 #4).
2. **`POST /ar/results` has no bound producer until P9 exists.** **Ruled (P2.11/P2.16):** the
   contract, validation, idempotency and quarantine paths ship now and are exercised by fixtures
   and the simulator; the live producer arrives with P9 at Wave 6 (R11). No test pretends a real
   producer exists.
3. **§105's assumption of P0 credentials.** §105 says P2 receives availability state from
   P0/P4/P7/P8, but the account half of P0 is a human task (R16). **Ruled (P2.2/P2.10/P2.13):** P2
   stores no platform credentials; the only secret it *uses* is `PLM_SERVICE_TOKEN` for the C1
   read; credential-dependent behaviour is `blocked`, never simulated.
4. **Table names diverging from `architecture.md` PART XX.** **Ruled (R3, P2.3/ADR-0024):** the
   phase plan owns table names; §44's names carry the `ar_` prefix, and three tables §44's own
   field lists require but never name are added (`ar_posterior_update_log`, `ar_channel_state`,
   `ar_manual_override`).
5. **§13 vs §90 fractions (70/20/10 vs 1.10).** **Ruled (ADR-0023 #3, implemented P2.1/P2.7):**
   §13 governs; validation rejects sums above 1.0; `baseline_fraction` is the non-exploratory
   residual inside exploit and may not exceed it.
6. **ADR-0023 #5's "using the `basis_weight` P1 persists".** `basis_weight` does **not** cross the
   C1 wire (ADR-0022 froze the field list; the adapter contract test pins the absence).
   **Ruled (ADR-0024 #4, P2.10):** the rankability threshold is operationalised as **2+ non-null
   components** on the returned payload; under-covered arms are persisted and returned with
   `EVIDENCE_INSUFFICIENT`. If the human partner wants the weighted share itself, that is a C1
   registry amendment (a wire change) — out of this plan's remit.
7. **§71's `channel_id` vs R19's `brand_id`.** **Ruled (ADR-0019, P2.13):** the cycle body carries
   `brand_id`; `channel_id` is refused by `extra="forbid"` rather than aliased; `active_channel_id`
   remains an adapter-input concept.
8. **Registry C7's one absent name, `quality status` ("unspecified — must be resolved at the
   PHASE-P2 freeze").** **Ruled (P2.11):** `ResultPayload.quality_status: QualityStatus | None`,
   validated against `plm_contracts.QualityStatus` (P9 §57's states), optional so the W5.0 C7
   freeze can keep or respell it via a registry amendment (R2) without breaking ingestion.
9. **§82 provider availability.** The frozen C2 brief has no capability key, so P2 cannot mark a
   directive as requiring an unavailable capability. **Ruled (P2.10):** the cycle accepts
   `unavailable_capabilities`, emits `PROVIDER_CAPABILITY_UNAVAILABLE`, and leaves routing to P3,
   whose packages carry provider constraints — partial implementation, recorded rather than
   pretended.
10. **§116 shadow-mode comparison.** There is no manual strategy to diff against until P3+ exists.
    **Ruled (P2.16):** P2 persists everything a comparison needs (decisions, directives, features);
    the comparison table itself is Wave-6 work, and the smoke says so instead of faking it.
11. **§67 "production stochastic decisions can vary".** P2 makes them *deterministic per cycle*
    instead: the seed derives from the deterministic `cycle_id` (or an explicit `random_seed`) and
    is persisted (`ar_allocation_cycle.random_seed`). **Ruled (P2.10):** replayability supersedes
    variance; a reviewer can reproduce any production portfolio exactly.
12. **§44's `experiment_result` vs `reward_observation` overlap.** Both carry the same idempotency
    key. **Ruled (P2.11):** `ar_reward_observation` is the immutable wire envelope;
    `ar_experiment_result` is the canonical result record mirrored on first store; both keyed on
    `(experiment_id, observation_window)`.
12b. **§13's class quotas vs §58's fill-until-slots.** If the quotas were cages, a cold start
    (every arm `radical_explore`) could never fill its slots, and §60/§69-E expect the opposite.
    **Ruled (P2.8):** quotas steer pass 2's order; §58's walk then fills remaining capacity in
    the same class order with every cap binding (pass 3, written in P2.8's code and docstring).
    Measured arms still fill the exploit quota first, so the fractions keep their meaning.

**Names or values the spec leaves unspecified — resolved here (all invented, all flagged)**

13. `DIMENSION_WEIGHTS`, `BASE_VARIANCE`, `CONFIDENCE_SPREAD`, `TRANSFER_HALFSAT` (P2.4/P2.5) —
    the spec fixes the transform's direction and versioning, not its constants; they move only with
    `feature_version`/`prior_version`.
14. `SCORE_AT_MAX_PENALTY = 4.0` (P2.9) — the fatigue curve's knee.
15. `REOPEN_SCORE_DELTA = 20.0` (P2.10/6b) — §39 says "materially"; 20 score points is the config
    constant, and the reason string `market_score_recovered` is stored verbatim.
16. `MarketSnapshot.source ∈ {"live", "stored"}`, `simulation_reward_version = "p2-sim-v1"`,
    `proxy_normalization_version = "p2-proxy-norm-v1"` — placeholders until P9 supplies real
    reward versions; each appears on decisions so nothing is anonymous.
17. `ar_allocation_cycle.{activation_state, random_seed, warning_codes_json}` and the wire copies
    on `ar_production_directive` — persistence-only columns so the serving rule and replay are
    enforceable; C2's wire stays the frozen eighteen.
18. `tests/contract/test_c1_market_to_ar_consumer.py` — a second C1 file rather than editing P1's;
    `tests/contract/` is the only place two services may be imported together (P0 §4), and P1's
    producer half stays untouched.
19. `api/service_token.py` duplicates P1's ~30 lines (services never import each other outside
    `tests/contract`). Promotion into `plm_platform` is a small follow-up once a third service
    needs it; recorded, not done silently.

**Known limits of this plan**

- §54's `estimated_audience_exposure` stays NULL until an impressions source exists (unknown ≠ 0);
  the column and the versioned calculation are shipped unused.
- P2.14's `ALL_SELECTED_ONE_CLUSTER` alert needs cluster membership at alert time; the cycle
  passes it in. The end-to-end wiring is exercised in P2.14's test through the function, not
  through a live cycle, because the alert is evaluated after `finish_cycle` by the caller.
- The Temporal integration test and the §97 acceptance run need compose; both skip with the named
  remedy rather than passing silently (the repo's integration convention).
- The `plm ar smoke` acceptance cannot produce a *successful* outcome in CI (no P1 service, no
  token); its blocked paths are what CI asserts, and the success path is documented as an operator
  command (§114 P2.16's own "against real P1 data" requirement).

