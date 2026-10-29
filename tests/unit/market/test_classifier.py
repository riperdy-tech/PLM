import pytest

from services.market_intelligence.domain.enums import TaxonomyDimension as D
from services.market_intelligence.taxonomy.classifier import (
    ClassificationInput,
    RuleBasedClassifier,
    classify_observation,
)
from services.market_intelligence.taxonomy.validators import (
    classification_input_fingerprint,
    measured_duration_bucket,
)

EXAMPLE = ClassificationInput(
    title="2AM Seoul — Rainy Night R&B Mix",
    description="Late-night Korean city vibes, melancholic and dreamy.",
)


@pytest.mark.parametrize(
    ("seconds", "bucket"),
    [
        (0.0, "lt_60s"), (59.999, "lt_60s"),
        (60.0, "60_180s"), (179.999, "60_180s"),
        (180.0, "3_10m"), (599.99, "3_10m"),
        (600.0, "10_45m"), (2699.99, "10_45m"),
        (2700.0, "45_75m"), (4500.0, "45_75m"),
        (4500.01, "gt_75m"),
    ],
)
def test_measured_duration_uses_disjoint_versioned_bins(
    seconds: float, bucket: str,
) -> None:
    assert measured_duration_bucket(seconds) == bucket


@pytest.mark.parametrize("invalid", [-1.0, float("nan"), float("inf")])
def test_invalid_video_duration_value_is_never_guessed(invalid: float) -> None:
    with pytest.raises(ValueError, match="duration"):
        measured_duration_bucket(invalid)


@pytest.mark.parametrize("invalid", [True, "3600"])
def test_invalid_video_duration_type_is_never_guessed(invalid: object) -> None:
    with pytest.raises(TypeError, match="duration"):
        measured_duration_bucket(invalid)  # type: ignore[arg-type]


def test_title_duration_is_not_an_identity_fact_without_measurement() -> None:
    classifier = RuleBasedClassifier()
    text = ClassificationInput(
        title="45_75m Night Drive R&B Playlist",
        description="45 75m rain city; extended mix",
    )
    draft = classifier.classify(text)
    assert D.GENRE in draft.dimensions
    assert D.DURATION_BUCKET not in draft.dimensions
    measured = classifier.classify(ClassificationInput(
        title=text.title, description=text.description, video_duration_seconds=125.0,
    ))
    assert measured.dimensions[D.DURATION_BUCKET] == ("60_180s",)
    assert measured.dimensions[D.GENRE] == draft.dimensions[D.GENRE]


def test_duration_bucket_is_part_of_versioned_cache_identity(market_session) -> None:
    classifier = RuleBasedClassifier()
    base = {"title": "45_75m R&B Night Drive", "description": "music"}
    short = ClassificationInput(**base, video_duration_seconds=125.0)
    long = ClassificationInput(**base, video_duration_seconds=3600.0)
    unknown = ClassificationInput(**base)
    assert classification_input_fingerprint(
        short.title, short.description, video_duration_seconds=125.0,
    ) != classification_input_fingerprint(
        long.title, long.description, video_duration_seconds=3600.0,
    )
    assert classification_input_fingerprint(
        short.title, short.description, video_duration_seconds=125.0,
    ) == classification_input_fingerprint(
        short.title, short.description, video_duration_seconds=150.0,
    )
    first_id, created = classify_observation(
        market_session, entity_id=None, observation_id=None, text=short,
        classifier=classifier, taxonomy_version="p1-taxonomy-v2",
    )
    assert created
    again_id, created = classify_observation(
        market_session, entity_id=None, observation_id=None,
        text=ClassificationInput(**base, video_duration_seconds=150.0),
        classifier=classifier, taxonomy_version="p1-taxonomy-v2",
    )
    assert not created and first_id == again_id
    long_id, created = classify_observation(
        market_session, entity_id=None, observation_id=None, text=long,
        classifier=classifier, taxonomy_version="p1-taxonomy-v2",
    )
    assert created and long_id != first_id
    unknown_id, created = classify_observation(
        market_session, entity_id=None, observation_id=None, text=unknown,
        classifier=classifier, taxonomy_version="p1-taxonomy-v2",
    )
    assert created and unknown_id not in {first_id, long_id}


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
