# tests/fixtures/ar/scenario.py
"""§97's deterministic test environment: twenty arms, five clusters, staged evidence.

The environment is data only; `seed_environment` persists it through the same
repositories, ingestion and update paths production uses, so an integration run exercises
real writes and a replay derives the same keys instead of minting new ones.

Deviations from the plan's draft fixture, with reasons (P2.15, reported to the controller):

* the draft said "five similarity clusters" but derived genre and language from the same
  `index % 3`, so the twenty arms collapsed to three (genre, language) pairs — §97 asks
  for five. The pairs below are chosen so the *live* clustering (weighted similarity
  ≥ 0.6 over the four dimensions the fixture sets: genre, use case, language, vocal
  mode; two arms merge when they share at least two of those) yields exactly five
  components, each holding four arms. Same-genre and same-language pairs carry disjoint
  use-case sets, which is what keeps the components separate;
* the draft left the stale arm at `3600 × index` seconds (≈ 19 h), far under
  `max_market_data_age_hours` (168 h) — nothing would actually have been stale. The
  stale arm now carries an over-max freshness explicitly;
* the draft's `seed_environment` wrote only arm states, so the §97 list's ten historical
  experiments and the 2-mature/3-provisional split had no data behind them. It now
  stores ten observations through the §72 ingestion path and materialises their
  posteriors through the §74 worker (cold-starting any that do not exist yet, as the
  cycle does).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from plm_contracts import ObservationWindow
from sqlalchemy.orm import Session

from services.autonomous_ar.config import ARSettings, load_ar_settings
from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.enums import ArmLifecycleStatus, ObservationMaturity
from services.autonomous_ar.domain.policy import DEFAULT_POLICY, PolicyConfig
from services.autonomous_ar.ingestion.result_adapter import store_result
from services.autonomous_ar.modeling.prior import cold_start_prior
from services.autonomous_ar.modeling.reward_model import NormalNormalRewardModel
from services.autonomous_ar.modeling.update import apply_pending_updates
from services.autonomous_ar.storage import repositories

__all__ = ["BRAND_ID", "NOW", "AcceptanceEnvironment", "build_acceptance_environment",
           "seed_environment"]

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
"""§97's fixture instant; fixed so every derived id is stable across runs."""

BRAND_ID = "p215_acceptance"
"""The fixture's brand coordinate (ADR-0019), namespaced so the shared dev database cannot
collide with another test's cycles."""

_GENRES = ("rnb", "city_pop", "house")
_USE_CASES = ("late_night_drive", "rainy_commute", "cafe_study", "hotel_lounge")
_LANGUAGES = ("english", "korean", "instrumental")
_PAIRS = (("rnb", "english"), ("rnb", "korean"), ("city_pop", "english"),
          ("city_pop", "instrumental"), ("house", "korean"))
"""Five (genre, language) clusters; see the module docstring for the separation rule."""

_PAIR_USE_CASES: tuple[tuple[int, int, int, int], ...] = (
    (0, 1, 0, 1),   # rnb × english
    (2, 3, 2, 3),   # rnb × korean        (same genre as row 0: disjoint use cases)
    (2, 3, 2, 3),   # city_pop × english  (same language as row 0: disjoint use cases)
    (0, 1, 0, 1),   # city_pop × instrumental (same genre as row 2: disjoint)
    (0, 1, 0, 1),   # house × korean      (same language as row 1: disjoint)
)

_COMPONENTS = {name: 55.0 for name in
               ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
                "cross_platform", "production_fit")}

_STALE_FRESHNESS_SECONDS = 200 * 3600.0
"""Over `PolicyConfig.max_market_data_age_hours` (168 h): §97's one stale opportunity."""

_HISTORY: tuple[tuple[ObservationMaturity, tuple[float, float]], ...] = (
    (ObservationMaturity.MATURE, (0.62, 0.58)),
    (ObservationMaturity.PROVISIONAL, (0.54, 0.51)),
)
"""Two windows per arm: mature arms (§97: exactly two) and provisional arms (exactly three)
receive ten historical experiments between them (2 × 2 + 3 × 2)."""


@dataclass(frozen=True, slots=True)
class AcceptanceEnvironment:
    """The fixture: the arms plus the §97 roles each arm plays."""

    arms: tuple[ContentArm, ...]
    blocked: tuple[str, ...]
    retired: tuple[str, ...]
    stale: tuple[str, ...]
    mature: tuple[str, ...]
    provisional: tuple[str, ...]


