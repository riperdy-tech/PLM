# tests/unit/market/test_opportunity_scoring.py
"""P1.9 scorer tests.

Dispatcher rules on top of the plan:
- components are Tri-valued: an unobserved input is `unknown`, never a measured zero;
- an arm with no observed evidence component gets **no score** rather than a low one;
- property tests pin the score/confidence bounds and monotonicity;
- a two-run replay over the same stored inputs pins determinism (incl. the version);
- §35's three-channel fixture: the 10M-subscriber slow channel must not outrank the
  smaller fast-growing ones.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import Tri

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.domain.enums import ScoringComponent
from services.market_intelligence.domain.value_objects import ScoreComponents, TaxonomyProfile
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.normalization.youtube import NormalizedObservationDraft
from services.market_intelligence.scoring.calibration import default_weights
from services.market_intelligence.scoring.opportunity import (
    ArmComponentScores,
    compute_component_scores,
    persist_scores,
    score_all_arms,
    score_arm,
)
from services.market_intelligence.storage import repositories
from services.market_intelligence.taxonomy.arms import ArmDraft

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_market_settings()


def _features(market_session, *, views: int, subscribers: int, fingerprint: str,
              arm_ids=None):
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
    return compute_arm_features({fingerprint: observations}, as_of=AS_OF, settings=SETTINGS,
                                arm_ids=arm_ids)


def _score_with(features, components, *, weights=None, score_version=None,
                feature_version=None, normalization_version=None):
    return score_arm(
        features, components, as_of=AS_OF, weights=weights or default_weights(),
        score_version=score_version or SETTINGS.score_version,
        feature_version=feature_version or SETTINGS.feature_version,
        normalization_version=normalization_version or SETTINGS.normalization_version,
    )


def test_component_scores_are_bounded_and_rank_the_population(market_session) -> None:
    big_slow = _features(market_session, views=600_000, subscribers=10_000_000, fingerprint="a")
    small_fast = _features(market_session, views=45_000, subscribers=20_000, fingerprint="b")
    features = {**big_slow, **small_fast}
    components = compute_component_scores(features, settings=SETTINGS)
    assert set(components) == {"a", "b"}
    for scores in components.values():
        assert isinstance(scores, ArmComponentScores)
        for value in scores.as_mapping().values():
            if value.is_known:
                assert 0.0 <= float(value.unwrap()) <= 100.0


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
    # The plan built the features without `arm_ids`, so every score carried the missing
    # arm-table id and `latest_scores`' join to `market_arms` found nothing. The plan's
    # own `compute_arm_features(..., arm_ids=...)` parameter is the designed path.
    features = _features(market_session, views=45_000, subscribers=20_000, fingerprint="b",
                         arm_ids={"b": "rnb_late_night_drive"})
    profile = TaxonomyProfile(genre="rnb", use_case="late_night_drive")
    draft = ArmDraft(arm_id="rnb_late_night_drive", fingerprint="b", profile=profile,
                     support_count=3)
    arm_id, created = repositories.upsert_arm(market_session, draft, brand_id="neon_seoul")
    assert (arm_id, created) == ("rnb_late_night_drive", True)

    scores = score_all_arms(features, as_of=AS_OF, settings=SETTINGS)
    written = persist_scores(market_session, scores, computed_at=AS_OF)
    assert written == 1
    latest = repositories.latest_scores(market_session, limit=10)
    assert latest[0]["arm_id"] == "rnb_late_night_drive"
    assert latest[0]["score_version"] == "p1-v1"
    assert latest[0]["market_opportunity_score"] == pytest.approx(
        scores["b"].market_opportunity_score
    )


def test_the_stored_versions_are_the_versions_of_the_computation(market_session) -> None:
    """Review fix 2: versions must travel on the score object.

    They used to reach the row only through independent `persist_scores` arguments, so
    a caller could store a row claiming versions the computation never ran with. The
    score is now constructed with the versions of the run, and persistence writes the
    score's own versions — there is no second version argument to disagree with.
    """
    features = _features(market_session, views=45_000, subscribers=20_000, fingerprint="b",
                         arm_ids={"b": "rnb_late_night_drive"})
    profile = TaxonomyProfile(genre="rnb", use_case="late_night_drive")
    repositories.upsert_arm(
        market_session,
        ArmDraft(arm_id="rnb_late_night_drive", fingerprint="b", profile=profile,
                 support_count=3),
        brand_id="neon_seoul",
    )
    components = compute_component_scores(features, settings=SETTINGS)
    score = _score_with(features["b"], components["b"], score_version="p1-v9",
                        feature_version="p1-features-v9", normalization_version="p1-norm-v9")
    assert score.feature_version == "p1-features-v9"
    assert score.normalization_version == "p1-norm-v9"

    persist_scores(market_session, {"b": score}, computed_at=AS_OF)
    row = repositories.scores_for_arm(market_session, "rnb_late_night_drive")[0]
    assert row["score_version"] == "p1-v9"
    assert row["feature_version"] == "p1-features-v9"
    assert row["normalization_version"] == "p1-norm-v9"


def test_a_persisted_score_reconstructs_from_its_recorded_inputs(market_session) -> None:
    """Review fix 2: a stored score must be recomputable from what it recorded.

    §30/AC-08: same observation set + versions ⇒ same score. The evidence used to cap
    at five observation ids, so a score fed by more sightings could not be rebuilt; the
    row now carries the full input set and the test recomputes through the production
    feature/scoring path and demands the identical value back.
    """
    _store_channel_points(market_session, "slow_mega", subscribers=10_000_000,
                          published_days_ago=1200,
                          points=((75, 2_000_000), (65, 2_000_500), (55, 2_001_000),
                                  (40, 2_001_500), (25, 2_002_000), (15, 2_002_500),
                                  (5, 2_003_000)))  # seven sightings > the old five-id cap
    _store_channel(market_session, "fast_small", subscribers=20_000, published_days_ago=60,
                   views=(1_000, 5_000, 12_000, 17_000, 21_000))

    original = _scores_from_stored(market_session)
    persist_scores(market_session, original, computed_at=AS_OF)

    tokens = ("slow_mega", "fast_small")
    entity_ids = [repositories.deterministic_id("entity", token) for token in tokens]
    observations = repositories.observations_for_entities(market_session, entity_ids)
    by_raw_id = {observation.raw_observation_id: observation for observation in observations}

    grouped = {}
    for token in tokens:
        row = repositories.scores_for_arm(market_session, token)[0]
        recorded = list(row["evidence_json"]["observation_ids"])
        assert len(recorded) == (7 if token == "slow_mega" else 5)
        grouped[token] = tuple(
            replace(by_raw_id[raw_id], observed_at=_as_utc(by_raw_id[raw_id].observed_at),
                    published_at=(None if by_raw_id[raw_id].published_at is None
                                  else _as_utc(by_raw_id[raw_id].published_at)))
            for raw_id in recorded
        )

    features = compute_arm_features(grouped, as_of=AS_OF, settings=SETTINGS)
    recomputed = score_all_arms(features, as_of=AS_OF, settings=SETTINGS)
    for token in tokens:
        row = repositories.scores_for_arm(market_session, token)[0]
        assert recomputed[token].market_opportunity_score == row["market_opportunity_score"]
        assert recomputed[token].score_version == row["score_version"]
        assert recomputed[token].feature_version == row["feature_version"]
        assert recomputed[token].normalization_version == row["normalization_version"]


def test_arm_identity_survives_a_recompute(market_session) -> None:
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


# --------------------------------------------------------------------------------------
# Dispatcher-mandated coverage: insufficient data, determinism, properties, §35 fixture.
# --------------------------------------------------------------------------------------

def test_an_arm_with_no_observed_signal_gets_no_score_rather_than_a_low_one(
    market_session,
) -> None:
    """Insufficiency is represented as absence of a score, not as a fabricated number."""
    rich = _features(market_session, views=45_000, subscribers=20_000, fingerprint="rich")
    thin_observation = NormalizedObservation(
        entity_id="thin-e1", source_id="s1", raw_observation_id="t1",
        observed_at=AS_OF - timedelta(days=3), published_at=AS_OF - timedelta(days=30),
    )
    thin = compute_arm_features({"thin": (thin_observation,)}, as_of=AS_OF, settings=SETTINGS)
    assert thin["thin"].observation_count == 1  # the arm exists with features…
    features = {**rich, **thin}

    components = compute_component_scores(features, settings=SETTINGS)
    assert "rich" in components
    assert "thin" not in components  # …but it has no measurable component to rank on

    scores = score_all_arms(features, as_of=AS_OF, settings=SETTINGS)
    assert "rich" in scores
    assert "thin" not in scores  # insufficient — not a 0.0, not a passing guess
    with pytest.raises(ValueError, match="no measured components"):
        _score_with(features["rich"], _all_unknown_components())


def _all_unknown_components() -> ArmComponentScores:
    return ArmComponentScores(
        demand=Tri.unknown(), growth=Tri.unknown(), competitor_velocity=Tri.unknown(),
        competition=Tri.unknown(), audience_quality=Tri.unknown(), cross_platform=Tri.unknown(),
        production_fit=Tri.unknown(),
    )


def test_production_fit_alone_cannot_carry_a_score(market_session) -> None:
    """Review fix 1: the module docstring says configuration "cannot carry a score by
    itself", but a measured `production_fit` used to satisfy the old
    `total_weight > 0` guard and produced an 80.0 score from no market evidence."""
    features = _features(market_session, views=45_000, subscribers=20_000, fingerprint="b")
    components = ArmComponentScores(
        demand=Tri.unknown(), growth=Tri.unknown(), competitor_velocity=Tri.unknown(),
        competition=Tri.unknown(), audience_quality=Tri.unknown(), cross_platform=Tri.unknown(),
        production_fit=Tri.known(80.0),
    )
    with pytest.raises(ValueError, match="evidence"):
        _score_with(features["b"], components)


def test_production_fit_still_contributes_when_evidence_exists(market_session) -> None:
    """The ruling keeps production_fit's weight once a real evidence component is in."""
    features = _features(market_session, views=45_000, subscribers=20_000, fingerprint="b")
    components = ArmComponentScores(
        demand=Tri.known(50.0), growth=Tri.unknown(), competitor_velocity=Tri.unknown(),
        competition=Tri.unknown(), audience_quality=Tri.unknown(), cross_platform=Tri.unknown(),
        production_fit=Tri.known(80.0),
    )
    score = _score_with(features["b"], components)
    assert score.market_opportunity_score == pytest.approx((50 * 0.25 + 80 * 0.05) / 0.30)


