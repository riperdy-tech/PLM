# P6 texture near-copy sensitivity: same recording transformed in memory (2026-10-25)

**Status: confirmed detection gap for two real, SHA256-verified development recordings.**
This is a read-only, reproducible diagnostic and NOT a rights clearance, originality
assessment, or approval to change the active near-copy gate.

## Why this was tested

On 2026-10-25, the P6 read-only full-archive audit measured all 168 tracks in
eight `neon_seoul` albums and compared 12,332 cross-release pairs. No pair
reached the current `album-texture-v1` near-copy threshold **0.998**, and
the largest four-window spectral score was 0.7296809346608583. That does
**not** establish recall (the ability to find genuine copies). The present
`p6-features-v2` signature uses only four normalised 48-band spectra; it
does not align waveforms, transcribe melody, or correct for changed playback
speed/pitch or the content position within a recording.

## Explicit real-original sensitivity experiment

Added `scripts/diagnose_p6_texture_robustness.py`. It reads P6 metadata in a
`REPEATABLE READ, READ ONLY` transaction for **exactly two explicitly named
eligible** packages, selects only each package's first source track, fetches
its original MinIO object with actual SHA256 verification, and synthesises
exactly three variants in memory. The source is never replaced or modified:

1. `gain_0.65`: rescale the samples to 65% amplitude and re-encode WAV PCM16.
2. `speed_1.05_pitch_shifted`: replay samples 5% faster via interpolation
   at the same declared sampling frequency. This changes **both speed and
   pitch**; it is *not* pitch-preserving time stretching.
3. `time_offset_0.5s`: insert 0.5 seconds of leading zeros, keeping the
   same duration by truncating the same number of final samples. Most
   content remains the same, but its four snapshot sampling positions shift.

All variants use the existing production `measure_features` path and exactly
the current cosine-like four-window score. Audio is bounded (64 MiB input,
12 million decoded frames, at most stereo), processed one source at a time.
The baseline feature extraction is reused between the three variants.
No database, MinIO object, WAV file or album-fingerprint evidence is written.

```powershell
.\.venv\Scripts\python.exe scripts\diagnose_p6_texture_robustness.py `
  -p mpkg_08b548e82a6b50b4 -p mpkg_b93288d81ea15141
```

## Scores actually obtained from those two development originals

| Transformation (same original source) | `mpkg_08b548…` | `mpkg_b93288…` |
| --- | ---: | ---: |
| Gain 0.65 | 0.9999999999561666 | 0.9999999999425963 |
| 5% faster playback with changed pitch | **0.8285148409526775** | **0.8853562637633020** |
| 0.5 s leading silence / matching tail removal | **0.39221709062775245** | **0.3644772691077813** |

The source WAV byte checksums differ after each transformation, as expected.
All six transformations are derived from the same two recordings: this
experiment **already knows** the relationship independently of the score.
Under the production threshold 0.998, the four transformed copies caused
by faster playback and offset are **missed**; the two gain changes still
reach the threshold. This is a verified false-negative **in the tested
transformation scenario**; it does not estimate population-level recall.

## Consequences and next evidence gates

- Treat **zero detections among 12,332 cross-release source pairs** as a
  narrow spectral screening result, **not** a certificate of no sampled
  near-copies after simple re-timing, recoding, offsetting or pitch changes.
- Do not silently lower `0.998` to force these two recordings to match:
  that could create unmeasured false positives among genuinely distinct
  songs, and may create costly blocks. The existing conservative release
  hold remains unchanged.
- A later **diagnostic-only** +/-0.75s alignment experiment recovered
  both known 0.5-second-offset copies with scores near 1.0 but did not
  recover speed/pitch changes. See
  `docs/reviews/2026-10-25-p6-time-offset-alignment.md`.
- Before changing production gates, collect human-labelled positive and
  negative *real-song* cases across appropriate transforms. Evaluate a
  separate alignment-aware/perceptual fingerprint or melody/chroma
  similarity method, and report confusion matrices and review load.
- Human listening and rights/provenance checks remain hard stops.
  Synthetic generated variants are not licensed third-party comparison
  recordings and cannot establish copyright validity.
- The local Alembic `0038` divergence, historical texture persistence
  approval and actual publishing permissions remain independent open items.

**Validation:** synthetic fixture tests verify deterministic transformations,
gain invariance, failure on invalid/missing textures, size/length bounds and
four baseline/variant measurements (one original + three variants). The
two-recording production-object sensitivity invocation succeeded (exit 0).
No paid provider call, audio publication, database update or MinIO write.
