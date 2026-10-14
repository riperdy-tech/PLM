"""P5's hold reason codes and their P29 categories (R4).

Every code here is emitted by a P5 rights, provenance or policy check as a hold: the
candidate is quarantined (never passed, never silently rejected) and the code is raised
into the platform exception queue, where ``plm_platform/policy/hold_classes.json``
classifies it. ``tests/unit/test_qa_detectors_reason_codes.py`` checks both directions:
every code here is classified, and every P29 category has a code with a named emitter.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

__all__ = [
    "AI_POLICY_CONFLICT",
    "AUDIO_FINGERPRINT_UNKNOWN",
    "COMMERCIAL_LICENSE_UNCERTAIN",
    "INCOMPLETE_P28_PROVENANCE",
    "INCOMPLETE_R6_PROVENANCE",
    "INTERNAL_CATALOG_AUDIO_MATCH",
    "LYRIC_SIMILARITY_HIGH",
    "LYRIC_SIMILARITY_UNKNOWN",
    "P5_HOLD_REASON_CODES",
    "P29_CATEGORY_CODES",
    "PROTECTED_ARTIST_REFERENCE_DETECTED",
    "REFERENCE_AUDIO_MATCH",
    "SAMPLE_PROVENANCE_UNCERTAIN",
    "UNAUTHORIZED_VOICE_CLONE_DETECTED",
    "UNKNOWN_COMMERCIAL_LICENSE",
]

# Provenance and commercial use (evaluators/rights.py)
INCOMPLETE_R6_PROVENANCE: Final = "INCOMPLETE_R6_PROVENANCE"
INCOMPLETE_P28_PROVENANCE: Final = "INCOMPLETE_P28_PROVENANCE"
UNKNOWN_COMMERCIAL_LICENSE: Final = "UNKNOWN_COMMERCIAL_LICENSE"
COMMERCIAL_LICENSE_UNCERTAIN: Final = "COMMERCIAL_LICENSE_UNCERTAIN"
SAMPLE_PROVENANCE_UNCERTAIN: Final = "SAMPLE_PROVENANCE_UNCERTAIN"
UNAUTHORIZED_VOICE_CLONE_DETECTED: Final = "UNAUTHORIZED_VOICE_CLONE_DETECTED"
# Audio fingerprint (evaluators/rights.py)
REFERENCE_AUDIO_MATCH: Final = "REFERENCE_AUDIO_MATCH"
INTERNAL_CATALOG_AUDIO_MATCH: Final = "INTERNAL_CATALOG_AUDIO_MATCH"
AUDIO_FINGERPRINT_UNKNOWN: Final = "AUDIO_FINGERPRINT_UNKNOWN"
# Lyric similarity (evaluators/lyrics.py)
LYRIC_SIMILARITY_HIGH: Final = "LYRIC_SIMILARITY_HIGH"
LYRIC_SIMILARITY_UNKNOWN: Final = "LYRIC_SIMILARITY_UNKNOWN"
# Prompt / metadata / lyrics screening (evaluators/policy.py, shared policy file)
PROTECTED_ARTIST_REFERENCE_DETECTED: Final = "PROTECTED_ARTIST_REFERENCE_DETECTED"
AI_POLICY_CONFLICT: Final = "AI_POLICY_CONFLICT"

P5_HOLD_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        INCOMPLETE_R6_PROVENANCE,
        INCOMPLETE_P28_PROVENANCE,
        UNKNOWN_COMMERCIAL_LICENSE,
        COMMERCIAL_LICENSE_UNCERTAIN,
        SAMPLE_PROVENANCE_UNCERTAIN,
        UNAUTHORIZED_VOICE_CLONE_DETECTED,
        REFERENCE_AUDIO_MATCH,
        INTERNAL_CATALOG_AUDIO_MATCH,
        AUDIO_FINGERPRINT_UNKNOWN,
        LYRIC_SIMILARITY_HIGH,
        LYRIC_SIMILARITY_UNKNOWN,
        PROTECTED_ARTIST_REFERENCE_DETECTED,
        AI_POLICY_CONFLICT,
    }
)
"""Codes a P5 check emits as a hold: the candidate is quarantined with this reason."""

P29_CATEGORY_CODES: Final = MappingProxyType(
    {
        # P29 category (docs/architecture.md:910-919): (reason codes, emitting phase)
        "artist reference detected": ((PROTECTED_ARTIST_REFERENCE_DETECTED,), "P5"),
        "copyrighted lyric similarity high": ((LYRIC_SIMILARITY_HIGH,), "P5"),
        "sample provenance uncertain": (
            (SAMPLE_PROVENANCE_UNCERTAIN, REFERENCE_AUDIO_MATCH),
            "P5",
        ),
        "commercial license uncertain": ((COMMERCIAL_LICENSE_UNCERTAIN,), "P5"),
        "unauthorized voice cloning detected": ((UNAUTHORIZED_VOICE_CLONE_DETECTED,), "P5"),
        "AI policy conflict": ((AI_POLICY_CONFLICT,), "P5"),
        # These two happen after the gate, on an external system's decision; P5 cannot
        # observe them. Their owners raise them (P8 submission, P7 publication).
        "distributor rejection": (("DISTRIBUTOR_REJECTION",), "P8"),
        "platform claim": (("PLATFORM_CLAIM",), "P7"),
    }
)
"""Each P29 category, the reason code(s) that report it, and the phase that emits them."""
