"""Unit tests for audio artifact probe and storage (§4.5, §111)."""

from services.music_factory.domain.enums import ArtifactType
from services.music_factory.providers.mock_provider import synthesize_mock_wav
from services.music_factory.storage.artifacts import (
    InMemoryArtifactStorage,
    create_audio_artifact,
    probe_audio_bytes,
)


def test_probe_valid_wav() -> None:
    wav_bytes = synthesize_mock_wav(duration_seconds=2.0, sample_rate=44100, channels=2)
    measured = probe_audio_bytes(wav_bytes)

    assert measured.channels == 2
    assert measured.sample_rate == 44100
    assert measured.codec == "pcm_s16le"
    assert measured.byte_size == len(wav_bytes)
    assert len(measured.sha256) == 64
    assert 1.9 <= measured.duration_seconds <= 2.1


def test_artifact_storage_and_creation() -> None:
    storage = InMemoryArtifactStorage()
    wav_bytes = synthesize_mock_wav(duration_seconds=1.0)
    measured = probe_audio_bytes(wav_bytes)

    uri = storage.put_artifact(
        candidate_id="cand_123",
        name="master.wav",
        data=wav_bytes,
    )
    assert uri == "s3://plm-artifacts/music/cand_123/master.wav"
    assert storage.get_artifact(uri) == wav_bytes

    artifact = create_audio_artifact(
        candidate_id="cand_123",
        object_uri=uri,
        measured=measured,
        artifact_type=ArtifactType.FULL_MIX,
    )
    assert artifact.candidate_id == "cand_123"
    assert artifact.object_uri == uri
    assert artifact.duration_seconds == measured.duration_seconds
    assert artifact.sha256 == measured.sha256
