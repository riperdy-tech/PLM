from services.market_intelligence.collection.seeds import (
    install_bootstrap_seeds,
    load_bootstrap_seeds,
)
from services.market_intelligence.domain.enums import ConnectorType, ProviderName
from services.market_intelligence.storage import repositories

SPECIALISED = {
    "music_genre", "music_mood", "use_case", "playlist_pattern",
    "artist_adjacent_term", "long_tail", "competitor_discovery",
}


def test_the_spec_stage_a_terms_are_all_present() -> None:
    terms = {spec.term for spec in load_bootstrap_seeds()}
    for expected in ("rnb", "pop", "indie", "city pop", "synthwave", "lofi", "ambient",
                     "house", "deep house", "jazz", "acoustic", "sleep music",
                     "study music", "focus music", "night drive", "cafe music",
                     "rainy day", "luxury lounge", "romantic", "melancholic"):
        assert expected in terms, expected


def test_every_seed_carries_a_query_type_and_priority() -> None:
    for spec in load_bootstrap_seeds():
        assert spec.query_type.value in SPECIALISED
        assert 0 <= spec.priority <= 100
        assert spec.locale and spec.language and spec.country_code


def test_install_is_idempotent_and_counts_only_new_queries(market_session) -> None:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    first = install_bootstrap_seeds(market_session, source_id=source_id,
                                    query_version="p1-queries-v1")
    second = install_bootstrap_seeds(market_session, source_id=source_id,
                                     query_version="p1-queries-v1")
    assert first == len(load_bootstrap_seeds())
    assert second == 0
    assert len(repositories.load_queries(market_session, source_id=source_id, active_only=False)) == first
