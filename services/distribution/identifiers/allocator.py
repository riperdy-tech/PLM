"""Persisted ISRC allocation: one stable ISRC per recording (P8 §7, §8, ADR-0029).

An ISRC identifies a recording, so it is keyed on the recording, here the P4 candidate id
that every downstream artifact carries. The first allocation for a recording draws the
next sequence number for its ``(country, registrant, year)`` from the database. Every
later request for the same recording returns the same ISRC, so a resubmission or a
metadata correction can never silently regenerate it.

This replaces deriving the ISRC from the track's position, which gave the first track of
every release the same code and made the second release fail on the unique constraint.

The registrant code is development configuration. Issuing real ISRCs needs an assigned
registrant (P0 §10).
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..domain.enums import ISRCSource
from ..domain.ids import generate_deterministic_isrc
from ..storage.models import IsrcAllocationRow

__all__ = ["allocate_isrc"]


def allocate_isrc(
    session: Session,
    recording_key: str,
    *,
    now: datetime,
    country: str = "US",
    registrant: str = "PLM",
) -> tuple[str, ISRCSource]:
    """The recording's ISRC: the stored one if it has one, else the next in sequence."""
    if not recording_key.strip():
        raise ValueError("recording_key is required to allocate an ISRC")
    existing = session.get(IsrcAllocationRow, recording_key)
    if existing is not None:
        return existing.isrc, ISRCSource.INTERNAL_ALLOCATION

    year = f"{now.year % 100:02d}"
    last = session.execute(
        sa.select(sa.func.max(IsrcAllocationRow.sequence)).where(
            IsrcAllocationRow.country == country,
            IsrcAllocationRow.registrant == registrant,
            IsrcAllocationRow.year == year,
        )
    ).scalar_one()
    sequence = (last or 0) + 1
    isrc = generate_deterministic_isrc(
        country=country, registrant=registrant, year_two_digits=year, sequence_number=sequence
    )
    session.add(
        IsrcAllocationRow(
            recording_key=recording_key,
            isrc=isrc,
            country=country,
            registrant=registrant,
            year=year,
            sequence=sequence,
            allocated_at=now,
        )
    )
    session.flush()
    return isrc, ISRCSource.INTERNAL_ALLOCATION
