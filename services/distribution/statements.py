"""Distributor statements for a release: the revenue source P9 ingests (R5).

Only a mock exists before R6: :class:`MockDistributorStatements` produces one statement
per calendar month (period ``YYYY-MM``, as distributors report) from the release's
submission until the caller's ``as_of``, with one line per ISRC, from the deterministic
outcome model (:mod:`plm_platform.testing.mock_outcomes`) driven by the release's stored
arm. A month still in progress is reported up to ``as_of``. It declares
``provider_mode = mock``; its provider name is ``mock_distributor``. Parsing a real
distributor's statement files is R6.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from plm_contracts import ProviderMode
from plm_platform.testing.mock_outcomes import MockOutcomeModel, monthly_periods

__all__ = ["MockDistributorStatements", "Statement", "StatementSource"]


class Statement:
    """One statement for one release and period, in P9's ingest shape."""

    def __init__(self, *, provider: str, period: str, period_start: datetime,
                 period_end: datetime, currency: str, rows: list[dict[str, Any]],
                 source_reference: str) -> None:
        self.provider = provider
        self.period = period
        self.period_start = period_start
        self.period_end = period_end
        self.currency = currency
        self.rows = rows
        self.source_reference = source_reference

    @property
    def raw_content(self) -> str:
        """The statement as CSV text (P9 deduplicates statements by its hash)."""
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=["isrc", "release_id", "streams", "revenue"],
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(self.rows)
        return buffer.getvalue()


class StatementSource(Protocol):
    provider_mode: ProviderMode
    provider_name: str

    @property
    def model_version(self) -> str | None: ...

    def statements(self, *, release_id: str, isrcs: Sequence[str], arm_id: str,
                   accrual_start: datetime, as_of: datetime) -> list[Statement]: ...


class MockDistributorStatements:
    """Deterministic, arm-dependent statement lines; labelled mock."""

    provider_mode = ProviderMode.MOCK
    provider_name = "mock_distributor"

    def __init__(self, model: MockOutcomeModel) -> None:
        self.model = model

    @property
    def model_version(self) -> str | None:
        return self.model.model_version

    def statements(self, *, release_id: str, isrcs: Sequence[str], arm_id: str,
                   accrual_start: datetime, as_of: datetime) -> list[Statement]:
        result: list[Statement] = []
        for period, start, end in monthly_periods(accrual_start, as_of):
            lines = self.model.statement_lines(isrcs=isrcs, arm_id=arm_id, period_start=start,
                                               period_end=end, period=period)
            result.append(Statement(
                provider=self.provider_name, period=period, period_start=start,
                period_end=end, currency=self.model.currency,
                rows=[{"isrc": line.isrc, "release_id": release_id, "streams": line.streams,
                       "revenue": str(line.revenue.amount)} for line in lines],
                source_reference=f"{self.provider_name}:{release_id}:{period}:"
                                 f"{end.isoformat()}",
            ))
        return result
