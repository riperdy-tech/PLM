"""Unit tests for raw provider report ingestion and Ruling R10 invariant (P8 §51, Ruling R10)."""

from __future__ import annotations

from datetime import UTC, datetime

from distribution.reports.ingest import get_report_reference, ingest_provider_report

from tests.support.distribution_db import distribution_db_session

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_ingest_provider_report_success_and_deduplication() -> None:
    with distribution_db_session() as session:
        raw_csv = "artist,isrc,streams\nNeon Seoul,USPLM2600001,15420\n"

        ref1 = ingest_provider_report(
            session,
            provider="spotify",
            provider_period="2026-09",
            raw_content=raw_csv,
            source_reference="sftp://reports/spotify_2026_09.csv",
            now=NOW,
        )

        assert ref1.provider == "spotify"
        assert ref1.provider_period == "2026-09"
        assert ref1.report_checksum is not None
        assert ref1.report_id.startswith("spotify_2026-09_")
        assert ref1.received_at == NOW

        # Ingesting same file again returns existing reference (idempotent deduplication)
        ref2 = ingest_provider_report(
            session,
            provider="spotify",
            provider_period="2026-09",
            raw_content=raw_csv,
            source_reference="sftp://reports/spotify_2026_09.csv",
            now=NOW,
        )
        assert ref1.report_id == ref2.report_id


def test_get_report_reference() -> None:
    with distribution_db_session() as session:
        ref = ingest_provider_report(
            session,
            provider="apple_music",
            provider_period="2026-08",
            raw_content="apple_data",
            source_reference="apple_report.csv",
            now=NOW,
        )

        fetched = get_report_reference(session, ref.report_id)
        assert fetched is not None
        assert fetched.report_id == ref.report_id
        assert fetched.provider == "apple_music"


def test_ruling_r10_invariant_no_revenue_calculation() -> None:
    """Ruling R10: P8 stores and versions raw reports and exposes references; P8 must NOT compute revenue."""
    with distribution_db_session() as session:
        ref = ingest_provider_report(
            session,
            provider="distrokid",
            provider_period="2026-07",
            raw_content="isrc,earnings_usd\nUSPLM2600001,45.20\n",
            source_reference="statement.csv",
            now=NOW,
        )

        # Assert no revenue, earnings, or financial fields are computed or exposed in the report model
        model_dict = ref.model_dump()
        forbidden_keys = {"revenue", "earnings", "royalty", "usd", "amount", "total"}
        assert not any(k in model_dict for k in forbidden_keys), (
            "Ruling R10 violation: P8 must not compute or store revenue figures."
        )
