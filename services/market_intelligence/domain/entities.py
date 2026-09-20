# services/market_intelligence/domain/entities.py
"""P1 domain records. Plain frozen dataclasses — persistence shapes live in storage/."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from plm_contracts import Tri

from .enums import ChangeEventType, CollectionRunStatus, EntityStatus, EntityType
from .value_objects import EvidenceSummary, ScoreComponents

__all__ = [
    "CollectionRunCounters", "MarketChangeEventDraft", "MarketEntity", "NormalizedObservation",
    "OpportunityScore", "QuarantinedRecord",
]


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    """§7.6 with Tri metrics: `NULL` ⇒ unknown, explicit `0` ⇒ known zero."""

    entity_id: str
    source_id: str
    raw_observation_id: str
    observed_at: datetime
    published_at: datetime | None = None
    views: Tri = field(default_factory=Tri.unknown)
    likes: Tri = field(default_factory=Tri.unknown)
    comments: Tri = field(default_factory=Tri.unknown)
    subscriber_count: Tri = field(default_factory=Tri.unknown)
    video_duration_seconds: Tri = field(default_factory=Tri.unknown)
    playlist_item_count: Tri = field(default_factory=Tri.unknown)
    channel_video_count: Tri = field(default_factory=Tri.unknown)
    text_title: str | None = None
    text_description: str | None = None
    country_signal: str | None = None
    language_signal: str | None = None

    def __post_init__(self) -> None:
        for name in ("views", "likes", "comments", "subscriber_count",
                     "video_duration_seconds", "playlist_item_count", "channel_video_count"):
            value = getattr(self, name)
            if not isinstance(value, Tri):
                raise TypeError(
                    f"{name} must be a Tri; raw numbers hide missing data (Review Focus 1)"
                )


@dataclass(frozen=True, slots=True)
class MarketEntity:
    entity_type: EntityType
    canonical_name: str
    normalized_name: str
    canonical_url: str | None = None
    language: str | None = None
    country_code: str | None = None
    status: EntityStatus = EntityStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class OpportunityScore:
    """§20's object, minus the wire mapping (api/schemas.py owns serialisation).

    The three versions ride on the object itself: they are the versions of the
    computation that produced this number, so persistence cannot disagree with it
    (review fix 2 — they used to arrive as independent `persist_scores` arguments).
    """

    arm_id: str
    market_opportunity_score: float
    confidence: float
    components: ScoreComponents
    evidence: EvidenceSummary
    as_of: datetime
    score_version: str
    feature_version: str
    normalization_version: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.market_opportunity_score <= 100.0:
            raise ValueError(
                f"market_opportunity_score must be within 0–100, got "
                f"{self.market_opportunity_score!r}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within 0–1, got {self.confidence!r}")


@dataclass(frozen=True, slots=True)
class QuarantinedRecord:
    """§24: quarantine instead of silent drop; always carries a reason code.

    The evidence identifiers (`raw_observation_id`, `source_id`) are part of the
    record: a quarantined row that cannot be traced back to what produced it is
    a silent drop with extra columns.
    """

    reason_code: str
    detail: str
    raw_observation_id: str | None = None
    source_id: str | None = None
    collection_run_id: str | None = None
    payload_uri: str | None = None


@dataclass(frozen=True, slots=True)
class MarketChangeEventDraft:
    event_type: ChangeEventType
    arm_id: str | None
    previous_value: float | None
    current_value: float | None
    detail: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CollectionRunCounters:
    """§42/§7.3 counts, attached to one `collection_run_id`."""

    request_count: int = 0
    success_count: int = 0
    error_count: int = 0
    rate_limit_count: int = 0
    raw_observation_count: int = 0
    normalized_observation_count: int = 0
    quarantined_count: int = 0
    status: CollectionRunStatus = CollectionRunStatus.RUNNING
