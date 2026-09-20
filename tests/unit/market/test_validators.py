import pytest
from plm_contracts import ErrorShape

from services.market_intelligence.domain.enums import TaxonomyDimension
from services.market_intelligence.taxonomy.validators import (
    ClassificationSchemaError,
    validate_classification,
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
