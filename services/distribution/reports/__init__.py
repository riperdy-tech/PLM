"""Provider report ingestion for Phase P8 (P8 §51, Ruling R10)."""

from .ingest import get_report_reference, ingest_provider_report

__all__ = ["get_report_reference", "ingest_provider_report"]
