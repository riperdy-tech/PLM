from services.market_intelligence.collection.seeds import (
    install_bootstrap_seeds,
    load_bootstrap_seeds,
)
from services.market_intelligence.domain.enums import ConnectorType, ProviderName, QueryType
from services.market_intelligence.storage import repositories

SPECIALISED = {
    "music_genre", "music_mood", "use_case", "playlist_pattern",
    "artist_adjacent_term", "long_tail", "competitor_discovery",
}


def test_the_pack_seeds_the_focus_identity_and_drops_the_old_concept() -> None:
    # ADR-0044 replaced §8's original genre/mood starter terms with the channel's
    # instrumental deep-focus electronic identity.
    seeds = {spec.term: spec for spec in load_bootstrap_seeds()}
    for expected in ("deep focus music", "coding music", "programming music",
                     "music for deep work", "future garage mix", "chillstep focus",
                     "downtempo electronic", "ambient electronic study",
                     "minimal electronic focus", "synthwave focus", "focus music for work",
                     "concentration music electronic"):
        assert expected in seeds, expected
    for core in ("deep focus music", "coding music", "programming music",
                 "music for deep work"):
        assert seeds[core].priority >= 80, core  # §9's high-priority class
    for retired in ("rnb", "pop", "city pop", "lofi", "sleep music", "night drive",
                    "cafe music", "luxury lounge", "romantic"):
        assert retired not in seeds, retired
    types = {spec.query_type for spec in seeds.values()}
    assert {QueryType.COMPETITOR_DISCOVERY, QueryType.LONG_TAIL} <= types
    assert all(spec.language == "english" and spec.locale == "en" for spec in seeds.values())


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


def test_install_retires_bootstrap_seeds_the_pack_no_longer_lists(market_session) -> None:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    common = {"source_id": source_id, "locale": "en", "country_code": "US",
              "language": "english", "priority": 80, "query_version": "p1-queries-v1"}
    # an old-concept bootstrap seed, a Stage B seed, and a pack seed a past pack retired
    repositories.upsert_query(market_session, query_text="lofi",
                              query_type=QueryType.MUSIC_GENRE, seed_group="bootstrap", **common)
    repositories.upsert_query(market_session, query_text="rainy lofi",
                              query_type=QueryType.LONG_TAIL, seed_group="expanded", **common)
    repositories.upsert_query(market_session, query_text="coding music",
                              query_type=QueryType.USE_CASE, seed_group="bootstrap", **common)
    assert repositories.sync_seed_group_active(
        market_session, source_id=source_id, seed_group="bootstrap",
        keep_fingerprints=frozenset(),
    ) == 2
    install_bootstrap_seeds(market_session, source_id=source_id, query_version="p1-queries-v1")
    active = {row["query_text"] for row in repositories.load_queries(
        market_session, source_id=source_id)}
    every = {row["query_text"]: row["active"] for row in repositories.load_queries(
        market_session, source_id=source_id, active_only=False)}
    assert every["lofi"] is False  # retired, not deleted
    assert "rainy lofi" in active  # another seed group is not the pack's to retire
    assert "coding music" in active  # back in the pack, so active again
    assert active == {spec.term for spec in load_bootstrap_seeds()} | {"rainy lofi"}
