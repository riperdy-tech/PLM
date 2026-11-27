"""FastAPI routes for `/youtube` publishing service.

A publish whose write the external-write guard refused (``deny``) answers ``409`` with
the code ``external_write_deny`` and the message ``"<capability> not performed:
<reason>"`` (the same shape as P8), after recording the attempt; nothing was sent. A
retry with the same package resumes the attempt and asks the guard again (R5).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from plm_contracts import ErrorShape, ObservationWindow, ProviderMode
from plm_contracts.seams import ExperimentLineage, SeamRejection, parse_seam
from plm_contracts.seams.c6 import MediaPackagePayload
from plm_platform.clearance import ClearanceVerifier
from plm_platform.clock import Clock
from plm_platform.execution import ExternalWriteGuard, WriteDecision, provider_mode_of
from pydantic import AwareDatetime

from ..analytics import (
    AnalyticsNotFinal,
    AnalyticsReportMalformed,
    AnalyticsUnavailable,
    YouTubeAnalyticsSource,
)
from ..client.base import YouTubeClientError
from ..client.mock import MockYouTubeClient
from ..domain.enums import FailureReasonCode, OperatingMode
from ..domain.ids import build_manifest_id
from ..domain.models import (
    ChannelProfile,
    PublishManifest,
    YouTubePublicationRecord,
    YouTubeVideoPublishedEvent,
)
from ..engine import YouTubePublishingEngine
from ..manifests import (
    CandidateSelectionError,
    PackageNotReadyError,
    build_publish_manifest,
    conflicts_with_stored_choice,
    preflight_metadata,
)
from ..preflight.gates import evaluate_preflight_all
from ..series import load_series_playlists
from ..storage.repositories import (
    get_channel_profile_by_channel_id,
    get_publication_record_by_package_id,
    get_publication_record_by_video_id,
    get_publish_attempt_by_id,
    get_publish_attempt_by_idempotency_key,
    get_publish_manifest_by_id,
    insert_channel_profile,
    insert_publish_manifest,
    replace_channel_profile,
)
from .schemas import (
    AttemptStatusResponse,
    ChannelProfileRequest,
    ChannelProfileResponse,
    HealthResponse,
    PublishRequest,
    PublishResponse,
    ReconcileResponse,
    VideoAnalyticsResponse,
)


def _publication_event(
    record: YouTubePublicationRecord, manifest: PublishManifest | None
) -> dict[str, Any] | None:
    """The C6 hop-2 wire of a stored publication (``None`` without its manifest)."""
    if manifest is None:
        return None
    return YouTubeVideoPublishedEvent.build(
        event="youtube_video_published",
        youtube_video_id=record.youtube_video_id,
        publish_attempt_id=record.publish_attempt_id,
        channel_id=record.channel_id,
        media_package_id=record.media_package_id,
        lineage=ExperimentLineage(
            experiment_id=record.experiment_id,
            experiment_family_id=record.experiment_family_id,
            content_arm_id=record.content_arm_id,
            content_genome_id=record.content_genome_id,
            production_directive_id=record.production_directive_id,
        ),
        published_at=record.published_at,
        metadata_version=manifest.metadata_version,
        contains_synthetic_media=record.contains_synthetic_media,
    ).to_wire()


def _refused(status: int, code: str, message: str,
             fields: list[str] | None = None) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail=ErrorShape(code=code, field_paths=fields or [], message=message).model_dump(),
    )


def _youtube_publish_time(client: Any, video_id: str) -> datetime:
    """When YouTube says the video became public (``snippet.publishedAt``), read now.

    Refuses a video YouTube does not show as public: its audience is not the one the
    window measures (review 2026-11-26 #33)."""
    try:
        resource = client.get_video(video_id)
    except YouTubeClientError as exc:
        raise _refused(503, "analytics_unavailable",
                       f"YouTube could not be asked about {video_id}: {exc}") from exc
    if not resource:
        raise _refused(409, "video_not_found_on_youtube", f"YouTube has no video {video_id!r}")
    status = resource.get("status") or {}
    privacy = status.get("privacyStatus")
    if privacy != "public":
        raise _refused(409, "video_not_public",
                       f"{video_id} is {privacy or 'of unknown privacy'}; only a public "
                       "video is measured")
    raw = (resource.get("snippet") or {}).get("publishedAt")
    published_at: datetime | None
    try:
        published_at = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        published_at = None
    if published_at is None or published_at.tzinfo is None:
        raise _refused(409, "publish_time_unknown",
                       f"YouTube returned no publish time for {video_id}")
    return published_at


def _channel_response(profile: ChannelProfile, *, created: bool) -> ChannelProfileResponse:
    return ChannelProfileResponse(
        profile_id=profile.id, channel_id=profile.channel_id,
        channel_name=profile.channel_name, brand_id=profile.brand_id,
        oauth_credential_ref=profile.oauth_credential_ref,
        default_made_for_kids=profile.default_made_for_kids,
        default_language=profile.default_language, default_country=profile.default_country,
        default_category_id=profile.default_category_id,
        default_privacy_mode=profile.default_privacy_mode.value,
        default_contains_synthetic_media=profile.default_contains_synthetic_media,
        default_publish_timezone=profile.default_publish_timezone, active=profile.active,
        operating_mode=profile.operating_mode.value, created_at=profile.created_at,
        updated_at=profile.updated_at, created=created,
    )


def create_youtube_router(
    session_factory: Any,
    auth_dependency: Any,
    client: Any = None,
    *,
    clock: Clock,
    clearance: ClearanceVerifier,
    write_guard: ExternalWriteGuard | None = None,
    write_guard_factory: Callable[[], ExternalWriteGuard] | None = None,
    media_bytes: Callable[[str, str], bytes] | None = None,
    analytics: YouTubeAnalyticsSource | None = None,
) -> APIRouter:
    """``analytics`` is the YouTube Analytics source (only a mock before R6); ``None``
    makes the analytics route refuse with ``analytics_source_not_configured``.

    ``write_guard_factory`` builds the guard for each publish, so a grant the owner adds
    while the service runs (the dashboard's approval) is seen; it wins over
    ``write_guard``. ``media_bytes(uri, checksum)`` fetches the package's own video and
    thumbnail bytes for a real client (a real upload without them is refused)."""
    router = APIRouter(prefix="/youtube", tags=["youtube"])
    yt_client = client or MockYouTubeClient()
    guard = write_guard or ExternalWriteGuard()

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            version="v1",
            timestamp=clock.now(),
        )

    @router.put("/channels/{channel_id}", response_model=ChannelProfileResponse,
                dependencies=[Depends(auth_dependency)])
    def configure_channel(channel_id: str, req: ChannelProfileRequest,
                          response: Response) -> ChannelProfileResponse:
        """Create (201) or replace (200) the channel's profile. A replay of the same facts
        changes nothing, not even ``updated_at``."""
        if not channel_id.strip() or len(channel_id) > 64:
            raise _refused(422, "invalid_channel_id",
                           "channel_id must be 1-64 characters", ["channel_id"])
        now = clock.now()
        facts = req.model_dump()
        with session_factory() as session:
            stored = get_channel_profile_by_channel_id(session, channel_id)
            if stored is None:
                profile = ChannelProfile(id=f"prof_{channel_id}", channel_id=channel_id,
                                         created_at=now, updated_at=now, **facts)
                insert_channel_profile(session, profile)
                session.commit()
                response.status_code = 201
                return _channel_response(profile, created=True)
            if stored.model_dump(include=set(facts)) == facts:
                return _channel_response(stored, created=False)
            profile = ChannelProfile(id=stored.id, channel_id=channel_id,
                                     created_at=stored.created_at, updated_at=now, **facts)
            replace_channel_profile(session, profile)
            session.commit()
            return _channel_response(profile, created=False)

    @router.post("/publish", response_model=PublishResponse, dependencies=[Depends(auth_dependency)])
    def publish_package(req: PublishRequest) -> PublishResponse:
        now = clock.now()
        try:
            pkg = parse_seam(MediaPackagePayload, req.media_package)
        except SeamRejection as exc:
            raise HTTPException(
                status_code=422,
                detail=ErrorShape(
                    code=exc.error.code,
                    field_paths=[f"media_package.{p}" for p in exc.field_paths],
                    message=exc.error.message,
                ).model_dump(),
            ) from exc

        with session_factory() as session:
            # 1. The channel must be configured by an operator (P0 §10); never invented.
            profile = get_channel_profile_by_channel_id(session, req.channel_id)
            if not profile:
                raise HTTPException(
                    status_code=409,
                    detail=ErrorShape(
                        code="channel_not_configured",
                        field_paths=["channel_id"],
                        message=f"no channel profile for {req.channel_id!r}; an operator "
                        "must configure the channel and its OAuth credential first",
                    ).model_dump(),
                )

            # 2. The manifest id is derived from the channel, the package and its video
            # checksum, so a repeated publish (a caller's retry, a processing poll)
            # resumes the stored manifest and attempt. One identity is one immutable
            # publication: a configuration edit since then changes nothing, and an
            # explicit selection naming other candidates is refused.
            stored = get_publish_manifest_by_id(session, build_manifest_id(
                profile.channel_id, pkg.media_package_id, pkg.video.checksum))
            if stored is not None:
                if conflicts_with_stored_choice(
                        stored, metadata_variant_id=req.metadata_variant_id,
                        thumbnail_id=req.thumbnail_id):
                    raise _refused(
                        409, "candidate_selection_conflict",
                        "this publication identity already has a different frozen assignment",
                        ["metadata_variant_id", "thumbnail_id"],
                    )
                manifest = stored
            else:
                # A new publication: manifest from the package's facts, then preflight.
                try:
                    manifest = build_publish_manifest(
                        pkg, profile, publish_at=req.publish_at, now=now,
                        metadata_variant_id=req.metadata_variant_id,
                        thumbnail_id=req.thumbnail_id,
                        series_playlists=load_series_playlists(),
                    )
                except PackageNotReadyError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail=ErrorShape(
                            code="package_not_ready",
                            field_paths=["media_package.status"],
                            message=str(exc),
                        ).model_dump(),
                    ) from exc
                except CandidateSelectionError as exc:
                    raise _refused(
                        422, "candidate_selection_invalid", str(exc),
                        ["metadata_variant_id", "thumbnail_id"],
                    ) from exc
                passed, results, reason = evaluate_preflight_all(
                    profile, pkg.to_wire(),
                    preflight_metadata(pkg, metadata_variant_id=manifest.metadata_version),
                    req.publish_at, now=now,
                    selected_thumbnail_id=(manifest.selection.thumbnail_id
                                           if manifest.selection else None),
                )
                if not passed:
                    diags = [d for r in results for d in r.diagnostics]
                    raise HTTPException(
                        status_code=422,
                        detail=ErrorShape(
                            code=reason.value if reason else "preflight_failed",
                            message="; ".join(diags),
                        ).model_dump(),
                    )
                insert_publish_manifest(session, manifest)
                session.commit()

            # 4. Execute Publishing Engine. A real upload sends the package's own bytes,
            # fetched by their pinned digests; a mock client uses its placeholders.
            video_data = thumbnail_data = None
            # An already-published package answers from its record without its bytes,
            # which may be archived off this machine by now (P6, 2026-11-27).
            published = get_publication_record_by_package_id(
                session, manifest.media_package_id) is not None
            # A processing poll of an uploaded video fetches (and hashes) nothing it
            # already sent: the 821 MB master was re-read on every poll.
            attempt = get_publish_attempt_by_idempotency_key(session, manifest.idempotency_key)
            need_video = attempt is None or not attempt.youtube_video_id
            need_thumbnail = attempt is None or not attempt.thumbnail_uploaded
            if (provider_mode_of(yt_client) is ProviderMode.REAL and not published
                    and (need_video or need_thumbnail)):
                if media_bytes is None:
                    raise _refused(503, "media_store_not_configured",
                                   "a real upload needs the package's stored bytes", [])
                if need_video:
                    video_data = media_bytes(manifest.video_master_uri,
                                             manifest.video_checksum)
                if need_thumbnail:
                    thumbnail_data = media_bytes(manifest.thumbnail_uri,
                                                 manifest.thumbnail_checksum)
            engine = YouTubePublishingEngine(
                yt_client, clock=clock, clearance=clearance,
                write_guard=guard if write_guard_factory is None else write_guard_factory(),
            )
            result = engine.execute_publish(
                session,
                manifest,
                video_data=video_data,
                thumbnail_data=thumbnail_data,
                operating_mode=OperatingMode(req.operating_mode),
            )

            denied = [d for d in engine.decisions if d.decision is WriteDecision.DENY]
            if denied and result.attempt.error_code == (
                    FailureReasonCode.EXTERNAL_WRITE_NOT_AUTHORIZED.value):
                raise _refused(
                    409, f"external_write_{denied[-1].decision.value}",
                    f"{denied[-1].capability} not performed: {denied[-1].reason}")

            vid_id = result.attempt.youtube_video_id
            record = result.publication_record
            return PublishResponse(
                attempt_id=result.attempt.attempt_id,
                manifest_id=manifest.manifest_id,
                candidate_selection=manifest.selection.model_dump(mode="json")
                if manifest.selection else None,
                state=result.attempt.state.value,
                youtube_video_id=vid_id,
                video_url=record.video_url if record else None,
                error=result.error,
                publication_event=result.event.to_wire() if result.event else None,
                published_at=record.published_at if record else None,
                external_writes=engine.write_receipts(manifest, result),
            )

    @router.get("/attempts/{attempt_id}", response_model=AttemptStatusResponse, dependencies=[Depends(auth_dependency)])
    def get_attempt(attempt_id: str) -> AttemptStatusResponse:
        with session_factory() as session:
            attempt = get_publish_attempt_by_id(session, attempt_id)
            if not attempt:
                raise HTTPException(
                    status_code=404,
                    detail=ErrorShape(code="attempt_not_found", message=f"Attempt {attempt_id} not found").model_dump(),
                )
            manifest = get_publish_manifest_by_id(session, attempt.manifest_id)
            record = (
                get_publication_record_by_package_id(session, manifest.media_package_id)
                if manifest is not None else None
            )
            return AttemptStatusResponse(
                attempt_id=attempt.attempt_id,
                manifest_id=attempt.manifest_id,
                candidate_selection=manifest.selection.model_dump(mode="json")
                if manifest and manifest.selection else None,
                channel_id=attempt.channel_id,
                state=attempt.state.value,
                operating_mode=attempt.operating_mode.value,
                youtube_video_id=attempt.youtube_video_id,
                thumbnail_uploaded=attempt.thumbnail_uploaded,
                bytes_uploaded=attempt.bytes_uploaded,
                total_bytes=attempt.total_bytes,
                error_code=attempt.error_code,
                error_message=attempt.error_message,
                created_at=attempt.created_at,
                updated_at=attempt.updated_at,
                completed_at=attempt.completed_at,
                published_at=record.published_at if record else None,
                publication_event=_publication_event(record, manifest) if record else None,
            )

    @router.get(
        "/videos/{video_id}/analytics",
        response_model=VideoAnalyticsResponse,
        dependencies=[Depends(auth_dependency)],
    )
    def get_video_analytics(
        video_id: str,
        window: ObservationWindow,
        as_of: AwareDatetime,
    ) -> VideoAnalyticsResponse:
        """One cumulative snapshot of a video P7 published, for ``window`` as of ``as_of``.

        The video must have a publication record (404 otherwise); the source is keyed by
        the record's stored arm. ``as_of`` is the caller's instant (the pipeline's
        ``workflow.now()``).

        A real source measures from YouTube's own publish time (``snippet.publishedAt``,
        when the video became public), read again now, never from when P7 finished: a
        video uploaded private and made public later starts its window then (review
        2026-11-26 #33). A video that is not public is refused (409
        ``video_not_public``). Until the window's Analytics numbers are final (Pacific
        days, plus three days) the answer is 409 ``analytics_not_final``; the caller
        asks again later. A mock source keeps the record's publication time, and an
        observation before it is 422.
        """
        if analytics is None:
            raise _refused(
                409, "analytics_source_not_configured",
                "no YouTube Analytics source: the mock needs PLM_MOCK_OUTCOMES_FILE; a "
                "real one needs YouTube OAuth credentials")
        with session_factory() as session:
            record = get_publication_record_by_video_id(session, video_id)
        if record is None:
            raise _refused(404, "video_not_published", f"P7 published no video {video_id!r}",
                           ["video_id"])
        observed_at: datetime = as_of
        real = analytics.provider_mode is ProviderMode.REAL
        published_at = record.published_at
        if real:
            published_at = _youtube_publish_time(yt_client, video_id)
        if observed_at < published_at:
            if real:
                raise _refused(409, "analytics_not_final",
                               f"{video_id} became public at {published_at.isoformat()}, "
                               "after the observation")
            raise _refused(422, "observation_precedes_publication",
                           "an observation cannot predate the publication it measures",
                           ["as_of"])
        try:
            snapshot = analytics.snapshot(record, window=window, observed_at=observed_at,
                                          published_at=published_at)
        except AnalyticsNotFinal as exc:
            raise _refused(409, "analytics_not_final", str(exc)) from exc
        except AnalyticsUnavailable as exc:
            raise _refused(503, "analytics_unavailable", str(exc)) from exc
        except AnalyticsReportMalformed as exc:
            raise _refused(409, "analytics_report_malformed", str(exc)) from exc
        revenue = snapshot.estimated_revenue

        def number(value: Decimal | None) -> float | None:
            return None if value is None else float(value)

        return VideoAnalyticsResponse(
            video_id=video_id, channel_id=record.channel_id,
            content_arm_id=record.content_arm_id, window=window.value,
            published_at=published_at, observed_at=observed_at,
            provider_mode=analytics.provider_mode.value, source=analytics.source_name,
            model_version=analytics.model_version,
            metrics={
                "views": snapshot.views,
                "watch_time_minutes": number(snapshot.watch_time_minutes),
                "average_view_duration_seconds": number(snapshot.average_view_duration_seconds),
                "average_percentage_viewed": number(snapshot.average_percentage_viewed),
                "subscribers_gained": snapshot.subscribers_gained,
                "subscribers_lost": snapshot.subscribers_lost,
                "estimated_revenue": None if revenue is None else {
                    "amount": str(revenue.amount), "currency": revenue.currency},
            },
        )

    @router.post("/reconcile/{attempt_id}", response_model=ReconcileResponse, dependencies=[Depends(auth_dependency)])
    def reconcile_attempt(attempt_id: str) -> ReconcileResponse:
        with session_factory() as session:
            attempt = get_publish_attempt_by_id(session, attempt_id)
            if not attempt:
                raise HTTPException(
                    status_code=404,
                    detail=ErrorShape(code="attempt_not_found", message=f"Attempt {attempt_id} not found").model_dump(),
                )
            if not attempt.youtube_video_id:
                raise HTTPException(
                    status_code=400,
                    detail=ErrorShape(code="not_uploaded", message=f"Attempt {attempt_id} has no remote video ID").model_dump(),
                )

            remote = yt_client.get_video(attempt.youtube_video_id) or {}
            return ReconcileResponse(
                attempt_id=attempt.attempt_id,
                state=attempt.state.value,
                youtube_video_id=attempt.youtube_video_id,
                remote_status=remote.get("status", {}),
            )

    return router
