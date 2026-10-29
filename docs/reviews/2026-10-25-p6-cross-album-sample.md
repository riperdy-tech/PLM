# P6 real-source cross-album **sample** texture diagnosis — 2026-10-25

**Classification: read-only sampled diagnostic, NOT a release/originality/rights gate.**

The connected local development DB has eight eligible `neon_seoul` P6
packages containing 168 source tracks. Prior read-only work verified all
168 original files' bytes and SHA256 values by complete texture-backfill dry
runs; no fingerprint evidence has been persisted.

## Diagnostic

`scripts/diagnose_p6_album_texture_samples.py` accepts **2 to 8 explicitly
named** READY/PUBLISHED package IDs and `--samples-per-album 1|2|3`
(default 2). There is no brand-wide discovery, automatic `--apply`, external
provider call or publishing. The script:

1. Reads P6 package, playlist and **every track's** stored identity in a
   `REPEATABLE READ, READ ONLY` metadata transaction. Refuses a missing,
   ineligible, cross-brand or identity/position/source mismatch. All selected
   packages must span at least two release manifests.
2. Selects only deterministic source positions (first; first/last;
   first/middle/last). Fetches selected originals from MinIO with the
   persisted source SHA256 verified, one track at a time.
3. Measures `p6-features-v2` four-window spectral signatures, refusing
   absent or malformed textures, and compares **different releases only**.
4. Returns the actual sample count and unsampled count, scored pairs,
   top similarities, exact-SHA256 reuse count and cases reaching the
   `album-texture-v1` threshold **0.998**. No spectral fingerprints are
   written to PostgreSQL or MinIO.

Invocation used (eight explicit package IDs):

```powershell
.\.venv\Scripts\python.exe scripts\diagnose_p6_album_texture_samples.py `
    -p mpkg_08b548e82a6b50b4 -p mpkg_5b742d7dcd895bd1 `
    -p mpkg_60f52efca73658e3 -p mpkg_b93288d81ea15141 `
    -p mpkg_c2f80bc2d2db5143 -p mpkg_ebc16c8c3c1754c1 `
    -p mpkg_eca6dffca769529b -p mpkg_ed0acf37058e5827 `
    --samples-per-album 2
```

## Actual local measured results

| Measure | Eight-album sample |
| --- | ---: |
| Packages | 8 (all `neon_seoul`) |
| Archived tracks represented | 168 |
| SHA256-verified audio samples actually measured | **16** |
| Tracks not measured by this sample diagnostic | **152** |
| Valid cross-release sample pairs | **112** |
| Max four-window spectral similarity | **0.6454168320** |
| Pairs at/above conservative 0.998 hold threshold | **0** |
| Pairs with identical original source SHA256 | **0** |

Five highest-scoring pairs: **0.6454, 0.6147, 0.6106, 0.5848, 0.5841**.
A separate two-album subset (38 archived tracks, four measured samples,
four compared pairs) had a maximum **0.5002244816**, also zero held pairs.

The samples are deliberately sparse. The score quantifies **spectral
texture** agreement at four moments only; it does not assess melody,
arrangement, chord progressions, copyright similarity, lyrical identity or
perceptual similarity across full tracks. Zero sample threshold hits is
**not** evidence that all 168 tracks would pass, nor proof of originality.
The 0.998 threshold remains uncalibrated against human listening labels.

The underlying metadata read is a transaction-level snapshot, but MinIO
audio retrieval happens *after* closing that snapshot; the content-addressed
SHA256 check detects changed bytes, not all concurrent metadata changes.
No user listening, rights, real audience response, production migration,
P6 `--apply` or outside-provider publishing was performed.

## Next bounded checks

- Calibrate spectral thresholds on human-labelled similar/non-similar
  recordings, including pitch changes, speed shifts, stems, silence and
  remixes. Keep the current fail-closed release gate separate from this
  sample diagnostic.
- **Completed later on 2026-10-25:** the explicit `--all-tracks` bounded,
  read-only path measured all 168 original tracks and scored 12,332
  cross-release pairs without persisting signatures. See
  `docs/reviews/2026-10-25-p6-cross-album-full.md`. This earlier
  16-track report remains sample-only evidence.
- Before persisting historic fingerprints, obtain operator approval and
  recheck the independent source/rights provenance and existing
  `0038` Alembic schema conflict.
