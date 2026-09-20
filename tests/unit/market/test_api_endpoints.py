# tests/unit/market/test_api_endpoints.py
"""P1.10: the read-only `/market/v1` endpoints against the fixture-backed app."""

from dataclasses import replace
from datetime import UTC, timedelta

from plm_contracts import Tri

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.value_objects import TaxonomyProfile
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.normalization.youtube import NormalizedObservationDraft
from services.market_intelligence.scoring.calibration import default_weights
from services.market_intelligence.scoring.opportunity import (
    ArmComponentScores,
    persist_scores,
    score_arm,
)
from services.market_intelligence.storage import repositories
from services.market_intelligence.taxonomy.arms import ArmDraft
from tests.support.c1_api import ARM_ID, AS_OF, open_c1_client, seed_market

SETTINGS = load_market_settings()
UNDER_COVERED_ARM_ID = "coverage_probe"


def _as_utc(moment):
    """SQLite (the seam harness) drops tzinfo on read-back; production is strict."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _seed_with_an_under_covered_arm(session) -> None:
    """The standard fixture plus one arm whose high score rests on a single component."""
    seed_market(session)
    entity_id = repositories.deterministic_id("entity", UNDER_COVERED_ARM_ID)
    repositories.insert_normalized_observation(session, NormalizedObservationDraft(
        entity_id=entity_id, source_id=repositories.deterministic_id(
            "source", UNDER_COVERED_ARM_ID),
        raw_observation_id=repositories.deterministic_id("raw", UNDER_COVERED_ARM_ID),
        observed_at=AS_OF - timedelta(days=5), published_at=AS_OF - timedelta(days=40),
        views=Tri.known(30_000), likes=Tri.unknown(), comments=Tri.unknown(),
        subscriber_count=Tri.known(4_000), video_duration_seconds=Tri.unknown(),
        playlist_item_count=Tri.unknown(), channel_video_count=Tri.unknown(),
        text_title=None, text_description=None, country_signal=None, language_signal=None,
    ))
    repositories.upsert_arm(
        session,
        ArmDraft(arm_id=UNDER_COVERED_ARM_ID, fingerprint=UNDER_COVERED_ARM_ID,
                 profile=TaxonomyProfile(), support_count=1),
        brand_id="neon_seoul",
    )
    observations = tuple(
        replace(observation, observed_at=_as_utc(observation.observed_at),
                published_at=(None if observation.published_at is None
                              else _as_utc(observation.published_at)))
        for observation in repositories.observations_for_entities(session, [entity_id])
    )
    features = compute_arm_features(
        {UNDER_COVERED_ARM_ID: observations},
        as_of=AS_OF, settings=SETTINGS,
        arm_ids={UNDER_COVERED_ARM_ID: UNDER_COVERED_ARM_ID},
    )
    score = score_arm(
        features[UNDER_COVERED_ARM_ID],
        ArmComponentScores(
            demand=Tri.known(100.0), growth=Tri.unknown(), competitor_velocity=Tri.unknown(),
            competition=Tri.unknown(), audience_quality=Tri.unknown(),
            cross_platform=Tri.unknown(), production_fit=Tri.known(100.0),
        ),
        as_of=AS_OF, weights=default_weights(), score_version=SETTINGS.score_version,
        feature_version=SETTINGS.feature_version,
        normalization_version=SETTINGS.normalization_version,
    )
    persist_scores(session, {UNDER_COVERED_ARM_ID: score}, computed_at=AS_OF)


def test_under_covered_arms_are_returned_after_the_ranked_ones() -> None:
    """Review fix 7: an arm below the coverage floor is still returned, but it is not
    mixed into the ranking — the fully covered arm stays first even though the
    under-covered arm's raw number is higher (100 vs the seeded arm's ~50)."""
    with open_c1_client(seed=_seed_with_an_under_covered_arm) as client:
        body = client.get("/market/v1/opportunities", params={"limit": 10}).json()
        ids = [item["arm_id"] for item in body["items"]]
        assert ids == [ARM_ID, UNDER_COVERED_ARM_ID]
        scores = [item["market_opportunity_score"] for item in body["items"]]
        assert scores[1] > scores[0], "the under-covered arm's number is higher — it still ranks last"


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
