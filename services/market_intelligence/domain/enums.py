# services/market_intelligence/domain/enums.py
"""P1 vocabulary. Every value is copied from PHASE-P1; storage keeps text only (L4)."""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ArmStatus", "ChangeEventType", "CollectionRunStatus", "ConnectorErrorClass",
    "ConnectorType", "EntityStatus", "EntityType", "EvidenceClass", "FreshnessBucket",
    "ProviderName", "ProviderObjectType", "QueryType", "ScoringComponent",
    "SourceStatus", "TaxonomyDimension",
]


class ProviderName(StrEnum):
    """§7.1's four configured providers."""

    YOUTUBE_DATA_API = "youtube_data_api"
    SPOTIFY_WEB_API = "spotify_web_api"
    PUBLIC_TRENDS = "public_trends"
    BROWSER_PUBLIC_SEARCH = "browser_public_search"


class ConnectorType(StrEnum):
    DATA_API = "data_api"
    WEB_API = "web_api"
    PUBLIC_FEED = "public_feed"
    BROWSER = "browser"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class QueryType(StrEnum):
    """§7.2. Query meaning lives in these tokens, never only in free text."""

    MUSIC_GENRE = "music_genre"
    MUSIC_MOOD = "music_mood"
    USE_CASE = "use_case"
    PLAYLIST_PATTERN = "playlist_pattern"
    ARTIST_ADJACENT_TERM = "artist_adjacent_term"
    LONG_TAIL = "long_tail"
    COMPETITOR_DISCOVERY = "competitor_discovery"


class CollectionRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProviderObjectType(StrEnum):
    VIDEO = "video"
    CHANNEL = "channel"
    PLAYLIST = "playlist"
    SEARCH_RESULT_PAGE = "search_result_page"
    TRACK = "track"
    TREND_POINT = "trend_point"


class EntityType(StrEnum):
    CHANNEL = "channel"
    VIDEO = "video"
    PLAYLIST = "playlist"
    TRACK = "track"
    ARTIST = "artist"
    QUERY = "query"
    CONCEPT = "concept"


class EntityStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    MERGED = "merged"


class ArmStatus(StrEnum):
    """§16. P2 owns arm *state*; P1 owns this lifecycle field on its own rows (R15)."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class ConnectorErrorClass(StrEnum):
    """§23's taxonomy. One token per failure mode; never a free-string error."""

    AUTH_FAILURE = "AUTH_FAILURE"
    RATE_LIMIT = "RATE_LIMIT"
    TRANSIENT_NETWORK = "TRANSIENT_NETWORK"
    PROVIDER_5XX = "PROVIDER_5XX"
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    SCHEMA_CHANGE = "SCHEMA_CHANGE"
    PARSER_FAILURE = "PARSER_FAILURE"
    DATA_QUALITY = "DATA_QUALITY"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"


class FreshnessBucket(StrEnum):
    """§21: 0–48h fresh, 3–7d normal, 8–30d stale, >30d expired."""

    FRESH = "fresh"
    NORMAL = "normal"
    STALE = "stale"
    EXPIRED = "expired"


class EvidenceClass(StrEnum):
    """§31: the report must distinguish these four; they are never merged."""

    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    CLASSIFIED = "CLASSIFIED"
    ASSUMED = "ASSUMED"


class ScoringComponent(StrEnum):
    """§17.2 component keys — the wire dict of §20 uses exactly these names."""

    DEMAND = "demand"
    GROWTH = "growth"
    COMPETITOR_VELOCITY = "competitor_velocity"
    COMPETITION = "competition"
    AUDIENCE_QUALITY = "audience_quality"
    CROSS_PLATFORM = "cross_platform"
    PRODUCTION_FIT = "production_fit"


class ChangeEventType(StrEnum):
    """§32 examples, as stored tokens."""

    OPPORTUNITY_SCORE_CHANGE = "opportunity_score_change"
    GROWTH_COMPONENT_CHANGE = "growth_component_change"
    NEW_QUERY_CLUSTER = "new_query_cluster"
    COMPETITOR_VELOCITY_SPIKE = "competitor_velocity_spike"
    SOURCE_AVAILABILITY_DEGRADED = "source_availability_degraded"


class TaxonomyDimension(StrEnum):
    """§14.2's thirteen dimensions; R14 makes this set authoritative over AC-05.

    The member order is the canonical fingerprint order — do not reorder.
    """

    GENRE = "genre"
    SUBGENRE = "subgenre"
    MOOD = "mood"
    USE_CASE = "use_case"
    LANGUAGE = "language"
    VOCAL_MODE = "vocal_mode"
    VOCAL_GENDER = "vocal_gender"
    ENERGY = "energy"
    BPM_RANGE = "bpm_range"
    DURATION_BUCKET = "duration_bucket"
    VISUAL_WORLD = "visual_world"
    AUDIENCE_INTENT = "audience_intent"
    TITLE_ARCHETYPE = "title_archetype"
