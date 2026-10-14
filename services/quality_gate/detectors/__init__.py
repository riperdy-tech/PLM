"""P5 rights detectors: audio fingerprint and lyric similarity (R4, ADR-0034).

Risk signals, not proof of originality. A detector that cannot conclude returns
``UNKNOWN``, which the gate quarantines; it is never clearance.
"""

from .config import (
    AudioFingerprintParams,
    DetectorPolicy,
    DetectorPolicyError,
    LyricSimilarityParams,
    load_detector_policy,
    parse_detector_policy,
)
from .fingerprint import (
    AudioFingerprint,
    AudioUnreadableError,
    ReferenceFingerprint,
    fingerprint_file,
    fingerprint_samples,
    match_fingerprint,
)
from .lyric_similarity import LyricReference, lyric_shingles, match_lyrics
from .outcome import DetectorOutcome, DetectorResult, DetectorUnavailableError
from .stores import (
    AudioFingerprintSource,
    DirectoryLyricCorpus,
    DirectoryReferenceAudioSet,
    FingerprintSet,
    InMemoryFingerprintSource,
    InMemoryLyricCorpus,
    LyricCorpus,
    LyricCorpusSource,
)
from .suite import AudioDetection, RightsDetectors, detect_audio, detect_lyrics

__all__ = [
    "AudioDetection",
    "AudioFingerprint",
    "AudioFingerprintParams",
    "AudioFingerprintSource",
    "AudioUnreadableError",
    "DetectorOutcome",
    "DetectorPolicy",
    "DetectorPolicyError",
    "DetectorResult",
    "DetectorUnavailableError",
    "DirectoryLyricCorpus",
    "DirectoryReferenceAudioSet",
    "FingerprintSet",
    "InMemoryFingerprintSource",
    "InMemoryLyricCorpus",
    "LyricCorpus",
    "LyricCorpusSource",
    "LyricReference",
    "LyricSimilarityParams",
    "ReferenceFingerprint",
    "RightsDetectors",
    "detect_audio",
    "detect_lyrics",
    "fingerprint_file",
    "fingerprint_samples",
    "load_detector_policy",
    "lyric_shingles",
    "match_fingerprint",
    "match_lyrics",
    "parse_detector_policy",
]