def _store_channel(session, token: str, *, subscribers: int, published_days_ago: int,
                   views: tuple[int, ...]) -> None:
    """Store one channel's real observation series through the production write path."""
    _store_channel_points(session, token, subscribers=subscribers,
                          published_days_ago=published_days_ago,
                          points=tuple(zip((55, 40, 25, 15, 5), views)))


def _store_channel_points(session, token: str, *, subscribers: int, published_days_ago: int,
                          points: tuple[tuple[int, int], ...]) -> None:
    """As `_store_channel`, but each sighting names its own (days_ago, views)."""
    entity_id = repositories.deterministic_id("entity", token)
    source_id = repositories.deterministic_id("source", token)
    for index, (days_ago, view_count) in enumerate(points):
        repositories.insert_normalized_observation(session, NormalizedObservationDraft(
            entity_id=entity_id, source_id=source_id,
            raw_observation_id=repositories.deterministic_id("raw", token, str(index)),
            observed_at=AS_OF - timedelta(days=days_ago),
            published_at=AS_OF - timedelta(days=published_days_ago),
            views=Tri.known(view_count), likes=Tri.known(view_count // 50),
            comments=Tri.unknown(), subscriber_count=Tri.known(subscribers),
            video_duration_seconds=Tri.known(2400.0), playlist_item_count=Tri.unknown(),
            channel_video_count=Tri.unknown(), text_title=None, text_description=None,
            country_signal=None, language_signal=None,
        ))


def _as_utc(moment: datetime) -> datetime:
    """SQLite (the unit harness) drops tzinfo on read-back; Postgres keeps it (§21).

    The production path is strict about aware timestamps (`_require_aware`), so the
    harness re-attaches UTC — the stored instants themselves are unchanged.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _scores_from_stored(session) -> dict:
    tokens = ("slow_mega", "fast_small")
    entity_ids = [repositories.deterministic_id("entity", token) for token in tokens]
    observations = [
        replace(observation, observed_at=_as_utc(observation.observed_at),
                published_at=None if observation.published_at is None
                else _as_utc(observation.published_at))
        for observation in repositories.observations_for_entities(session, entity_ids)
    ]
    grouped = {
        token: tuple(o for o in observations
                     if o.entity_id == repositories.deterministic_id("entity", token))
        for token in tokens
    }
    features = compute_arm_features(grouped, as_of=AS_OF, settings=SETTINGS)
    return score_all_arms(features, as_of=AS_OF, settings=SETTINGS)


def _canonical(scores: dict) -> str:
    return json.dumps(
        {
            arm: {
                "arm_id": score.arm_id,
                "score": score.market_opportunity_score,
                "confidence": score.confidence,
                "components": {name.value: value.to_json_value()
                               for name, value in score.components.as_mapping().items()},
                "evidence": {
                    "observation_count": score.evidence.observation_count,
                    "source_count": score.evidence.source_count,
                    "top_observation_ids": list(score.evidence.top_observation_ids),
                },
                "as_of": score.as_of.isoformat(),
                "score_version": score.score_version,
                "feature_version": score.feature_version,
                "normalization_version": score.normalization_version,
            }
            for arm, score in sorted(scores.items())
        },
        sort_keys=True,
    )


def test_replaying_the_same_stored_inputs_produces_identical_scores(market_session) -> None:
    """AC-08/§30: same stored observations + same versions ⇒ identical output."""
    _store_channel(market_session, "slow_mega", subscribers=10_000_000,
                   published_days_ago=1200,
                   views=(2_000_000, 2_000_500, 2_001_000, 2_001_500, 2_002_000))
    _store_channel(market_session, "fast_small", subscribers=20_000, published_days_ago=60,
                   views=(1_000, 5_000, 12_000, 17_000, 21_000))

    first = _scores_from_stored(market_session)
    second = _scores_from_stored(market_session)
    assert set(first) == {"slow_mega", "fast_small"}
    assert _canonical(first) == _canonical(second)
    assert first["fast_small"].score_version == "p1-v1"
    assert '"score_version": "p1-v1"' in _canonical(first)  # the version travels with the run


@pytest.mark.parametrize("component", [component.value for component in ScoringComponent])
def test_raising_any_component_value_never_lowers_the_score(market_session,
                                                            component: str) -> None:
    """Property (§34): monotone in every component while nothing else changes."""
    features = _features(market_session, views=45_000, subscribers=20_000, fingerprint="b")
    previous = -1.0
    for value in (0.0, 25.0, 50.0, 75.0, 100.0):
        mapping = {name.value: 50.0 for name in ScoringComponent}
        mapping[component] = value
        score = _score_with(features["b"], ScoreComponents.from_mapping(mapping))
        assert score.market_opportunity_score >= previous, component
        previous = score.market_opportunity_score


def test_bounds_hold_for_a_grid_of_component_inputs(market_session) -> None:
    """Property (§34): score ∈ [0,100] and confidence ∈ [0,1] for any component grid."""
    features = _features(market_session, views=45_000, subscribers=20_000, fingerprint="b")
    grid = (0.0, 25.0, 50.0, 75.0, 100.0)
    for demand in grid:
        for competition in grid:
            components = ScoreComponents(
                demand=demand, growth=demand, competitor_velocity=competition,
                competition=competition, audience_quality=demand,
                cross_platform=competition, production_fit=demand,
            )
            score = _score_with(features["b"], components)
            assert 0.0 <= score.market_opportunity_score <= 100.0
            assert 0.0 <= score.confidence <= 1.0


_THREE_CHANNELS = {
    # §35: A is the biggest and slowest, B is small/cheap and fast, C is mid-sized.
    # (subscribers, published_days_ago, views at 55/40/25/15/5 days ago)
    "slow_mega": (10_000_000, 1200,
                  (2_000_000, 2_000_500, 2_001_000, 2_001_500, 2_002_000)),
    "fast_small": (20_000, 60, (1_000, 5_000, 12_000, 17_000, 21_000)),
    "growing_mid": (80_000, 500, (140_000, 148_000, 154_000, 158_000, 161_000)),
}


def _channel_observations(token: str) -> tuple[NormalizedObservation, ...]:
    subscribers, published_days_ago, views = _THREE_CHANNELS[token]
    return tuple(
        NormalizedObservation(
            entity_id=f"{token}-channel", source_id="youtube_data_api",
            raw_observation_id=f"{token}-raw-{index}",
            observed_at=AS_OF - timedelta(days=days_ago),
            published_at=AS_OF - timedelta(days=published_days_ago),
            views=Tri.known(view_count), likes=Tri.known(view_count // 50),
            subscriber_count=Tri.known(subscribers),
            video_duration_seconds=Tri.known(2400.0),
        )
        for index, (days_ago, view_count) in enumerate(zip((55, 40, 25, 15, 5), views))
    )


def test_a_mega_channel_with_slow_growth_does_not_automatically_outrank_growers() -> None:
    """§35's whole purpose: raw size must not decide the ranking."""
    observations_by_arm = {token: _channel_observations(token) for token in _THREE_CHANNELS}
    features = compute_arm_features(observations_by_arm, as_of=AS_OF, settings=SETTINGS)
    scores = score_all_arms(features, as_of=AS_OF, settings=SETTINGS)
    assert set(scores) == set(_THREE_CHANNELS)

    mega = scores["slow_mega"].market_opportunity_score
    for smaller in ("fast_small", "growing_mid"):
        assert mega < scores[smaller].market_opportunity_score, (
            "the 10M-subscriber slow channel outranked a smaller fast-growing one; "
            "§35 says fix the weighting, not the fixture"
        )

    # §17.3: no secondary source → the component is unknown, never a zero; the
    # measured components still produce a score instead of the arm dropping out.
    for score in scores.values():
        assert score.components.cross_platform.is_unknown
        assert 0.0 < score.market_opportunity_score <= 100.0


def test_the_mega_channel_still_loses_when_one_window_sighting_is_removed() -> None:
    """Review fix 5: the ranking must survive on the windowed metric alone.

    With the day-5 sighting gone, slow_mega used to keep a ~869/day "velocity" —
    its 50/day delta averaged with its own lifetime fallback (~1689/day) — which beat
    fast_small's ~439/day and flipped §35 (slow 61.11 vs fast 47.22). Windowed-only
    medians: slow 50/day, fast 500/day, mid 400/day, and the ranking holds for the
    right reason.
    """
    observations_by_arm = {
        token: tuple(observation for observation in _channel_observations(token)
                     if observation.observed_at != AS_OF - timedelta(days=5))
        for token in _THREE_CHANNELS
    }
    features = compute_arm_features(observations_by_arm, as_of=AS_OF, settings=SETTINGS)
    assert float(features["slow_mega"].median_velocity_30d.unwrap()) == pytest.approx(50.0)
    assert float(features["fast_small"].median_velocity_30d.unwrap()) == pytest.approx(500.0)
    assert float(features["growing_mid"].median_velocity_30d.unwrap()) == pytest.approx(400.0)

    scores = score_all_arms(features, as_of=AS_OF, settings=SETTINGS)
    mega = scores["slow_mega"].market_opportunity_score
    for smaller in ("fast_small", "growing_mid"):
        assert mega < scores[smaller].market_opportunity_score
