# P6 actual archived audio: bounded full cross-release texture audit (2026-10-25)

**State: completed READ-ONLY analysis for the eight selected local development albums.**
This is not a license, copyright, melody-similarity, human-listening or
publication clearance. It does not modify the stored P6 album evidence.

## Procedure and limits

The existing diagnostic `scripts/diagnose_p6_album_texture_samples.py` now
supports the **explicit** `--all-tracks` flag. The default remains a
two-track-per-album sparse sample. Full mode requires 2..8 caller-specified
packages, at most 30 archived tracks in any one package, and at most **200**
tracks across the batch; larger sets fail closed. It never queries an
unspecified catalog. The source playlist/header, rendered tracklist, track
order, source artifact, song ID and checksum metadata are checked for all
selected tracks in a `REPEATABLE READ, READ ONLY` SQL transaction. The
transaction closes before sequential MinIO reads. For each chosen track the
stored source bytes are **SHA256-verified**, the current
`p6-features-v2` extractor measures the four-window signature, and only
the 192 floats per track are retained for scoring.

The analysis compares every pair of tracks belonging to **different
release-manifest IDs**, using the same four-window spectral agreement
formula and `album-texture-v1` near-copy threshold (0.998). It does not
compare tracks inside one release; those were previously measured by
the separate eight-album dry-run backfill which checks within-album
near-copies. Full results remain in memory/stdout, not in PostgreSQL, MinIO,
or a persisted fingerprint catalog.

```powershell
.\.venv\Scripts\python.exe scripts\diagnose_p6_album_texture_samples.py `
  -p mpkg_08b548e82a6b50b4 -p mpkg_5b742d7dcd895bd1 `
  -p mpkg_60f52efca73658e3 -p mpkg_b93288d81ea15141 `
  -p mpkg_c2f80bc2d2db5143 -p mpkg_ebc16c8c3c1754c1 `
  -p mpkg_eca6dffca769529b -p mpkg_ed0acf37058e5827 `
  --all-tracks
```

## Measured output on the local development environment

| Measure | Actual result |
| --- | ---: |
| Explicit albums | 8 (`neon_seoul`) |
| Tracks in the selected archives | **168** |
| Source audio SHA256-verified and measured | **168** |
| Unsampled within these selected archives | **0** |
| Cross-release pair comparisons | **12,332** |
| Maximum spectral score | **0.7296809346608583** |
| Comparisons at/above 0.998 threshold | **0** |
| Exact same source SHA256 across compared pairs | **0** |
| Persisted textures, source/DB/object writes | **None** |

The highest five scores (descending) were approximately 0.729681,
0.724789, 0.704168, 0.700063, and 0.695739. All selected packages
are the eight archived READY/PUBLISHED `neon_seoul` packages previously
found in the developer DB; this is **not a complete inventory of other
brands, environments or unpublished source recordings**.

Pair count check: `C(168,2) - 4*C(19,2) - 4*C(23,2) = 12,332`.

## Critical limits

- The recording's four spectral snapshots are not a melody matcher,
  structural comparison, voice/acoustic fingerprint service or complete
  semantic similarity model. Gain/pitch, arrangement, time-stretch,
  mastered alternates and near-melodies can evade the 0.998 threshold.
- A human-labelled true-positive/true-negative corpus has not been
  used to calibrate sensitivity and specificity. Zero matches is
  **not** proof of musical originality or commercial clearance.
- The metadata is a consistent read-only SQL snapshot, but content reads
  take place afterward. SHA256 verifies selected object bytes, not an
  immutable cross-system transaction in the face of concurrent DB edits.
- MinIO reads and local computation can be expensive. This is a manually
  selected bounded operator diagnostic, not an automatic release gate or
  a scheduled production scan. No irreversible step was authorized.
- Existing eight-album `backfill-album-textures` default dry runs verified
  and measured all 168 sources separately, but `--apply` has **not** been
  run: persisted historical texture evidence remains missing.

## What remains

Human-reviewed cross-track listening and melody/arrangement similarity,
provider rights and original-source lineage, calibration against positive
examples, explicit archival evidence persistence approval, and the
unresolved `0038` migration divergence before any approved schema work.
