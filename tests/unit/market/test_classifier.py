from services.market_intelligence.domain.enums import TaxonomyDimension as D
from services.market_intelligence.taxonomy.classifier import (
    ClassificationInput,
    RuleBasedClassifier,
    classify_observation,
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
