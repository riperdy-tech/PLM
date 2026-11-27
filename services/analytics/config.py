"""P9 settings: deployment values come from `plm_platform.config`; these are P9's knobs.

Constructed from code defaults plus explicit overrides; P9 never reads `os.environ`.
The channel facts (:func:`parse_channel_facts`) come from a file the app factory names.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from plm_contracts.money import DEFAULT_REPORTING_CURRENCY
from plm_platform.config import Settings

__all__ = [
    "AnalyticsSettings",
    "ChannelFactsError",
    "NonMonetizedAttestation",
    "load_analytics_settings",
    "parse_channel_facts",
]

_CURRENCY = re.compile(r"^[A-Z]{3}$")


class ChannelFactsError(ValueError):
    """The operator's channel-facts document is malformed."""


@dataclass(frozen=True, slots=True)
class NonMonetizedAttestation:
    """The operator's dated statement that a channel earns no YouTube revenue (not in
    the YouTube Partner Program). It stands in for a revenue statement: realized revenue
    0, labelled ``operator_attested_non_monetized``, only while it is valid."""

    channel_id: str
    attested_by: str
    attested_at: datetime
    valid_until: datetime
    reference: str | None = None

    def is_valid_at(self, instant: datetime) -> bool:
        return self.attested_at <= instant < self.valid_until


@dataclass(frozen=True, slots=True)
class AnalyticsSettings:
    platform: Settings | None = None
    reporting_currency: str = DEFAULT_REPORTING_CURRENCY
    """Every reported amount is converted into this currency (P9 §23; KRW by default)."""
    ar_base_url: str = "http://127.0.0.1:8082"
    """P2's base URL; the reward outbox delivers C7 leg 2 to ``{ar_base_url}/ar/results``."""
    ar_timeout_seconds: float = 10.0
    """Per-request timeout for reward delivery; a read timeout leaves the event UNCERTAIN."""
    youtube_only_channels: frozenset[str] = field(default_factory=frozenset)
    """Channels that publish to YouTube only (no distributor release): only their videos
    may be measured with no ISRC."""
    non_monetized_attestations: tuple[NonMonetizedAttestation, ...] = ()

    def __post_init__(self) -> None:
        if not _CURRENCY.match(self.reporting_currency):
            raise ValueError(f"reporting_currency must be an ISO 4217 code: {self.reporting_currency!r}")
        if self.ar_timeout_seconds <= 0:
            raise ValueError("ar_timeout_seconds must be positive")

    def non_monetized_attestation(self, channel_id: str,
                                  at: datetime) -> NonMonetizedAttestation | None:
        """The channel's attestation valid at ``at``, if any."""
        return next((a for a in self.non_monetized_attestations
                     if a.channel_id == channel_id and a.is_valid_at(at)), None)


def _instant(entry: Mapping[str, Any], key: str, where: str) -> datetime:
    try:
        moment = datetime.fromisoformat(str(entry[key]))
    except (KeyError, ValueError):
        raise ChannelFactsError(f"{where}.{key} must be an ISO 8601 instant") from None
    if moment.tzinfo is None:
        raise ChannelFactsError(f"{where}.{key} must carry a timezone")
    return moment


def parse_channel_facts(raw: Mapping[str, Any]) -> dict[str, object]:
    """Settings overrides from the operator's channel-facts document::

        {"youtube_only_channels": ["UC..."],
         "non_monetized_attestations": [
           {"channel_id": "UC...", "attested_by": "owner",
            "attested_at": "2026-11-27T00:00:00+00:00",
            "valid_until": "2026-12-31T00:00:00+00:00", "reference": "not in YPP"}]}

    Anything malformed raises ``ValueError``; nothing is defaulted.
    """
    unknown = set(raw) - {"youtube_only_channels", "non_monetized_attestations"}
    if unknown:
        raise ChannelFactsError(f"unknown channel facts: {sorted(unknown)}")
    channels = raw.get("youtube_only_channels", [])
    if not isinstance(channels, list) or not all(
            isinstance(c, str) and c.strip() for c in channels):
        raise ChannelFactsError("youtube_only_channels must be a list of channel ids")
    attestations: list[NonMonetizedAttestation] = []
    entries = raw.get("non_monetized_attestations", [])
    if not isinstance(entries, list):
        raise ChannelFactsError("non_monetized_attestations must be a list")
    for index, entry in enumerate(entries):
        where = f"non_monetized_attestations[{index}]"
        if not isinstance(entry, dict):
            raise ChannelFactsError(f"{where} must be an object")
        extra = set(entry) - {"channel_id", "attested_by", "attested_at", "valid_until",
                              "reference"}
        if extra:
            raise ChannelFactsError(f"{where}: unknown fields {sorted(extra)}")
        channel_id, attested_by = entry.get("channel_id"), entry.get("attested_by")
        if not isinstance(channel_id, str) or not channel_id.strip() or not isinstance(
                attested_by, str) or not attested_by.strip():
            raise ChannelFactsError(f"{where}: channel_id and attested_by must be non-empty")
        attested_at = _instant(entry, "attested_at", where)
        valid_until = _instant(entry, "valid_until", where)
        if valid_until <= attested_at:
            raise ChannelFactsError(f"{where}: valid_until must be after attested_at")
        reference = entry.get("reference")
        attestations.append(NonMonetizedAttestation(
            channel_id=channel_id.strip(), attested_by=attested_by.strip(),
            attested_at=attested_at, valid_until=valid_until,
            reference=None if reference is None else str(reference)))
    return {"youtube_only_channels": frozenset(c.strip() for c in channels),
            "non_monetized_attestations": tuple(attestations)}


def load_analytics_settings(
    *, platform: Settings | None = None, **overrides: object
) -> AnalyticsSettings:
    """Build P9 settings; unknown keyword overrides raise (a typo must not pass silently)."""
    unknown = set(overrides) - set(AnalyticsSettings.__dataclass_fields__)
    if unknown:
        raise TypeError(f"unknown analytics settings: {sorted(unknown)}")
    return AnalyticsSettings(platform=platform, **overrides)  # type: ignore[arg-type]
