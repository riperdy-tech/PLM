"""ISRC validation, formatting and stability management for Phase P8 (P8 §7, §8).

Follows ISO 3901 specification.
Enforces the core rule: Never silently regenerate an ISRC for an existing recording
during a metadata correction or resubmission.
"""

from __future__ import annotations

import re

from ..domain.enums import ISRCSource
from ..domain.ids import generate_deterministic_isrc

# ISO 3901 standard format: 2 letters country, 3 alphanumeric registrant, 2 digits year, 5 digits sequence
ISRC_REGEX = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{2}[0-9]{5}$")


def clean_isrc(isrc: str) -> str:
    """Normalize ISRC string by removing hyphens/spaces and uppercasing."""
    return isrc.replace("-", "").replace(" ", "").strip().upper()


def validate_isrc_format(isrc: str) -> bool:
    """Validate that the string complies with the 12-character ISO 3901 format."""
    normalized = clean_isrc(isrc)
    return bool(ISRC_REGEX.match(normalized))


def format_isrc_display(isrc: str) -> str:
    """Format 12-char ISRC into canonical hyphenated display format CC-XXX-YY-NNNNN."""
    normalized = clean_isrc(isrc)
    if not validate_isrc_format(normalized):
        return isrc
    return f"{normalized[:2]}-{normalized[2:5]}-{normalized[5:7]}-{normalized[7:]}"


def preserve_or_allocate_isrc(
    existing_isrc: str | None,
    sequence_number: int = 1,
    country: str = "US",
    registrant: str = "PLM",
    year_two_digits: str = "26",
) -> tuple[str, ISRCSource]:
    """Preserve existing valid ISRC or allocate a new deterministic ISRC.

    Guarantees stability: if an existing valid ISRC is provided, it is retained
    with ISRCSource.USER_SUPPLIED rather than newly generated (P8 §7, §8).
    """
    if existing_isrc and validate_isrc_format(existing_isrc):
        return clean_isrc(existing_isrc), ISRCSource.USER_SUPPLIED

    new_isrc = generate_deterministic_isrc(
        country=country,
        registrant=registrant,
        year_two_digits=year_two_digits,
        sequence_number=sequence_number,
    )
    return new_isrc, ISRCSource.INTERNAL_ALLOCATION
