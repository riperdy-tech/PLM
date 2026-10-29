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

    found = connector.search_videos("lofi study")
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
    # R5's synthetic outcomes must name the IDs P1 now actually creates;
    # v1's historical arm IDs are not reused by taxonomy v2.
    strong = "p1_taxonomy_v2_lofi_study_focus_45_75m"
    weak = "p1_taxonomy_v2_rnb_45_75m"
    assert {strong, weak} <= {arm.arm_id for arm in arms}
    model = load_mock_outcome_model(
        Path(__file__).resolve().parents[3] / "config/dev/mock_outcomes.json",
    )
    assert model.effect(strong) >= model.effect(weak) * Decimal("1.2")