def build_acceptance_environment() -> AcceptanceEnvironment:
    """§97's fixture, exactly: twenty arms across five clusters with staged evidence roles."""
    arms: list[ContentArm] = []
    index = 0
    for pair_index, (genre, language) in enumerate(_PAIRS):
        vocal_mode = "vocal" if language != "instrumental" else "instrumental"
        for offset in range(4):
            use_case = _USE_CASES[_PAIR_USE_CASES[pair_index][offset]]
            freshness = (_STALE_FRESHNESS_SECONDS if index == 19 else 3600.0 * index)
            arms.append(ContentArm(
                arm_id=f"p215_{genre}_{language}_{index:02d}",
                dimensions=ArmDimensions(genre=genre, use_cases=(use_case,),
                                         language=language, vocal_mode=vocal_mode),
                p1_score=85.0 - index * 2.0,
                p1_confidence=0.9 - (index % 5) * 0.15,  # multiple confidence levels (§97)
                component_scores=dict(_COMPONENTS), observation_count=5, source_count=2,
                as_of=NOW, score_version="p1-v1", freshness_seconds=freshness,
            ))
            index += 1
    # one blocked, one retired, one stale — each in a different cluster, so the cluster
    # concurrency cap (2) still lets every cluster fill its two slots (§97's ten dirs)
    return AcceptanceEnvironment(
        arms=tuple(arms), blocked=(arms[7].arm_id,), retired=(arms[11].arm_id,),
        stale=(arms[19].arm_id,), mature=(arms[0].arm_id, arms[1].arm_id),
        provisional=(arms[2].arm_id, arms[3].arm_id, arms[4].arm_id),
    )


def seed_environment(session: Session, environment: AcceptanceEnvironment, *,
                     now: datetime, brand_id: str = BRAND_ID,
                     settings: ARSettings | None = None,
                     policy: PolicyConfig | None = None) -> None:
    """Persist states, posteriors and the ten historical experiments — replay-safe.

    Every write is idempotent on its natural key: re-running on the same database leaves
    the row counts unchanged (the P1 `UniqueViolation` lesson, exercised by
    `test_seed_environment_is_idempotent_and_marks_the_excluded_arms`). Posteriors are
    cold-started only when absent — `upsert_posterior` guards its version, so a replayed
    seed must not try to re-initialise a state the updates have since moved (§75).
    """
    resolved_settings = settings if settings is not None else load_ar_settings()
    resolved_policy = policy if policy is not None else DEFAULT_POLICY
    for arm in environment.arms:
        repositories.upsert_arm_state(session, brand_id=brand_id, arm_id=arm.arm_id, now=now)
    repositories.set_arm_status(session, arm_id=environment.blocked[0],
                                status=ArmLifecycleStatus.BLOCKED, now=now, reason="rights")
    repositories.set_arm_status(session, arm_id=environment.retired[0],
                                status=ArmLifecycleStatus.RETIRED, now=now,
                                reason="WEAK_POSTERIOR")
    model = NormalNormalRewardModel(
        observation_variance=resolved_settings.observation_variance,
        variance_floor=resolved_settings.posterior_variance_floor,
    )
    for arm in environment.arms:
        if repositories.load_posterior(session, arm.arm_id) is not None:
            continue  # a replayed seed never re-initialises a moved posterior (§75)
        prior = cold_start_prior(arm, settings=resolved_settings, policy=resolved_policy)
        repositories.upsert_posterior(session, model.initialize_prior(arm, prior),
                                      brand_id=brand_id, expected_version=0)
    for arm_id, maturity, rewards in _history_rows(environment):
        for window, reward in zip((ObservationWindow.D7, ObservationWindow.D28), rewards,
                                  strict=True):
            store_result(session, _payload(arm_id, maturity, window, reward, now=now),
                         brand_id=brand_id, now=now)
    apply_pending_updates(session, settings=resolved_settings, now=now, policy=resolved_policy)


def _history_rows(
    environment: AcceptanceEnvironment,
) -> list[tuple[str, ObservationMaturity, tuple[float, float]]]:
    rows: list[tuple[str, ObservationMaturity, tuple[float, float]]] = []
    for maturity, rewards in _HISTORY:
        arm_ids = (environment.mature if maturity is ObservationMaturity.MATURE
                   else environment.provisional)
        rows.extend((arm_id, maturity, rewards) for arm_id in arm_ids)
    return rows


def _payload(arm_id: str, maturity: ObservationMaturity, window: ObservationWindow,
             reward: float, *, now: datetime) -> dict[str, Any]:
    return {
        "experiment_id": f"p215-history-{arm_id}-{window.value}",
        "arm_id": arm_id, "content_id": f"p215-content-{arm_id}",
        "observation_window": window.value, "status": maturity.value,
        "observed_at": (now - timedelta(days=3)).isoformat(), "raw_metrics": {},
        "normalized_reward": reward, "reward_version": "p9-reward-v1",
        "attribution_confidence": 0.9,
    }
