from services.market_intelligence.storage.models import MARKET_METADATA, MARKET_TABLES

EXPECTED = (
    "market_sources", "market_queries", "market_seed_evidence", "market_collection_runs",
    "market_raw_observations", "market_entities", "market_entity_identities",
    "market_observations", "market_taxonomy_classifications", "market_arms",
    "market_opportunity_scores", "market_change_events", "market_quarantined_records",
    "market_snapshots",
)


def test_every_table_lives_in_the_market_schema() -> None:
    assert MARKET_TABLES == EXPECTED
    assert {t.schema for t in MARKET_METADATA.tables.values()} == {"market"}
    assert set(MARKET_METADATA.tables) == {f"market.{name}" for name in EXPECTED}


def test_spec_required_columns_exist() -> None:
    tables = MARKET_METADATA.tables
    assert {c.name for c in tables["market.market_sources"].columns} >= {
        "id", "provider_name", "connector_type", "base_identifier", "status",
        "configuration_version", "created_at", "updated_at",
    }
    assert {c.name for c in tables["market.market_raw_observations"].columns} >= {
        "id", "collection_run_id", "source_id", "provider_object_type",
        "provider_object_id", "observed_at", "request_context_hash", "payload_json",
        "payload_hash", "schema_version",
    }
    assert {c.name for c in tables["market.market_observations"].columns} >= {
        "id", "entity_id", "source_id", "observed_at", "published_at", "views", "likes",
        "comments", "subscriber_count", "video_duration_seconds", "playlist_item_count",
        "channel_video_count", "text_title", "text_description", "country_signal",
        "language_signal",
    }


def test_no_native_enums_and_no_zero_defaults_on_metrics() -> None:
    for table in MARKET_METADATA.tables.values():
        for column in table.columns:
            assert column.type.__class__.__name__ != "Enum"
    observation = MARKET_METADATA.tables["market.market_observations"]
    for metric in ("views", "likes", "comments", "subscriber_count"):
        assert observation.c[metric].nullable is True
        assert observation.c[metric].default is None
