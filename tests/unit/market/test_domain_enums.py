# tests/unit/market/test_domain_enums.py
from services.market_intelligence.domain.enums import (
    CollectionRunStatus,
    ConnectorErrorClass,
    EntityType,
    ProviderName,
    QueryType,
    TaxonomyDimension,
)


def test_provider_names_are_the_spec_examples() -> None:
    assert {p.value for p in ProviderName} == {
        "youtube_data_api",
        "spotify_web_api",
        "public_trends",
        "browser_public_search",
    }


def test_run_statuses_and_query_types_match_the_spec() -> None:
    assert {s.value for s in CollectionRunStatus} == {
        "queued", "running", "succeeded", "partial", "failed", "cancelled",
    }
    assert {q.value for q in QueryType} == {
        "music_genre", "music_mood", "use_case", "playlist_pattern",
        "artist_adjacent_term", "long_tail", "competitor_discovery",
    }
    assert {e.value for e in EntityType} == {
        "channel", "video", "playlist", "track", "artist", "query", "concept",
    }


def test_connector_error_classes_are_the_spec_eleven() -> None:
    assert {c.value for c in ConnectorErrorClass} == {
        "AUTH_FAILURE", "RATE_LIMIT", "TRANSIENT_NETWORK", "PROVIDER_5XX",
        "INVALID_REQUEST", "NOT_FOUND", "QUOTA_EXCEEDED", "SCHEMA_CHANGE",
        "PARSER_FAILURE", "DATA_QUALITY", "UNSUPPORTED_OPERATION",
    }


def test_taxonomy_has_thirteen_dimensions_per_r14() -> None:
    assert len(TaxonomyDimension) == 13
    assert {d.value for d in TaxonomyDimension} == {
        "genre", "subgenre", "mood", "use_case", "language", "vocal_mode",
        "vocal_gender", "energy", "bpm_range", "duration_bucket",
        "visual_world", "audience_intent", "title_archetype",
    }
