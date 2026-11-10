# tests/unit/market/test_ci_fixture_connector.py
"""The ``ENV=ci`` fixture-backed connector yields a corpus that forms arms (R5).

A daily run with no injected connector builds the production connector, which under
``ENV=ci`` is the fixture-backed mock. Its corpus used to be two videos of two different
taxonomy profiles, below §16's arm support floor (two videos per profile), so a CI run
formed no arm, scored nothing, and P2 had no opportunity to allocate: the R5 full chain
could not start from a P1 observation.

Proves, with no network and no database: what the production connector returns under
``ENV=ci`` for one query (search, then the videos' hydration), classified by P1's
rule-based classifier, forms at least two arms at the configured support floor. The
integration tier (`tests/integration/test_r5_full_chain.py`) runs `run_daily` whole.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from plm_platform.config import load_settings
from plm_platform.testing.mock_outcomes import load_mock_outcome_model

from services.autonomous_ar.config import ARSettings
from services.autonomous_ar.domain.arm import ArmDimensions
from services.autonomous_ar.modeling.similarity import arm_similarity
from services.autonomous_ar.policy.identity import load_brand_identities
from services.market_intelligence.cli import _production_connector
from services.market_intelligence.config import load_market_settings
from services.market_intelligence.normalization.youtube import _seconds
from services.market_intelligence.taxonomy.arms import generate_candidate_arms
from services.market_intelligence.taxonomy.classifier import (
    ClassificationInput,
    RuleBasedClassifier,
)
from services.market_intelligence.taxonomy.validators import ClassificationSchemaError

pytestmark = pytest.mark.usefixtures("platform_env")


def test_the_ci_corpus_forms_at_least_two_supported_arms(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "ci")
    settings = load_market_settings(platform=load_settings())
    connector = _production_connector(settings, 10_000)

    found = connector.search_videos("focus electronic")
    ids = [obj.provider_object_id for obj in found.objects]
    videos = connector.fetch_videos(ids)
    assert len(videos.objects) == len(ids) >= 2 * settings.min_arm_support

    classifier = RuleBasedClassifier()
    drafts = []
    for video in videos.objects:
        snippet = video.payload["snippet"]
        try:
            drafts.append(classifier.classify(ClassificationInput(
                title=snippet.get("title"), description=snippet.get("description"),
                video_duration_seconds=_seconds(
                    (video.payload.get("contentDetails") or {}).get("duration"),
                ),
            )))
        except ClassificationSchemaError:
            continue
    arms = generate_candidate_arms(
        drafts, min_support=settings.min_arm_support,
        taxonomy_version=settings.taxonomy_version,
    )
    assert len(arms) >= 2, [d.dimensions for d in drafts]
    # P2 blocks an arm outside the brand's identity (ADR-0044) before it allocates, so
    # every arm the CI corpus forms must be one the brand may produce.
    identity = load_brand_identities()["longform_signal"]
    dims = {arm.arm_id: ArmDimensions(
        genre=arm.profile.genre, subgenre=arm.profile.subgenre, moods=arm.profile.mood,
        use_cases=(arm.profile.use_case,) if arm.profile.use_case else (),
        language=arm.profile.language, vocal_mode=arm.profile.vocal_mode,
        duration_bucket=arm.profile.duration_bucket, visual_world=arm.profile.visual_world,
        title_archetype=arm.profile.title_archetype) for arm in arms}
    for arm in arms:
        assert identity.admits(dims[arm.arm_id]), arm.profile
    # R5's synthetic outcomes must name the IDs P1 now actually creates. Taxonomy v3
    # IDs are digests of the profile, so the two fixture profiles are found by profile.
    by_profile = {(arm.profile.subgenre, arm.profile.mood): arm.arm_id for arm in arms}
    strong = by_profile[(None, ("dreamy",))]
    weak = by_profile[("future_garage_focus", ("melancholic",))]
    # P2 caps a cluster of similar arms at one of R5's three slots: the first cycle can
    # allocate both arms only when they are not one cluster.
    assert arm_similarity(dims[strong], dims[weak]) < ARSettings().similarity_threshold
    model = load_mock_outcome_model(
        Path(__file__).resolve().parents[3] / "config/dev/mock_outcomes.json",
    )
    assert model.effect(strong) >= model.effect(weak) * Decimal("1.2")
