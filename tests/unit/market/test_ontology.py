from services.market_intelligence.domain.enums import TaxonomyDimension
from services.market_intelligence.taxonomy.ontology import (
    DIMENSION_SPECS,
    OTHER_CANDIDATE,
    controlled_values,
    is_controlled_value,
    normalize_dimension_value,
    profile_from_dimensions,
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


def test_the_focus_identity_vocabulary_is_controlled_and_old_values_survive() -> None:
    from services.market_intelligence.taxonomy.ontology import (
        FOCUS_SUBGENRES,
        SUBGENRE_PARENT_GENRE,
    )

    assert is_controlled_value(TaxonomyDimension.GENRE, "focus_electronic")
    assert len(FOCUS_SUBGENRES) == 9
    for subgenre in FOCUS_SUBGENRES:
        assert is_controlled_value(TaxonomyDimension.SUBGENRE, subgenre)
        assert SUBGENRE_PARENT_GENRE[subgenre] == "focus_electronic"
    # historic classifications must still validate (§14.3: values never silently shrink)
    for genre in ("rnb", "lofi", "city_pop", "synthwave"):
        assert is_controlled_value(TaxonomyDimension.GENRE, genre)
    assert is_controlled_value(TaxonomyDimension.SUBGENRE, "alt_rnb")
