"""Publishing orchestrator and durable state machine for P7 YouTube Publishing Engine (P7 §6, §11, §12, Ruling R9)."""

from __future__ import annotations

import logging
from typing import Any

from plm_contracts import ExecutionPolicy, ProviderMode
from plm_contracts.seams import ExperimentLineage
from plm_platform.clearance import ClearanceVerifier, TrackRef
from plm_platform.clock import Clock
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
    caps the policy at dry-run, so it can never upload to a real channel. A funded write
    is settled with the guard after the provider call: committed once it returned,
    released when the engine stops before calling. A provider call that raised leaves the
    reservation held (outcome uncertain) for operator reconciliation.

    Every timestamp the engine records comes from the injected ``clock``, so a run
    replays to identical attempt and publication records under a ``FixedClock``.

    ``decisions`` keeps every guard decision this engine asked for, in order;
    :meth:`write_receipts` turns a finished publication into the receipt of each write
    it comprises, for callers that account for the spend (the pipeline, R5).
    """

    def __init__(
        self,
        client: YouTubeClientProtocol,
        *,
        clock: Clock,
        clearance: ClearanceVerifier,
        chunk_size: int = 1024 * 1024,  # 1MB
        max_processing_polls: int = 5,
        write_guard: ExternalWriteGuard | None = None,
    ) -> None:
        self.client = client
        self._clock = clock
        self._clearance = clearance
        self.chunk_size = chunk_size
        self.max_processing_polls = max_processing_polls
        self.write_guard = write_guard or ExternalWriteGuard()
        self.provider_mode = provider_mode_of(client)
        self.decisions: list[GuardDecision] = []

    @staticmethod
    def write_key(manifest: PublishManifest, capability: str) -> str:
        """The idempotency key the guard decides a write of ``manifest`` under."""
        return f"{manifest.idempotency_key}:{capability}"

    def write_receipts(
        self, manifest: PublishManifest, result: PublishingEngineResult
    ) -> list[dict[str, str]]:
        """One receipt per write a publication comprises (upload, thumbnail, playlist).

        A write decided in this call reports its decision. A write performed by an
        earlier call (a resumed attempt, a replayed publication) reports what must have
        been decided for it to happen: ``simulate`` for a mock client (the guard
        simulates every mock write), ``execute`` for a real one. The spend of an executed
        write is the ledger entry under (capability, key). A publication that did not
        complete reports only this call's decisions (e.g. a recorded intent).
        """
        decided = {d.capability: d for d in self.decisions}
        receipts: list[dict[str, str]] = [
            {"capability": d.capability, "idempotency_key": d.idempotency_key,
             "decision": d.decision.value, "reason": d.reason}
            for d in self.decisions
        ]
        record = result.publication_record
        if record is None:
            return receipts
        performed = [CAPABILITY_UPLOAD, CAPABILITY_THUMBNAIL]
        if record.playlist_id:
            performed.append(CAPABILITY_PLAYLIST)
        mock = self.provider_mode is ProviderMode.MOCK
        for capability in performed:
            if capability in decided:
                continue
            receipts.append({
                "capability": capability,
                "idempotency_key": self.write_key(manifest, capability),
                "decision": (WriteDecision.SIMULATE if mock else WriteDecision.EXECUTE).value,
                "reason": "performed_earlier",
            })
        return receipts

    def _authorize(
        self, capability: str, manifest: PublishManifest, operating_mode: OperatingMode
    ) -> GuardDecision:
        ceiling = ExecutionPolicy.DRY_RUN if operating_mode is OperatingMode.DRY_RUN else None
        decision = self.write_guard.authorize_external_write(
            capability,
            provider_mode=self.provider_mode,
            idempotency_key=self.write_key(manifest, capability),
            policy_ceiling=ceiling,
            now=self._clock.now(),
        )
        self.decisions.append(decision)
        return decision

    def _media_bytes(self, supplied: bytes | None, placeholder_attr: str) -> bytes | None:
        """The bytes to upload: the supplied media, or a mock client's own placeholder.

        A real client never gets placeholder bytes; missing media there is a refusal.
        """
        if supplied:
            return supplied
        if self.provider_mode is ProviderMode.MOCK:
            placeholder = getattr(self.client, placeholder_attr, None)
            return placeholder if isinstance(placeholder, bytes) else None
        return None

    def _missing_media(
        self, session: Session, attempt: PublishAttempt, what: str
    ) -> PublishingEngineResult:
        message = f"no {what} bytes supplied for a real upload; nothing was sent"
        update_publish_attempt(
            session,
            attempt.attempt_id,
            state=PublishState.FAILED_PERMANENT,
            error_code=FailureReasonCode.PREFLIGHT_ARTIFACT_MISSING.value,
            error_message=message,
            updated_at=self._clock.now(),
        )
        session.commit()
        updated = get_publish_attempt_by_id(session, attempt.attempt_id)
        return PublishingEngineResult(attempt=updated or attempt, error=message)

    def _not_cleared(
        self, session: Session, attempt: PublishAttempt, detail: str
    ) -> PublishingEngineResult:
        message = f"release not cleared; nothing was sent: {detail}"
        update_publish_attempt(
            session,
            attempt.attempt_id,
            state=PublishState.BLOCKED_PREFLIGHT,
            error_code=FailureReasonCode.RELEASE_NOT_CLEARED.value,
            error_message=message,
            updated_at=self._clock.now(),
        )
        session.commit()
        updated = get_publish_attempt_by_id(session, attempt.attempt_id)
        return PublishingEngineResult(attempt=updated or attempt, error=message)

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
                updated_at=self._clock.now(),
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
            updated_at=self._clock.now(),
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
        now = self._clock.now()

        # 1. Idempotency Check (P7 §11.1, §11.2)
        existing_rec = get_publication_record_by_package_id(session, manifest.media_package_id)
        if existing_rec:
            logger.info("Found existing publication record for package %s: video %s", manifest.media_package_id, existing_rec.youtube_video_id)
            # Emit Hop 2 event for the existing publication
            event = YouTubeVideoPublishedEvent.build(
                event="youtube_video_published",
                youtube_video_id=existing_rec.youtube_video_id,
                publish_attempt_id=existing_rec.publish_attempt_id,
                channel_id=existing_rec.channel_id,
                media_package_id=existing_rec.media_package_id,
                lineage=ExperimentLineage(
                    experiment_id=existing_rec.experiment_id,
                    experiment_family_id=existing_rec.experiment_family_id,
                    content_arm_id=existing_rec.content_arm_id,
                    content_genome_id=existing_rec.content_genome_id,
                    production_directive_id=existing_rec.production_directive_id,
                ),
                published_at=existing_rec.published_at,
                metadata_version=manifest.metadata_version,
                contains_synthetic_media=existing_rec.contains_synthetic_media,
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

        if not attempt.youtube_video_id:
            # The package is a claim; P5 and the exception queue are the authority. Asked
            # again right before the upload, so a hold raised after packaging stops it.
            report = self._clearance(
                [TrackRef(t.candidate_id, t.artifact_id) for t in manifest.tracks]
            )
            if not report.cleared:
                return self._not_cleared(session, attempt, report.summary())

        video_bytes = self._media_bytes(video_data, "placeholder_video_bytes")
        if video_bytes is None:
            return self._missing_media(session, attempt, "video")
        total_bytes = len(video_bytes)

        try:
            # 3. Resumable Upload (if not already uploaded)
            youtube_video_id = attempt.youtube_video_id
            if not youtube_video_id:
                upload_decision = self._authorize(CAPABILITY_UPLOAD, manifest, operating_mode)
                if not upload_decision.decision.may_call_provider:
                    return self._halt_for(session, attempt, upload_decision)
                # Step 3a: Create Upload Session
                if not attempt.upload_session_uri:
                    update_publish_attempt(
                        session,
                        attempt.attempt_id,
                        state=PublishState.UPLOAD_SESSION_CREATING,
                        total_bytes=total_bytes,
                        updated_at=self._clock.now(),
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
                        upload_key=manifest.idempotency_key,
                    )
                    update_publish_attempt(
                        session,
                        attempt.attempt_id,
                        state=PublishState.UPLOADING,
                        upload_session_uri=session_uri,
                        updated_at=self._clock.now(),
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
                        updated_at=self._clock.now(),
                    )
                    session.commit()

                if not video_resource or "id" not in video_resource:
                    raise YouTubeClientError("Upload completed but no video resource returned")

                youtube_video_id = str(video_resource["id"])
                self.write_guard.settle_external_write(upload_decision, performed=True)
                update_publish_attempt(
                    session,
                    attempt.attempt_id,
                    state=PublishState.UPLOADED,
                    youtube_video_id=youtube_video_id,
                    updated_at=self._clock.now(),
                )
                session.commit()

            # 4. Processing & Verification
            update_publish_attempt(
                session,
                attempt.attempt_id,
                state=PublishState.PROCESSING,
                updated_at=self._clock.now(),
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
                    updated_at=self._clock.now(),
                )
                session.commit()
                return PublishingEngineResult(
                    attempt=attempt, error=f"Remote video {youtube_video_id} not found"
                )

            # 5. Thumbnail upload
            decision = self._authorize(CAPABILITY_THUMBNAIL, manifest, operating_mode)
            if not decision.decision.may_call_provider:
                return self._halt_for(session, attempt, decision)
            thumb_bytes = self._media_bytes(thumbnail_data, "placeholder_thumbnail_bytes")
            if thumb_bytes is None:
                self.write_guard.settle_external_write(
                    decision, performed=False, reason="thumbnail_bytes_missing"
                )
                return self._missing_media(session, attempt, "thumbnail")
            update_publish_attempt(
                session,
                attempt.attempt_id,
                state=PublishState.THUMBNAIL_PENDING,
                updated_at=self._clock.now(),
            )
            session.commit()

            self.client.set_thumbnail(youtube_video_id, thumb_bytes)
            self.write_guard.settle_external_write(decision, performed=True)
            update_publish_attempt(
                session,
                attempt.attempt_id,
                state=PublishState.THUMBNAIL_SET,
                thumbnail_uploaded=True,
                updated_at=self._clock.now(),
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
                    updated_at=self._clock.now(),
                )
                session.commit()

                item = self.client.insert_playlist_item(playlist_id, youtube_video_id)
                self.write_guard.settle_external_write(decision, performed=True)
                playlist_item_id = item.get("id")
                update_publish_attempt(
                    session,
                    attempt.attempt_id,
                    state=PublishState.PLAYLIST_ATTACHED,
                    youtube_playlist_id=playlist_id,
                    youtube_playlist_item_id=playlist_item_id,
                    updated_at=self._clock.now(),
                )
                session.commit()

            # 7. Final State: Scheduled or Published
            final_state = PublishState.SCHEDULED if manifest.publish_at else PublishState.VERIFIED
            completed_time = self._clock.now()

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
            event = YouTubeVideoPublishedEvent.build(
                event="youtube_video_published",
                youtube_video_id=youtube_video_id,
                publish_attempt_id=attempt.attempt_id,
                channel_id=manifest.channel_id,
                media_package_id=manifest.media_package_id,
                lineage=manifest.lineage,
                published_at=completed_time,
                metadata_version=manifest.metadata_version,
                contains_synthetic_media=manifest.contains_synthetic_media,
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
                updated_at=self._clock.now(),
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
                updated_at=self._clock.now(),
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
                updated_at=self._clock.now(),
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
                updated_at=self._clock.now(),
            )
            session.commit()
            updated_attempt = get_publish_attempt_by_id(session, attempt.attempt_id)
            return PublishingEngineResult(attempt=updated_attempt or attempt, error=str(exc))
