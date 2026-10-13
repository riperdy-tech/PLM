"""Publishing orchestrator and durable state machine for P7 YouTube Publishing Engine (P7 §6, §11, §12, Ruling R9)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from plm_contracts import ExecutionPolicy
from plm_platform.execution import (
    ExternalWriteGuard,
    GuardDecision,
    WriteDecision,
    provider_mode_of,
)
from sqlalchemy.orm import Session

from .client.base import (
    YouTubeAuthError,
    YouTubeClientError,
    YouTubeClientProtocol,
    YouTubeQuotaExceededError,
    YouTubeTransientError,
)
from .domain.enums import (
    FailureReasonCode,
    OperatingMode,
    PublishState,
)
from .domain.ids import build_attempt_id, build_publication_record_id
from .domain.models import (
    PublishAttempt,
    PublishManifest,
    YouTubePublicationRecord,
    YouTubeVideoPublishedEvent,
)
from .storage.repositories import (
    get_publication_record_by_package_id,
    get_publish_attempt_by_id,
    get_publish_attempt_by_idempotency_key,
    insert_publication_record,
    insert_publish_attempt,
    update_publish_attempt,
)

logger = logging.getLogger(__name__)


class PublishingEngineResult:
    """Outcome of a publishing engine execution."""

    def __init__(
        self,
        *,
        attempt: PublishAttempt,
        publication_record: YouTubePublicationRecord | None = None,
        event: YouTubeVideoPublishedEvent | None = None,
        error: str | None = None,
    ) -> None:
        self.attempt = attempt
        self.publication_record = publication_record
        self.event = event
        self.error = error

    @property
    def is_success(self) -> bool:
        return self.attempt.state in {PublishState.PUBLISHED, PublishState.VERIFIED, PublishState.SCHEDULED}


CAPABILITY_UPLOAD = "youtube.upload"
CAPABILITY_THUMBNAIL = "youtube.thumbnail"
CAPABILITY_PLAYLIST = "youtube.playlist"


class YouTubePublishingEngine:
    """Core state machine executing the YouTube publishing pipeline.

    Every write to YouTube (upload, thumbnail, playlist placement) first passes the
    platform :class:`~plm_platform.execution.ExternalWriteGuard`. ``OperatingMode.DRY_RUN``
    caps the policy at dry-run, so it can never upload to a real channel.
    """

    def __init__(
        self,
        client: YouTubeClientProtocol,
        *,
        chunk_size: int = 1024 * 1024,  # 1MB
        max_processing_polls: int = 5,
        write_guard: ExternalWriteGuard | None = None,
    ) -> None:
        self.client = client
        self.chunk_size = chunk_size
        self.max_processing_polls = max_processing_polls
        self.write_guard = write_guard or ExternalWriteGuard()
        self.provider_mode = provider_mode_of(client)

    def _authorize(
        self, capability: str, manifest: PublishManifest, operating_mode: OperatingMode
    ) -> GuardDecision:
        ceiling = ExecutionPolicy.DRY_RUN if operating_mode is OperatingMode.DRY_RUN else None
        return self.write_guard.authorize_external_write(
            capability,
            provider_mode=self.provider_mode,
            idempotency_key=f"{manifest.idempotency_key}:{capability}",
            policy_ceiling=ceiling,
        )

    def _halt_for(
        self, session: Session, attempt: PublishAttempt, decision: GuardDecision
    ) -> PublishingEngineResult:
        """Stop before a write the guard did not permit, recording why on the attempt."""
        message = f"{decision.capability}: {decision.decision.value} ({decision.reason})"
        if decision.decision is WriteDecision.RECORD_INTENT:
            update_publish_attempt(
                session,
                attempt.attempt_id,
                state=PublishState.INTENT_RECORDED,
                error_message=message,
                updated_at=datetime.now(UTC),
            )
            session.commit()
            updated = get_publish_attempt_by_id(session, attempt.attempt_id)
            return PublishingEngineResult(attempt=updated or attempt)
        update_publish_attempt(
            session,
            attempt.attempt_id,
            state=PublishState.FAILED_RETRYABLE,
            error_code=FailureReasonCode.EXTERNAL_WRITE_NOT_AUTHORIZED.value,
            error_message=message,
            updated_at=datetime.now(UTC),
        )
        session.commit()
        updated = get_publish_attempt_by_id(session, attempt.attempt_id)
        return PublishingEngineResult(attempt=updated or attempt, error=message)

    def execute_publish(
        self,
        session: Session,
        manifest: PublishManifest,
        *,
        video_data: bytes | None = None,
        thumbnail_data: bytes | None = None,
        operating_mode: OperatingMode = OperatingMode.DRY_RUN,
    ) -> PublishingEngineResult:
        """Execute or resume publishing for the given manifest."""
        now = datetime.now(UTC)

        # 1. Idempotency Check (P7 §11.1, §11.2)
        existing_rec = get_publication_record_by_package_id(session, manifest.media_package_id)
        if existing_rec:
            logger.info("Found existing publication record for package %s: video %s", manifest.media_package_id, existing_rec.youtube_video_id)
            # Emit Hop 2 event for the existing publication
            event = YouTubeVideoPublishedEvent(
                youtube_video_id=existing_rec.youtube_video_id,
                publish_attempt_id=existing_rec.publish_attempt_id,
                experiment_id=existing_rec.experiment_id,
                content_arm_id=existing_rec.content_arm_id,
                published_at=existing_rec.published_at,
                metadata_version=manifest.metadata_version,
                experiment_family_id=existing_rec.experiment_family_id,
                content_genome_id=existing_rec.content_genome_id,
                production_directive_id=existing_rec.production_directive_id,
                media_package_id=existing_rec.media_package_id,
            )
            attempt = get_publish_attempt_by_idempotency_key(session, manifest.idempotency_key)
            if not attempt:
                attempt = PublishAttempt(
                    attempt_id=build_attempt_id(manifest.manifest_id, 1),
                    manifest_id=manifest.manifest_id,
                    channel_id=manifest.channel_id,
                    idempotency_key=manifest.idempotency_key,
                    state=PublishState.VERIFIED,
                    operating_mode=operating_mode,
                    youtube_video_id=existing_rec.youtube_video_id,
                    created_at=now,
                    updated_at=now,
                    completed_at=now,
                )
            return PublishingEngineResult(attempt=attempt, publication_record=existing_rec, event=event)

        # 2. Lookup or create PublishAttempt
        attempt = get_publish_attempt_by_idempotency_key(session, manifest.idempotency_key)
        if not attempt:
            attempt_id = build_attempt_id(manifest.manifest_id, 1)
            attempt = PublishAttempt(
                attempt_id=attempt_id,
                manifest_id=manifest.manifest_id,
                channel_id=manifest.channel_id,
                idempotency_key=manifest.idempotency_key,
                state=PublishState.READY,
                operating_mode=operating_mode,
                created_at=now,
                updated_at=now,
            )
            insert_publish_attempt(session, attempt)
            session.commit()

        video_bytes = video_data or b"MOCK_VIDEO_BINARY_STREAM"
        total_bytes = len(video_bytes)

        try:
            # 3. Resumable Upload (if not already uploaded)
            youtube_video_id = attempt.youtube_video_id
            if not youtube_video_id:
                decision = self._authorize(CAPABILITY_UPLOAD, manifest, operating_mode)
                if not decision.decision.may_call_provider:
                    return self._halt_for(session, attempt, decision)
                # Step 3a: Create Upload Session
                if not attempt.upload_session_uri:
                    update_publish_attempt(
                        session,
                        attempt.attempt_id,
                        state=PublishState.UPLOAD_SESSION_CREATING,
                        total_bytes=total_bytes,
                        updated_at=datetime.now(UTC),
                    )
                    session.commit()

                    snippet: dict[str, Any] = {
                        "title": manifest.title,
                        "description": manifest.description,
                        "tags": manifest.tags,
                        "categoryId": manifest.category_id,
                    }
                    status: dict[str, Any] = {
                        "privacyStatus": manifest.privacy_status.value,
                        "containsSyntheticMedia": manifest.contains_synthetic_media,
                        "madeForKids": manifest.made_for_kids,
                    }
                    if manifest.publish_at:
                        status["publishAt"] = manifest.publish_at.isoformat()

                    session_uri = self.client.insert_resumable_session(
                        snippet=snippet,
                        status=status,
                        content_length=total_bytes,
                    )
                    update_publish_attempt(
                        session,
                        attempt.attempt_id,
                        state=PublishState.UPLOADING,
                        upload_session_uri=session_uri,
                        updated_at=datetime.now(UTC),
                    )
                    session.commit()
                else:
                    session_uri = attempt.upload_session_uri

                # Step 3b: Chunked upload
                video_resource: dict[str, Any] | None = None
                bytes_sent = attempt.bytes_uploaded

                while bytes_sent < total_bytes:
                    chunk = video_bytes[bytes_sent : bytes_sent + self.chunk_size]
                    video_resource = self.client.upload_video_chunk(
                        session_uri=session_uri,
                        chunk=chunk,
                        start_byte=bytes_sent,
                        total_bytes=total_bytes,
                    )
                    bytes_sent += len(chunk)
                    update_publish_attempt(
                        session,
                        attempt.attempt_id,
                        bytes_uploaded=bytes_sent,
                        updated_at=datetime.now(UTC),
                    )
                    session.commit()

                if not video_resource or "id" not in video_resource:
                    raise YouTubeClientError("Upload completed but no video resource returned")

                youtube_video_id = str(video_resource["id"])
                update_publish_attempt(
                    session,
                    attempt.attempt_id,
                    state=PublishState.UPLOADED,
                    youtube_video_id=youtube_video_id,
                    updated_at=datetime.now(UTC),
                )
                session.commit()

            # 4. Processing & Verification
            update_publish_attempt(
                session,
                attempt.attempt_id,
                state=PublishState.PROCESSING,
                updated_at=datetime.now(UTC),
            )
            session.commit()

            remote_video = self.client.get_video(youtube_video_id)
            if remote_video is None:
                # Video not found on remote
                update_publish_attempt(
                    session,
                    attempt.attempt_id,
                    state=PublishState.FAILED_RETRYABLE,
                    error_code=FailureReasonCode.REMOTE_NOT_FOUND.value,
                    error_message=f"Remote video {youtube_video_id} not found",
                    updated_at=datetime.now(UTC),
                )
                session.commit()
                return PublishingEngineResult(
                    attempt=attempt, error=f"Remote video {youtube_video_id} not found"
                )

            # 5. Thumbnail upload
            decision = self._authorize(CAPABILITY_THUMBNAIL, manifest, operating_mode)
            if not decision.decision.may_call_provider:
                return self._halt_for(session, attempt, decision)
            thumb_bytes = thumbnail_data or b"MOCK_THUMBNAIL_BINARY_IMAGE"
            update_publish_attempt(
                session,
                attempt.attempt_id,
                state=PublishState.THUMBNAIL_PENDING,
                updated_at=datetime.now(UTC),
            )
            session.commit()

            self.client.set_thumbnail(youtube_video_id, thumb_bytes)
            update_publish_attempt(
                session,
                attempt.attempt_id,
                state=PublishState.THUMBNAIL_SET,
                thumbnail_uploaded=True,
                updated_at=datetime.now(UTC),
            )
            session.commit()

            # 6. Playlist placement (if configured)
            playlist_id = manifest.playlist_id
            playlist_item_id = None
            if playlist_id:
                decision = self._authorize(CAPABILITY_PLAYLIST, manifest, operating_mode)
                if not decision.decision.may_call_provider:
                    return self._halt_for(session, attempt, decision)
                update_publish_attempt(
                    session,
                    attempt.attempt_id,
                    state=PublishState.PLAYLIST_PENDING,
                    updated_at=datetime.now(UTC),
                )
                session.commit()

                item = self.client.insert_playlist_item(playlist_id, youtube_video_id)
                playlist_item_id = item.get("id")
                update_publish_attempt(
                    session,
                    attempt.attempt_id,
                    state=PublishState.PLAYLIST_ATTACHED,
                    youtube_playlist_id=playlist_id,
                    youtube_playlist_item_id=playlist_item_id,
                    updated_at=datetime.now(UTC),
                )
                session.commit()

            # 7. Final State: Scheduled or Published
            final_state = PublishState.SCHEDULED if manifest.publish_at else PublishState.VERIFIED
            completed_time = datetime.now(UTC)

            update_publish_attempt(
                session,
                attempt.attempt_id,
                state=final_state,
                completed_at=completed_time,
                updated_at=completed_time,
            )
            session.commit()

            # 8. Create Publication Record & Seam C6 Hop 2 Event (Ruling R9)
            rec_id = build_publication_record_id(youtube_video_id)
            record = YouTubePublicationRecord(
                publication_record_id=rec_id,
                youtube_video_id=youtube_video_id,
                channel_id=manifest.channel_id,
                publish_attempt_id=attempt.attempt_id,
                media_package_id=manifest.media_package_id,
                release_manifest_id=manifest.release_manifest_id,
                experiment_id=manifest.experiment_id,
                experiment_family_id=manifest.experiment_family_id,
                content_arm_id=manifest.content_arm_id,
                content_genome_id=manifest.content_genome_id,
                production_directive_id=manifest.production_directive_id,
                title=manifest.title,
                privacy_status=manifest.privacy_status,
                contains_synthetic_media=manifest.contains_synthetic_media,
                published_at=completed_time,
                video_url=f"https://www.youtube.com/watch?v={youtube_video_id}",
                thumbnail_url=f"https://i.ytimg.com/vi/{youtube_video_id}/maxresdefault.jpg",
                playlist_id=playlist_id,
                created_at=completed_time,
                updated_at=completed_time,
            )
            insert_publication_record(session, record)
            session.commit()

            # Hop 2 Event carrying all R9 identity fields
            event = YouTubeVideoPublishedEvent(
                youtube_video_id=youtube_video_id,
                publish_attempt_id=attempt.attempt_id,
                experiment_id=manifest.experiment_id,
                content_arm_id=manifest.content_arm_id,
                published_at=completed_time,
                metadata_version=manifest.metadata_version,
                experiment_family_id=manifest.experiment_family_id,
                content_genome_id=manifest.content_genome_id,
                production_directive_id=manifest.production_directive_id,
                media_package_id=manifest.media_package_id,
            )

            # Reload updated attempt
            updated_attempt = get_publish_attempt_by_idempotency_key(session, manifest.idempotency_key)
            return PublishingEngineResult(
                attempt=updated_attempt or attempt,
                publication_record=record,
                event=event,
            )

        except YouTubeAuthError as exc:
            logger.error("Publishing auth error: %s", exc)
            update_publish_attempt(
                session,
                attempt.attempt_id,
                state=PublishState.FAILED_PERMANENT,
                error_code=FailureReasonCode.AUTH_TOKEN_EXPIRED.value,
                error_message=str(exc),
                updated_at=datetime.now(UTC),
            )
            session.commit()
            updated_attempt = get_publish_attempt_by_id(session, attempt.attempt_id)
            return PublishingEngineResult(attempt=updated_attempt or attempt, error=str(exc))

        except YouTubeQuotaExceededError as exc:
            logger.warning("Publishing quota exceeded: %s", exc)
            update_publish_attempt(
                session,
                attempt.attempt_id,
                state=PublishState.FAILED_RETRYABLE,
                error_code=FailureReasonCode.QUOTA_EXCEEDED.value,
                error_message=str(exc),
                updated_at=datetime.now(UTC),
            )
            session.commit()
            updated_attempt = get_publish_attempt_by_id(session, attempt.attempt_id)
            return PublishingEngineResult(attempt=updated_attempt or attempt, error=str(exc))

        except YouTubeTransientError as exc:
            logger.warning("Publishing transient failure: %s", exc)
            update_publish_attempt(
                session,
                attempt.attempt_id,
                state=PublishState.FAILED_RETRYABLE,
                error_code=FailureReasonCode.UPLOAD_FAILED.value,
                error_message=str(exc),
                retry_count=attempt.retry_count + 1,
                updated_at=datetime.now(UTC),
            )
            session.commit()
            updated_attempt = get_publish_attempt_by_id(session, attempt.attempt_id)
            return PublishingEngineResult(attempt=updated_attempt or attempt, error=str(exc))

        except Exception as exc:
            logger.exception("Unexpected error during YouTube publishing")
            update_publish_attempt(
                session,
                attempt.attempt_id,
                state=PublishState.FAILED_PERMANENT,
                error_code=FailureReasonCode.UPLOAD_FAILED.value,
                error_message=str(exc),
                updated_at=datetime.now(UTC),
            )
            session.commit()
            updated_attempt = get_publish_attempt_by_id(session, attempt.attempt_id)
            return PublishingEngineResult(attempt=updated_attempt or attempt, error=str(exc))
