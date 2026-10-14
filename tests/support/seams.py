"""Complete, valid seam payload fixtures (ADR-0025).

Seam models have no defaults for required facts, so a test that needs "a track spec"
must supply a whole one. These builders do that in one place; tests override only the
fields they care about.
"""

from __future__ import annotations

import hashlib
from typing import Any

from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c4 import CandidateReadyPayload
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_contracts.seams.c6 import MediaPackagePayload
from plm_contracts.seams.c7 import RAW_REWARD_METRICS

__all__ = [
    "candidate_event",
    "candidate_event_fields",
    "directive_fields",
    "lineage_fields",
    "media_package",
    "media_package_fields",
    "publication_fields",
    "release_manifest",
    "release_manifest_fields",
    "rights_fields",
    "sample_lineage",
    "track_spec_fields",
    "unmeasured_raw_metrics",
]


def directive_fields(**overrides: Any) -> dict[str, Any]:
    """A complete C2 production directive (P2 -> P3)."""
    fields: dict[str, Any] = {
        "directive_id": "dir_fixture_001",
        "decision_id": "dec_fixture_001",
        "arm_id": "rnb_late_night_drive_english_vocal_45_75m",
        "allocation_type": "exploit",
        "priority": 1,
        "quantity": 2,
        "brief": {
            "genre": "rnb",
            "mood": ["melancholic", "intimate"],
            "use_case": ["late_night_drive"],
            "language": "english",
            "vocal_mode": "vocal",
            "duration_bucket": "45_75m",
            "visual_world": "rainy_asian_city",
        },
        "expected_value": 88.0,
        "uncertainty": 0.12,
        "budget_limit": 100.0,
        "market_context": {"brand_id": "neon_seoul"},
        "selection_reason": ["high_demand_opportunity"],
        "directive_version": "p2-dir-v1",
        "policy_version": "p2-v1",
        "expires_at": "2026-11-01T00:00:00Z",
        "exploration_fraction": 0.25,
        "value_model": "early-stage-proxy-v1",
    }
    fields.update(overrides)
    return fields


def lineage_fields(**overrides: str) -> dict[str, str]:
    fields = {
        "experiment_id": "exp_test_01",
        "experiment_family_id": "fam_test_01",
        "content_arm_id": "arm_test_01",
        "content_genome_id": "gen_test_01",
        "production_directive_id": "dir_test_01",
    }
    fields.update(overrides)
    return fields


def sample_lineage(**overrides: str) -> ExperimentLineage:
    return ExperimentLineage(**lineage_fields(**overrides))


def track_spec_fields(**overrides: Any) -> dict[str, Any]:
    """Keyword arguments for a complete C3 track spec (no ``schema_version``)."""
    fields: dict[str, Any] = {
        "track_spec_id": "trk_test_01",
        "playlist_spec_id": "ply_test_01",
        "lineage": lineage_fields(),
        "brand_profile_id": "neon_seoul_profile",
        "hard_constraints": {"genre": "rnb", "language": "english", "vocal_mode": "vocal"},
        "bounded_preferences": {
            "bpm": 88,
            "key_profile": "A_minor",
            "mood": ["melancholic"],
            "use_case": ["late_night_drive"],
        },
        "creative_latitude": {"energy": "medium"},
        "lyric_spec": {"lyric_density": "moderate"},
        "vocal_spec": {"vocal_mode": "vocal", "vocal_gender": "female"},
        "instrumentation_spec": {"instrumentation_profile": ["rhodes"]},
        "production_style": "clean_modern",
        "track_role": "focus",
        "requested_duration": 180,
        "candidate_target": 2,
        "priority": 1,
        "budget_limit": 20.0,
        "provider_policy": {"allowed_providers": ["mock"]},
    }
    fields.update(overrides)
    return fields


def rights_fields(**overrides: Any) -> dict[str, Any]:
    """A complete declared rights snapshot (C4 ``rights``)."""
    fields: dict[str, Any] = {
        "commercial_use_status": "eligible",
        "basis": "commercial_subscription",
        "license_reference": "lic_test_01",
        "plan_or_tier": "pro",
        "account_identity_ref": "acct_test_01",
        "terms_version": "terms_2026_01",
        "attempt_id": "att_test_01",
        "provider_id": "mock",
        "policy_version": "p4-v1",
        "checked_at": "2026-09-25T12:00:00+00:00",
        "downloaded_at": "2026-09-25T12:00:00+00:00",
    }
    fields.update(overrides)
    return fields


