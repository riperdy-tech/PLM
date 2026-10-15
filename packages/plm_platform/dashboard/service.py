"""Demo data for the visual dashboard, served only behind ``--demo``.

Nothing here is read from the PLM pipeline. Every KPI, arm, candidate and release below
is a fixed literal, and the audio is one synthesized loop. The demo app stamps every
payload with :data:`DEMO_DATA_SOURCE` and the page carries a DEMO DATA banner, so no
consumer can mistake it for live data. The live dashboard (:mod:`.live`) never falls
back to anything in this module.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from typing import Any, Final

DEMO_DATA_SOURCE: Final[str] = "demo"
"""Provenance label carried by every payload of the demo dashboard (``--demo``)."""


class UnknownCaseError(KeyError):
    """A QA resolution named a case the dashboard does not hold."""


class DashboardService:
    """Fixed demo data and synthetic audio for the visual dashboard (not pipeline data)."""

    def __init__(self) -> None:
        self._cases: dict[str, dict[str, Any]] = {
            "case_001": {
                "case_id": "case_001",
                "candidate_id": "cand_cyber_04",
                "reason": "duration_slight_delta",
                "status": "QUARANTINED",
                "resolution": None,
                "notes": "Expected 180.0s, candidate length was 178.5s.",
            }
        }

    def get_stats(self) -> dict[str, Any]:
        """Return platform summary KPIs."""
        return {
            "total_cycles": 18,
            "active_arms": 6,
            "candidates_generated": 54,
            "cleared_releases": 48,
            "quarantined_cases": len([c for c in self._cases.values() if c["status"] == "QUARANTINED"]),
            "total_revenue_usd": 1845.20,
            "avg_normalized_reward": 0.74,
            "loop_status": "READY",
        }

    def get_arms(self) -> list[dict[str, Any]]:
        """Return active bandit arms and Bayesian posterior states."""
        return [
            {
                "arm_id": "arm_synthwave_night",
                "name": "Neon Midnight (Synthwave)",
                "status": "ACTIVE",
                "allocation_type": "EXPLOIT",
                "observation_count": 8,
                "posterior_mean": 0.82,
                "confidence_interval": [0.74, 0.90],
                "reward_history": [0.65, 0.70, 0.78, 0.81, 0.85, 0.88, 0.84, 0.82],
            },
            {
                "arm_id": "arm_lofi_rain",
                "name": "Tokyo Rain (Lo-Fi Chill)",
                "status": "ACTIVE",
                "allocation_type": "EXPLOIT",
                "observation_count": 5,
                "posterior_mean": 0.76,
                "confidence_interval": [0.66, 0.85],
                "reward_history": [0.60, 0.68, 0.75, 0.80, 0.76],
            },
            {
                "arm_id": "arm_ambient_orbit",
                "name": "Orbital Drift (Deep Ambient)",
                "status": "ACTIVE",
                "allocation_type": "EXPLORE",
                "observation_count": 3,
                "posterior_mean": 0.64,
                "confidence_interval": [0.49, 0.79],
                "reward_history": [0.55, 0.62, 0.64],
            },
            {
                "arm_id": "arm_cyberpunk_bass",
                "name": "Neuro Shock (Cyberpunk)",
                "status": "ACTIVE",
                "allocation_type": "EXPLORE",
                "observation_count": 2,
                "posterior_mean": 0.58,
                "confidence_interval": [0.40, 0.76],
                "reward_history": [0.52, 0.58],
            },
        ]

    def get_candidates(self) -> list[dict[str, Any]]:
        """Return generated music candidates catalog."""
        return [
            {
                "candidate_id": "cand_synth_01",
                "title": "Starlight Highway",
                "genre": "Synthwave / Outrun",
                "bpm": 118,
                "key": "A minor",
                "duration_seconds": 184.0,
                "prompt": "Driving analog arpeggios, nostalgic 80s drums, warm tape saturation",
                "rights_status": "COMMERCIALLY_CLEARED",
                "qa_disposition": "PASSED",
                "audio_url": "/api/audio/cand_synth_01",
            },
            {
                "candidate_id": "cand_lofi_02",
                "title": "Midnight Coffee in Shibuya",
                "genre": "Lo-Fi Beats",
                "bpm": 84,
                "key": "F major",
                "duration_seconds": 156.0,
                "prompt": "Dusty rhodes piano, vinyl crackle, mellow bassline, rainy atmosphere",
                "rights_status": "COMMERCIALLY_CLEARED",
                "qa_disposition": "PASSED",
                "audio_url": "/api/audio/cand_lofi_02",
            },
            {
                "candidate_id": "cand_ambient_03",
                "title": "Cassini Echoes",
                "genre": "Cosmic Ambient",
                "bpm": 60,
                "key": "D minor",
                "duration_seconds": 210.0,
                "prompt": "Shimmering pad soundscapes, subterranean drone, generative bells",
                "rights_status": "COMMERCIALLY_CLEARED",
                "qa_disposition": "PASSED",
                "audio_url": "/api/audio/cand_ambient_03",
            },
            {
                "candidate_id": "cand_cyber_04",
                "title": "Sublevel Glitch",
                "genre": "Dark Cyberpunk",
                "bpm": 130,
                "key": "E minor",
                "duration_seconds": 178.5,
                "prompt": "Heavy reese bass, glitch percussion, distorted vocal chops",
                "rights_status": "QUARANTINED",
                "qa_disposition": "QUARANTINED",
                "audio_url": "/api/audio/cand_cyber_04",
            },
        ]

    def get_releases(self) -> list[dict[str, Any]]:
        """Return published YouTube and DSP releases."""
        return [
            {
                "release_id": "rel_synth_01",
                "title": "Starlight Highway",
                "artist": "Neon Horizon",
                "media_package_id": "med_pkg_91823",
                "status": "LIVE",
                "youtube_status": "PUBLISHED",
                "youtube_video_id": "yt_vid_starlight88",
                "dsp_status": "DISTRIBUTED",
                "isrc": "US-PLM-26-00101",
                "upc": "880928371920",
                "manifest_id": "rel_man_882910",
                "published_at": "2026-10-12T18:30:00Z",
            },
            {
                "release_id": "rel_lofi_02",
                "title": "Midnight Coffee in Shibuya",
                "artist": "Rainy Room Records",
                "media_package_id": "med_pkg_91824",
                "status": "LIVE",
                "youtube_status": "PUBLISHED",
                "youtube_video_id": "yt_vid_coffee_rain",
                "dsp_status": "DISTRIBUTED",
                "isrc": "US-PLM-26-00102",
                "upc": "880928371921",
                "manifest_id": "rel_man_882911",
                "published_at": "2026-10-13T09:15:00Z",
            },
        ]

    def generate_audio_wav(self, candidate_id: str) -> bytes:
        """Synthesize a rich harmonic audio buffer (16-bit PCM WAV at 44.1kHz)."""
        sample_rate = 44100
        duration_seconds = 4.0
        num_samples = int(sample_rate * duration_seconds)

        # Base chord progression frequencies (A2, C3, E3, G3, B3)
        chords = [
            [220.0, 261.63, 329.63, 392.00],  # Am7
            [174.61, 220.0, 261.63, 329.63],  # Fmaj7
            [130.81, 164.81, 196.00, 246.94],  # Cmaj7
            [196.00, 246.94, 293.66, 349.23],  # G7
        ]

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)  # mono
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)

            raw_samples = bytearray()
            for i in range(num_samples):
                t = i / sample_rate
                # Determine chord index across duration
                chord_idx = int((t / duration_seconds) * len(chords)) % len(chords)
                active_chord = chords[chord_idx]

                # Envelope for smooth looping
                env = math.sin(math.pi * (t / duration_seconds)) ** 0.5

                sample_val = 0.0
                for freq in active_chord:
                    # Fundamental + soft harmonics
                    harmonic1 = math.sin(2.0 * math.pi * freq * t)
                    harmonic2 = 0.3 * math.sin(4.0 * math.pi * freq * t)
                    harmonic3 = 0.1 * math.sin(6.0 * math.pi * freq * t)
                    sample_val += (harmonic1 + harmonic2 + harmonic3) / len(active_chord)

                # Add a warm subtle tremolo/chorus effect
                tremolo = 0.85 + 0.15 * math.sin(2.0 * math.pi * 3.0 * t)
                final_val = sample_val * env * tremolo * 0.7

                # Clamp to 16-bit integer range
                int_sample = int(max(-1.0, min(1.0, final_val)) * 32767.0)
                raw_samples.extend(struct.pack("<h", int_sample))

            wf.writeframes(raw_samples)

        return buf.getvalue()

    def resolve_qa_case(self, case_id: str, resolution: str, notes: str) -> dict[str, Any]:
        """Resolve a quarantined demo case; an unknown case id raises, never "resolves"."""
        case = self._cases.get(case_id)
        if case is None:
            raise UnknownCaseError(case_id)
        case["status"] = "CLEARED" if resolution == "CLEARED_BY_OVERRIDE" else "REJECTED"
        case["resolution"] = resolution
        case["notes"] = notes
        return {
            "case_id": case_id,
            "status": "resolved",
            "resolution": resolution,
            "notes": notes,
        }
