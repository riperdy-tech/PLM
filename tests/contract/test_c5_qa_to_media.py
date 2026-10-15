# tests/contract/test_c5_qa_to_media.py
"""Seam C5 — PHASE-P5 → PHASE-P6 (release manifest). FROZEN by R7, R8, ADR-0025, ADR-0028.

Producer: P5's real gate engine, and its real audited-override path, serving manifests
through ``ReleaseManifest.to_wire()``. Consumer: P6's real ``assemble_playlist`` /
``assemble_media_package``. Both use ``plm_contracts.seams.c5.ReleaseManifestPayload``.
P6 reads each track's audio from an in-memory artifact store by the manifest's URI and
checksum, and renders with the stand-in toolchain (no FFmpeg): this proves the seam,
not the render.

What this proves:

* a passed candidate's manifest carries its lineage and artifact facts into P6 unchanged;
* an override keeps the automated disposition and adds an override record, and P6 admits
  it only because of that record;
* a quarantined or rejected track with no override, a missing fact, a fifth "disposition"
  or mixed lineage is refused by P6, never assembled.
"""

from __future__ import annotations

import json
import math
import struct
import tempfile
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from plm_contracts import GateDisposition
from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c3 import DurationRangeSeconds
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_platform.clock import FixedClock
from plm_platform.exceptions import ExceptionQueue, StaticReviewerDirectory

from services.media_factory.audio.features import measure_features
from services.media_factory.playlist.assembler import PlaylistAssemblyError, assemble_playlist
from services.media_factory.rendering.assembler import assemble_media_package
from services.media_factory.visuals.planner import BrandProfile
from services.quality_gate.domain.enums import ResolutionCode
from services.quality_gate.gate.engine import run_candidate_evaluation
from services.quality_gate.gate.persistence import persist_gate_result
from services.quality_gate.gate.quarantine import resolve_quarantine
from tests.support.contract import SeamRegistry, assert_refused, with_value, without
from tests.support.media_backend import InMemoryArtifactStore, StandInToolchain
from tests.support.qa_db import qa_db_session
from tests.support.rights_detectors import configured_detectors
from tests.support.seams import candidate_event, lineage_fields, rights_fields

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

REGISTRY_ENTRY = "C5 · PHASE-P5 → PHASE-P6"
BRAND = BrandProfile(brand_id="brand_neon", brand_name="Neon Records")


def _wav(tmp: Path, name: str, seconds: float = 2.0) -> Path:
    path = tmp / f"{name}.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        frames = bytearray()
        for i in range(int(seconds * 44100)):
            v = int(15000 * math.sin(2.0 * math.pi * 440.0 * i / 44100))
            frames.extend(struct.pack("<hh", v, v))
        wf.writeframes(frames)
    return path


def _passed_manifest(tmp: Path, candidate_id: str, *, seconds: int = 2,
                     store: InMemoryArtifactStore | None = None,
                     **event_kw: Any) -> dict[str, Any]:
    path = _wav(tmp, candidate_id, float(seconds))
    if store is not None:  # P6 reads the artifact by URI and checksum
        stored = store.put_source(f"{candidate_id}/full_mix.wav", path.read_bytes())
        event_kw = {"artifact_uri": stored.uri, "artifact_sha256": stored.sha256, **event_kw}
    result = run_candidate_evaluation(
        event=candidate_event(
            candidate_id=candidate_id, requested_duration_seconds=seconds,
            requested_duration_range={"min_seconds": 1, "max_seconds": max(2, seconds)},
            measured_audio={"duration_seconds": float(seconds), "sample_rate": 44100,
                            "channels": 2},
            **event_kw,
        ),
        audio_path=path,
        clock=FixedClock(FIXED_NOW),
        # R4: the rights detectors run on controlled synthetic references (ADR-0034);
        # the P28 facts travel on the C4 event (ADR-0035).
        detectors=configured_detectors(),
    )
    assert result.release_manifest is not None, result.disposition
    return json.loads(json.dumps(result.release_manifest.to_wire()))


@pytest.fixture(scope="module")
def tmp() -> Path:
    return Path(tempfile.mkdtemp())


@pytest.fixture(scope="module")
def passed(tmp: Path) -> dict[str, Any]:
    return _passed_manifest(tmp, "cand_c5_a")


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    contract_registry.require_entry(REGISTRY_ENTRY)


def test_producer_emits_exactly_the_shared_wire(passed: dict[str, Any]) -> None:
    assert tuple(passed) == tuple(ReleaseManifestPayload.model_fields)
    assert passed["disposition"] in ("PASSED", "PASSED_WITH_WARNINGS")
    assert passed["override"] is None
    assert passed["lineage"] == lineage_fields()


def test_real_manifests_assemble_into_a_package_with_their_lineage(tmp: Path) -> None:
    store = InMemoryArtifactStore()
    wires = [_passed_manifest(tmp, f"cand_c5_{n}", seconds=12, store=store,
                              track_spec_id=f"trk_c5_{n}") for n in "abc"]
    manifests = [parse_seam(ReleaseManifestPayload, m) for m in wires]
    result = assemble_media_package(
        track_manifests=manifests,
        brand_profile=BRAND,
        duration_range=DurationRangeSeconds(min_seconds=30, max_seconds=34),
        visual_profile="brand_neon_rain_v1",
        playlist_concept="Contract Session",
        store=store,
        toolchain=StandInToolchain(),
        render_profile_name="preview_360p_v1",
        clock=FixedClock(FIXED_NOW),
    )
    pkg = result.media_package
    assert pkg.lineage is not None
    assert pkg.lineage.model_dump() == wires[0]["lineage"]
    by_song = {w["candidate_id"]: w for w in wires}
    assert {t.song_id for t in result.playlist_tracks} == set(by_song)
    for track in result.playlist_tracks:
        assert track.audio_uri == by_song[track.song_id]["artifact_uri"]
        assert track.audio_checksum == by_song[track.song_id]["artifact_sha256"]