def candidate_event_fields(**overrides: Any) -> dict[str, Any]:
    """Keyword arguments for a complete C4 event (no ``schema_version``)."""
    fields: dict[str, Any] = {
        "event_type": "music_candidate_ready_for_p5",
        "candidate_id": "cand_test_01",
        "artifact_id": "art_test_01",
        "artifact_uri": "music/test/art_test_01.wav",
        "artifact_sha256": "0" * 64,
        "track_spec_id": "trk_test_01",
        "lineage": lineage_fields(),
        "provider_id": "mock",
        "model_id": "mock-v1",
        "measured_audio": {"duration_seconds": 1.0, "sample_rate": 44100, "channels": 2},
        "requested_duration_seconds": 1,
        "vocal_mode": "instrumental",
        "genre_tags": ["ambient"],
        "prompt_text": "ambient synth pad electronic chill",
        "lyrics_text": None,
        "rights": rights_fields(),
    }
    fields.update(overrides)
    return fields


def candidate_event(**overrides: Any) -> CandidateReadyPayload:
    return CandidateReadyPayload.build(**candidate_event_fields(**overrides))


def release_manifest_fields(**overrides: Any) -> dict[str, Any]:
    """Keyword arguments for a complete C5 track manifest (no ``schema_version``)."""
    candidate = overrides.get("candidate_id", "cand_test_01")
    fields: dict[str, Any] = {
        "p5_manifest_id": f"rel_man_{candidate}",
        "release_manifest_version": "1.0",
        "candidate_id": candidate,
        "canonical_artifact_id": f"art_{candidate}",
        "artifact_uri": f"music/test/{candidate}.wav",
        "artifact_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
        "measured_duration_seconds": 150.0,
        "track_spec_id": "trk_test_01",
        "lineage": lineage_fields(),
        "disposition": "PASSED",
        "override": None,
        "technical_status": "PASSED",
        "commercial_use_status": "eligible",
        "provenance_status": "COMPLETE",
        "ai_disclosure_required": True,
        "rights_risk": "LOW",
        "policy_status": "COMPLIANT",
        "evaluation_id": f"eval_{candidate}",
        "evaluation_policy_version": "p5-v1",
        "warning_codes": [],
        "evidence_bundle_id": f"evi_{candidate}",
    }
    fields.update(overrides)
    return fields


def release_manifest(**overrides: Any) -> ReleaseManifestPayload:
    return ReleaseManifestPayload.build(**release_manifest_fields(**overrides))


def media_package_fields(*candidates: str, **overrides: Any) -> dict[str, Any]:
    """Keyword arguments for a complete C6 hop-1 package over the given recordings."""
    songs = candidates or ("cand_test_01",)
    digest = hashlib.sha256("|".join(songs).encode()).hexdigest()
    fields: dict[str, Any] = {
        "media_package_id": "mpkg_test_01",
        "release_manifest_id": "plm_test_01",
        "lineage": lineage_fields(),
        "brand_id": "brand_neon",
        "ai_disclosure_required": True,
        "video": {
            "master_uri": "media/test/video.mp4",
            "checksum": digest,
            "duration_seconds": 150.0 * len(songs),
            "render_profile": "yt_longform_1080p_v1",
        },
        "audio": {"master_uri": "media/test/audio.wav", "checksum": digest},
        "thumbnails": [
            {"id": "thumb_a", "uri": "media/test/thumb_a.jpg", "checksum": digest,
             "width": 1280, "height": 720},
        ],
        "tracklist": [
            {"position": n, "song_id": song, "title": f"Track {n}", "duration_seconds": 150.0,
             "source_artifact_id": f"art_{song}"}
            for n, song in enumerate(songs, start=1)
        ],
        "chapters": [],
        "metadata_candidates": [
            {"variant_id": "meta_a", "title": "Test Mix", "description": "A test mix.",
             "tags": ["test"], "category_id": "10", "contains_synthetic_media": True},
        ],
        "short_source_assets": [],
        "render_report": {},
        "status": "ready_for_publish",
    }
    fields.update(overrides)
    return fields


def media_package(*candidates: str, **overrides: Any) -> MediaPackagePayload:
    return MediaPackagePayload.build(**media_package_fields(*candidates, **overrides))


def publication_fields(**overrides: Any) -> dict[str, Any]:
    """Keyword arguments for a complete C6 hop-2 ``youtube_video_published`` event."""
    fields: dict[str, Any] = {
        "event": "youtube_video_published",
        "youtube_video_id": "yt_test_01",
        "publish_attempt_id": "att_test_01",
        "channel_id": "UC_test",
        "media_package_id": "mpkg_test_01",
        "lineage": lineage_fields(),
        "published_at": "2026-09-25T12:00:00+00:00",
        "metadata_version": "meta_a",
        "contains_synthetic_media": True,
    }
    fields.update(overrides)
    return fields


def unmeasured_raw_metrics() -> dict[str, float | None]:
    """The C7 leg-2 raw-metric keys, none measured (``null``, never ``0``)."""
    return dict.fromkeys(RAW_REWARD_METRICS)
