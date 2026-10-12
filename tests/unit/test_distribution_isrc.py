"""Unit tests for ISRC validation, formatting and stability rules (P8 §7, §8)."""

from __future__ import annotations

from distribution.domain.enums import ISRCSource
from distribution.identifiers.isrc import (
    format_isrc_display,
    preserve_or_allocate_isrc,
    validate_isrc_format,
)


def test_isrc_validation() -> None:
    assert validate_isrc_format("USPLM2600001")
    assert validate_isrc_format("GB-AAA-26-00001")
    assert validate_isrc_format("jpzzz2600010")

    # Invalid formats
    assert not validate_isrc_format("INVALID")
    assert not validate_isrc_format("123456789012")
    assert not validate_isrc_format("USPLM260000")  # 11 chars
    assert not validate_isrc_format("USPLM26000001")  # 13 chars


def test_isrc_formatting() -> None:
    assert format_isrc_display("USPLM2600001") == "US-PLM-26-00001"
    assert format_isrc_display("invalid") == "invalid"


def test_isrc_stability_invariant() -> None:
    # Rule P8 §7, §8: Never silently regenerate an ISRC for an existing recording
    isrc, source = preserve_or_allocate_isrc("USPLM2600042", sequence_number=99)
    assert isrc == "USPLM2600042"
    assert source == ISRCSource.USER_SUPPLIED

    # If None, allocate new deterministic ISRC
    new_isrc, new_source = preserve_or_allocate_isrc(None, sequence_number=5)
    assert new_isrc == "USPLM2600005"
    assert new_source == ISRCSource.INTERNAL_ALLOCATION
