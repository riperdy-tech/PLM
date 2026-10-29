"""Autonomous end-to-end album production and publishing pipeline for Rhodes & Rain Records."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.music_factory.providers.suno_client import SunoClient, SunoClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("album_producer")

OUTPUT_DIR = Path("output_media/full_album")
TRACKS_DIR = OUTPUT_DIR / "tracks"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TRACKS_DIR.mkdir(parents=True, exist_ok=True)

ALBUM_TITLE = "Midnight Rain & Rhodes: Cozy Lo-Fi for Late Nights & Rainy Days [Full Album]"
ALBUM_TAGS = [
    "lofi",
    "lofi hip hop",
    "chill beats",
    "rhodes piano",
    "rain sounds",
    "relaxing beats",
    "study music",
    "sleep beats",
    "cozy lofi",
    "rhodes and rain records",
]

# Track definitions: prompts tailored for cohesive warm Rhodes + rain lo-fi vibe
TRACK_SPECS = [
    {
        "title": "First Drop at Dusk",
        "tags": "lofi, rhodes electric piano, chill beats, rain, mellow, instrumental",
        "prompt": "chill lo-fi hip hop, warm vintage rhodes electric piano chords, gentle rain sounds, relaxing study focus beat, 78 bpm",
    },
    {
        "title": "Windowpane Reflections",
        "tags": "lofi, jazzy rhodes, rain sounds, vinyl crackle, chillhop, instrumental",
        "prompt": "jazzy lo-fi beats, soulful rhodes chords, soft tape saturation, gentle drizzle soundscape, 80 bpm",
    },
    {
        "title": "Midnight Coffee Steam",
        "tags": "lofi, rhodes mk1, ambient rain, warm sub bass, cozy, instrumental",
        "prompt": "cozy midnight lofi, warm vintage tremolo rhodes piano, relaxing boom bap rhythm, soft rainfall, 76 bpm",
    },
    {
        "title": "Rain Over Neon Puddles",
        "tags": "lofi, tape saturated rhodes, chill beats, urban rain, instrumental",
        "prompt": "nostalgic lofi hip hop, rich 7th rhodes chords, deep bass, gentle rain on pavement, 82 bpm",
    },
    {
        "title": "Slow Train Through the Mist",
        "tags": "lofi, mellow rhodes, distant rain, dreamy, relaxing, instrumental",
        "prompt": "dreamy lofi chillout, slow rhodes arpeggios, atmospheric rain and distant thunder, vinyl warmth, 75 bpm",
    },
    {
        "title": "Late Night Study Sanctuary",
        "tags": "lofi, soulful rhodes, study beats, focus music, rain, instrumental",
        "prompt": "deep focus study lofi, soothing electric piano chords, steady head-nod drum groove, cozy room rainfall, 80 bpm",
    },
    {
        "title": "Gentle Thunder Far Away",
        "tags": "lofi, atmospheric rhodes, soft thunder, nighttime ambience, instrumental",
        "prompt": "atmospheric chill lofi, warm jazz rhodes voicings, distant rolling thunder, quiet rain ambiance, 74 bpm",
    },
]

TRACK_TITLES = [

    "First Drop at Dusk",
    "Windowpane Reflections",
    "Midnight Coffee Steam",
    "Rain Over Neon Puddles",
    "Slow Train Through the Mist",
    "Late Night Study Sanctuary",
    "Gentle Thunder Far Away",
    "Quiet Attic Glow",
    "Damp Leaves on the Pavement",
    "Low Fidelity Heartbeat",
    "Steam Rising from Asphalt",
    "2 AM Solitude",
    "Amber Streetlight Shadows",
    "Echoes in the Downpour",
    "Fading Static in the Fog",
    "First Light After the Storm",
]


# Existing completed clips from Suno generation batches to harvest
KNOWN_CLIPS = [
    "c2866c17-c1df-48e3-b8cb-c9aeab966459",
    "5080cd9a-98a4-4489-90ce-1f1db9f86aab",
    "c1fbd8d1-5452-4466-821e-d9647b83dbed",
    "202ac3c9-94ca-475e-a915-a2afdcd2cc27",
    "92e22a68-ce7c-4546-994e-ebf1534ee569",
    "304c62f0-023c-4fcf-9ff7-9008d00a63f1",
    "0f308eff-83e4-4548-9e52-df90d912f1cc",
    "712cd340-9f6f-4379-9eb9-99c3f2e75f13",
    "b4411ac7-6f97-45a3-b475-13464ce8e594",
    "ae6c0e96-cb62-4075-a26c-dced2dd3cd80",
    "cad8bd85-7ea1-409a-8b2a-b73001a06260",
    "5cd984dc-8ba6-4812-b529-97855b59b6b4",
    "112db5ec-2aa5-4bd5-b193-46d705e75f73",
    "35e7b6f9-fd6a-4a53-b0e5-d74b91e9aee0",
    "6f32fb23-7424-4e3f-9eec-979bb2c958fd",
    "67310755-234b-482d-8f76-34ffa4ca2dfc",
]



@dataclass
class TrackItem:
    index: int
    title: str
    clip_id: str
    wav_path: Path
    duration_seconds: float
    start_seconds: float = 0.0


def format_timestamp(seconds: float) -> str:
    total_sec = round(seconds)
    mins = total_sec // 60
    secs = total_sec % 60
    return f"{mins}:{secs:02d}"



def get_audio_duration(file_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def generate_and_download_tracks(suno: SunoClient, target_track_count: int = 16) -> list[TrackItem]:

    """Ensure target_track_count tracks are generated and saved as 44.1kHz WAVs."""
    logger.info("Checking available tracks in %s...", TRACKS_DIR)

    all_clip_ids: list[str] = list(KNOWN_CLIPS)

    # 1. Submit remaining generations needed
    needed_pairs = (target_track_count - len(all_clip_ids) + 1) // 2
    if needed_pairs > 0:
        logger.info("Submitting %d generation batch requests to Suno...", needed_pairs)
        for i in range(needed_pairs):
            spec = TRACK_SPECS[(i + 2) % len(TRACK_SPECS)]
            title = f"{spec['title']} (Pt. {i+2})"
            logger.info("  Submitting: %s", title)
            try:
                ids = suno.submit_generation(
                    prompt=spec["prompt"],
                    tags=spec["tags"],
                    title=title,
                    instrumental=True,
                )
                logger.info("    Received clip IDs: %s", ids)
                all_clip_ids.extend(ids)
                time.sleep(2)
            except (SunoClientError, RuntimeError, OSError) as e:
                logger.error("    Submission error: %s", e)

    logger.info("Total clips to harvest: %d", len(all_clip_ids))

    # 2. Extract and harvest audio for each clip
    tracks: list[TrackItem] = []
    track_num = 1

    for clip_id in all_clip_ids:
        wav_file = TRACKS_DIR / f"track_{track_num:02d}_{clip_id[:8]}.wav"
        # A cached clip at a different position must not silently inherit
        # the next playlist index (the source of stale archive filenames).
        # This legacy entrypoint remains disabled; never auto-rename/reharvest.
        previous_positions = sorted(TRACKS_DIR.glob(f"*_{clip_id[:8]}.wav"))
        if any(path != wav_file for path in previous_positions):
            raise RuntimeError("legacy_harvest_displaced_cached_clip_refused")
        if wav_file.is_file() and wav_file.stat().st_size > 1000:
            dur = get_audio_duration(wav_file)
            logger.info("Track %d already harvested: %s (%.1f s)", track_num, wav_file.name, dur)
            title = f"Midnight Rhodes Track {track_num}"
            tracks.append(TrackItem(track_num, title, clip_id, wav_file, dur))
            track_num += 1
            if len(tracks) >= target_track_count:
                break
            continue

        logger.info("Harvesting audio for clip %s (track %d/%d)...", clip_id, track_num, target_track_count)
        try:
            wav_bytes, dur = suno.extract_audio_via_video(clip_id, max_wait_seconds=180.0)
            wav_file.write_bytes(wav_bytes)
            logger.info("  Extracted: %s (%.1f s, %d bytes)", wav_file.name, dur, len(wav_bytes))
            title = f"Midnight Rhodes Track {track_num}"
            tracks.append(TrackItem(track_num, title, clip_id, wav_file, dur))
            track_num += 1
        except (SunoClientError, RuntimeError, OSError) as e:
            logger.warning("  Failed to extract clip %s: %s; skipping to next", clip_id, e)

        if len(tracks) >= target_track_count:
            break

    return tracks


def concatenate_album_audio(tracks: list[TrackItem], output_wav: Path, crossfade_sec: float = 2.0) -> float:
    """Concatenate tracks using FFmpeg with smooth crossfades and two-pass loudnorm."""
    logger.info("Assembling continuous album master with %.1f s crossfades...", crossfade_sec)

    # Build FFmpeg filter_complex for seamless acrossfade
    # E.g. [0:a][1:a]acrossfade=d=2:c1=tri:c2=tri[a01]; [a01][2:a]acrossfade=...
    inputs: list[str] = []
    for t in tracks:
        inputs.extend(["-i", str(t.wav_path)])

    filter_chains: list[str] = []
    current_label = "0:a"

    current_start = 0.0
    for idx, t in enumerate(tracks):
        t.start_seconds = max(0.0, current_start)
        # Advance current_start by duration minus crossfade (except last track)
        if idx < len(tracks) - 1:
            current_start += (t.duration_seconds - crossfade_sec)
        else:
            current_start += t.duration_seconds

    for i in range(1, len(tracks)):
        next_input = f"{i}:a"
        out_label = f"m{i}" if i < len(tracks) - 1 else "album_mix"
        filter_chains.append(
            f"[{current_label}][{next_input}]acrossfade=d={crossfade_sec}:c1=tri:c2=tri[{out_label}]"
        )
        current_label = out_label

    # Studio mastering: clean sub-35Hz rumble, tame 100Hz boomy resonance, normalize to YouTube -14 LUFS standard
    filter_chains.append(
        f"[{current_label}]highpass=f=35,lowshelf=f=100:g=-3.0:width_type=q:w=0.7,loudnorm=I=-14:TP=-1.5:LRA=7[album_master]"
    )
    current_label = "album_master"
    filter_str = "; ".join(filter_chains)


    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_str,
        "-map",
        f"[{current_label}]",
        "-c:a",
        "pcm_s16le",
        "-ar",
        "44100",
        str(output_wav),
    ]

    logger.info("Running FFmpeg audio assembly...")
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        logger.error("FFmpeg concat failed: %s", res.stderr[-1000:])
        raise RuntimeError(f"FFmpeg audio assembly failed: {res.stderr[-300:]}")

    total_dur = get_audio_duration(output_wav)
    logger.info("Continuous album audio rendered: %s (Total duration: %.1f s / %.1f mins)",
                output_wav.name, total_dur, total_dur / 60.0)
    return total_dur


def render_album_video(
    audio_wav: Path,
    artwork_img: Path,
    output_mp4: Path,
    tracks: list[TrackItem] | None = None,
) -> None:
    """Render 1080p Full HD video master combining artwork, continuous audio, and embedded chapter markers."""
    logger.info("Rendering 1080p video master: %s...", output_mp4.name)
    expected_duration = get_audio_duration(audio_wav)
    if not 0 < expected_duration <= 7200:
        raise RuntimeError("legacy_render_source_audio_duration_invalid")

    inputs: list[str] = [
        "-loop",
        "1",
        "-framerate",
        "2",
        "-i",
        str(artwork_img),
        "-i",
        str(audio_wav),
    ]

    metadata_args: list[str] = []
    if tracks:
        chapters_file = output_mp4.parent / "chapters_meta.txt"
        lines = [";FFMETADATA1", "title=Midnight Rain & Rhodes"]
        for i, t in enumerate(tracks):
            start_ms = int(t.start_seconds * 1000)
            if i < len(tracks) - 1:
                end_ms = int(tracks[i + 1].start_seconds * 1000)
            else:
                end_ms = int((t.start_seconds + t.duration_seconds) * 1000)
            lines.extend([
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start_ms}",
                f"END={end_ms}",
                f"title={t.title}",
            ])
        chapters_file.write_text("\n".join(lines), encoding="utf-8")
        inputs.extend(["-i", str(chapters_file)])
        metadata_args = ["-map_metadata", "2"]

    # 1920x1080 static background image looping to full audio length with faststart AAC audio
    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-vf",
        "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "stillimage",
        "-crf",
        "22",
        "-c:a",
        "aac",
        "-b:a",
        "256k",
        "-ar",
        "48000",
        *metadata_args,
        "-t",
        f"{expected_duration:.6f}",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_mp4),
    ]


    logger.info("Starting FFmpeg 1080p render (this may take 2-4 minutes)...")
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        logger.error("Video render failed: %s", res.stderr[-1000:])
        raise RuntimeError(f"Video render failed: {res.stderr[-300:]}")

    # This archived helper may be invoked directly even though main() is disabled.
    # Never hand off its render merely because FFmpeg exited successfully.
    from scripts.probe_local_mp4_audio import require_local_render_audio_sync

    try:
        require_local_render_audio_sync(
            output_mp4, expected_audio_seconds=expected_duration,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError("legacy_video_render_local_audio_sync_unverified") from exc

    size_mb = output_mp4.stat().st_size / (1024 * 1024)
    logger.info("1080p Album video master rendered successfully: %.1f MB", size_mb)


def publish_album_to_youtube(
    video_path: Path,
    tracks: list[TrackItem],
    total_duration_sec: float,
) -> str:
    """Legacy direct uploader is disabled; publish via the authorized P7 engine.

    This script has no immutable C6 package, rights gate, authorization receipt,
    or live chapter verification. OAuth credentials alone are not upload consent.
    """
    raise RuntimeError(
        "Direct album upload blocked: use the P5-cleared C6 package and "
        "P7 publishing engine with ExternalWriteGuard and verified metadata"
    )


def main() -> None:
    # This archived one-off path bypasses P5 clearance, approved budgets and
    # P7's write guard. Stop BEFORE calling the paid generation provider.
    raise RuntimeError(
        "Legacy production script disabled before paid generation: "
        "use the guarded P3->P4->P5->P6->P7 pipeline"
    )
    suno = SunoClient()
    if not suno.is_configured:
        logger.error("Suno client is not configured")
        sys.exit(1)

    logger.info("=== STEP 1: Harvesting 16 Tracks via Suno Zero-Quota Video Demux ===")
    tracks = generate_and_download_tracks(suno, target_track_count=16)
    logger.info("Successfully harvested %d tracks!", len(tracks))


    if len(tracks) < 6:
        logger.error("Not enough tracks harvested to assemble album (found %d)", len(tracks))
        sys.exit(1)

    # Assign nice atmospheric titles matching the concept
    for idx, t in enumerate(tracks):
        t.title = TRACK_TITLES[idx] if idx < len(TRACK_TITLES) else f"Midnight Rhodes Track {idx + 1}"


    logger.info("=== STEP 2: Assembling Seamless Continuous Audio Master ===")
    album_audio = OUTPUT_DIR / "album_master_continuous.wav"
    total_duration = concatenate_album_audio(tracks, album_audio, crossfade_sec=2.5)

    logger.info("=== STEP 3: Rendering 1080p Video Master ===")
    artwork_img = Path("output_media/suno_artwork.jpeg")
    album_video = OUTPUT_DIR / "midnight_rain_and_rhodes_album_1080p.mp4"
    render_album_video(album_audio, artwork_img, album_video, tracks=tracks)



    logger.info("=== STEP 4: Publishing to Rhodes & Rain Records ===")
    video_id = publish_album_to_youtube(album_video, tracks, total_duration)

    summary_file = OUTPUT_DIR / "publication_summary.json"
    summary = {
        "title": ALBUM_TITLE,
        "video_id": video_id,
        "watch_url": f"https://youtu.be/{video_id}",
        "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
        "duration_seconds": total_duration,
        "duration_minutes": total_duration / 60.0,
        "track_count": len(tracks),
        "tracks": [
            {
                "index": t.index,
                "title": t.title,
                "clip_id": t.clip_id,
                "duration_seconds": t.duration_seconds,
                "start_seconds": t.start_seconds,
                "timestamp": format_timestamp(t.start_seconds),
            }
            for t in tracks
        ],
    }
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info("Publication summary saved to %s", summary_file)
    logger.info("ALBUM PUBLISHING COMPLETE! Video ID: %s", video_id)


if __name__ == "__main__":
    main()