def test_audited_override_keeps_the_automated_disposition(tmp: Path) -> None:
    path = _wav(tmp, "cand_c5_quar")
    result = run_candidate_evaluation(
        event=candidate_event(
            candidate_id="cand_c5_quar", requested_duration_seconds=2,
            measured_audio={"duration_seconds": 2.0, "sample_rate": 44100, "channels": 2},
            rights=rights_fields(commercial_use_status="unknown"),
        ),
        audio_path=path,
        clock=FixedClock(FIXED_NOW),
        # R4: the rights detectors run on controlled synthetic references and the P28
        # generation facts are declared here, because C4 does not carry them yet
        # (docs/STATUS.md; without them P5 quarantines, ADR-0034).
        detectors=configured_detectors(),
    )
    assert result.disposition.value == "QUARANTINED"
    assert result.quarantine_case is not None and result.release_manifest is None

    with qa_db_session() as session:
        persist_gate_result(session, result)
        _, manifest = resolve_quarantine(
            session, result.quarantine_case.id, ResolutionCode.CLEARED_BY_OVERRIDE,
            "auditor_01", "reviewed",
            now=FIXED_NOW,
            # Unknown rights are reviewable only by a rights reviewer with the license
            # evidence (R3 exception queue); that is the path exercised here.
            queue=ExceptionQueue(
                reviewers=StaticReviewerDirectory({"auditor_01": ["rights_reviewer"]})
            ),
            reviewer_role="rights_reviewer",
            evidence={
                "provider_license_document_ref": "doc://provider/license/42",
                "license_terms_version": "2026-09",
                "commercial_use_confirmation_ref": "ticket://provider/7781",
            },
        )
    assert manifest is not None
    wire = json.loads(json.dumps(manifest.to_wire()))
    assert wire["disposition"] == GateDisposition.QUARANTINED.value
    assert wire["override"]["status"] == "CLEARED_BY_OVERRIDE"
    assert wire["override"]["quarantine_case_id"] == result.quarantine_case.id
    assert wire["lineage"] == lineage_fields()

    # P6 admits it only because of the override record.
    parsed = parse_seam(ReleaseManifestPayload, wire)
    features = {"cand_c5_quar": measure_features("cand_c5_quar", path.read_bytes())}
    assert assemble_playlist(
        manifests=[parsed], features=features, brand_id="brand_neon",
        duration_range=DurationRangeSeconds(min_seconds=2, max_seconds=2), now=FIXED_NOW,
    ).tracks
    stripped = parse_seam(ReleaseManifestPayload, with_value(wire, "override", None))
    with pytest.raises(PlaylistAssemblyError):
        assemble_playlist(manifests=[stripped], features=features, brand_id="brand_neon",
                          duration_range=DurationRangeSeconds(min_seconds=2, max_seconds=2),
                          now=FIXED_NOW)


@pytest.mark.parametrize(
    "field",
    [
        *(f for f in ReleaseManifestPayload.model_fields),
        "lineage.content_arm_id",
        "lineage.production_directive_id",
    ],
)
def test_p6_refuses_a_manifest_missing_any_required_fact(
    passed: dict[str, Any], field: str
) -> None:
    exc = assert_refused(
        lambda p: parse_seam(ReleaseManifestPayload, p), without(passed, field), SeamRejection,
        because=f"{field} missing",
    )
    assert isinstance(exc, SeamRejection)
    assert exc.error.code == "missing_required_field"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("disposition", "CLEARED_BY_OVERRIDE"),  # not a fifth disposition (R8)
        ("disposition", "cleared"),  # not a state at all (R8)
        ("commercial_use_status", "ELIGIBLE"),  # the shared vocabulary is lowercase
        ("measured_duration_seconds", 0),
        ("artifact_sha256", "chk_default"),
        ("lineage.content_arm_id", "arm_unknown"),
        ("schema_version", "2.0"),
        ("surprise", 1),
    ],
)
def test_p6_refuses_invalid_unknown_or_future_manifests(
    passed: dict[str, Any], field: str, value: Any
) -> None:
    assert_refused(
        lambda p: parse_seam(ReleaseManifestPayload, p), with_value(passed, field, value),
        SeamRejection, because=f"{field}={value!r}",
    )


def test_an_override_on_a_passing_manifest_is_refused(passed: dict[str, Any]) -> None:
    override = {
        "status": "CLEARED_BY_OVERRIDE", "quarantine_case_id": "qc", "auditor_id": "a",
        "resolved_at": "2026-09-25T12:00:00+00:00", "reason_codes": [],
    }
    assert_refused(
        lambda p: parse_seam(ReleaseManifestPayload, p), with_value(passed, "override", override),
        SeamRejection, because="override on PASSED",
    )


def test_p6_refuses_tracks_from_two_arms(tmp: Path, passed: dict[str, Any]) -> None:
    other = _passed_manifest(tmp, "cand_c5_other", lineage=lineage_fields(content_arm_id="arm_b"))
    manifests = [parse_seam(ReleaseManifestPayload, m) for m in (passed, other)]
    with pytest.raises(PlaylistAssemblyError) as exc:
        assemble_playlist(manifests=manifests, features={}, brand_id="brand_neon",
                          duration_range=DurationRangeSeconds(min_seconds=4, max_seconds=4),
                          now=FIXED_NOW)
    assert exc.value.error.code == "mixed_lineage"
