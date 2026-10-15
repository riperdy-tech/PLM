"""YouTube Analytics for a published video: the source P9 measures from (R5).

Only a mock source exists before R6: :class:`MockYouTubeAnalytics` answers from the
deterministic outcome model (:mod:`plm_platform.testing.mock_outcomes`), keyed by the
publication's stored identity (video id, arm) and the instants the caller passes. It
declares ``provider_mode = mock``, and every answer carries it, so P9 stores the numbers
as ``synthetic``. A real YouTube Analytics client is R6; until then a deployment whose
YouTube provider is real has no analytics source and the route refuses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from plm_contracts import ObservationWindow, ProviderMode
from plm_platform.testing.mock_outcomes import MockOutcomeModel, MockVideoSnapshot

from .domain.models import YouTubePublicationRecord

__all__ = ["MockYouTubeAnalytics", "YouTubeAnalyticsSource"]


class YouTubeAnalyticsSource(Protocol):
    provider_mode: ProviderMode
    source_name: str

    @property
    def model_version(self) -> str | None: ...

    def snapshot(self, record: YouTubePublicationRecord, *, window: ObservationWindow,
                 observed_at: datetime) -> MockVideoSnapshot: ...


class MockYouTubeAnalytics:
    """Deterministic, arm-dependent video metrics; labelled mock."""

    provider_mode = ProviderMode.MOCK
    source_name = "mock_youtube_analytics"

    def __init__(self, model: MockOutcomeModel) -> None:
        self.model = model

    @property
    def model_version(self) -> str | None:
        return self.model.model_version

    def snapshot(self, record: YouTubePublicationRecord, *, window: ObservationWindow,
                 observed_at: datetime) -> MockVideoSnapshot:
        return self.model.video_snapshot(
            video_id=record.youtube_video_id, arm_id=record.content_arm_id,
            published_at=record.published_at, window=window, observed_at=observed_at)
