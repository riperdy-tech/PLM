# P6 time-offset alignment diagnostic, not a release gate (2026-10-25)

**State: validated on two SHA256-verified real source recordings; not deployed.**

The existing four-window `p6-features-v2` signature missed two simple
copies in the prior read-only robustness probe: inserting 0.5 seconds
of initial silence and truncating an equal amount from the end resulted
in stationary scores of 0.392217 and 0.364477, below the production
0.998 near-duplicate hold threshold. This was a known same-source
relationship, not an inferred rights or originality decision.

## Diagnostic construction

Added `services/media_factory/audio/texture_alignment.py`, a standalone
**diagnostic-only** function `offset_aligned_texture()`. It reuses the
existing production feature extractor's 2048-sample FFT/48-band,
four-window texture calculation and mono/downsample preprocessing without
changing the production extractor or album/cross-album gates. It tests
exactly seven bounded **candidate waveform shifts** from -0.75 through
+0.75 seconds in 0.25-second steps, including zero. The candidate is
shifted in transient memory with zero padding at its edge; no media files
are created. A spectral window with no valid features is excluded;
the result discloses how many shifts produced a valid score. Zero shift
is mandatory. Decoding is limited to 64 MiB input per side, at most
12 million decoded frames, and mono/stereo channels. Incompatible
analysis sample rates and unmeasurable zero-shift signatures refuse.

The existing manual diagnostic
`scripts/diagnose_p6_texture_robustness.py` now includes both the
stationary texture score and the **maximum offset-search score** and
selected shift for each transformed variant. It still accepts only two
explicit, eligible archived P6 packages, reads their metadata inside a
read-only PostgreSQL transaction, and fetches sources from MinIO only
with a matching content-addressed SHA256. There are no DB/MinIO writes,
provider calls, external uploads or changed production decisions.

```powershell
.\.venv\Scripts\python.exe scripts\diagnose_p6_texture_robustness.py `
  -p mpkg_08b548e82a6b50b4 -p mpkg_b93288d81ea15141
```

## Observations from two distinct archived originals

| Test variant derived from the same original | Current fixed-position score A / B | Best bounded offset score A / B | Winning shift |
| --- | --- | --- | --- |
| 65% gain | 1.000000 / 1.000000 | 1.000000 / 1.000000 | 0.0s |
| 5% faster, with pitch change | 0.828515 / 0.885356 | 0.828515 / 0.885356 | 0.0s |
| 0.5s initial silence, equal tail truncation | **0.392217 / 0.364477** | **0.999998073 / 0.999999740** | +0.5s |

All seven offsets were valid for these two original WAV recordings.
**Time alignment found the two known offset copies** at scores above
0.998; **it did not solve speed/pitch changes**.

The 0.998 comparison here is only a reference to the old threshold,
not a calibrated threshold for the new offset-search method. Searching
several offsets increases opportunities for a high score on an unrelated
recording; true-positive and true-negative rates cannot be estimated
from these two known positives and one unrelated synthetic test clip.
There has been **no human-labelled reference dataset**, no sensitivity/
specificity or review-load evaluation, and no change to the deployed
`album-texture-v1` gate.

## Validation and remaining work

- Test fixtures check unshifted-score equivalence to the existing P6
  extractor, deterministic shifted copies, gain invariance, finite offset
  and sample-rate bounds, nondecodable/too-short/silent audio refusal,
  and one synthetic distinct-key counterexample. A skipped offset
  is not counted as a successful measurement.
- A real read-only two-package diagnostic returned successfully,
  including actual aligned scores above; no modifications were made
  to the original audio archives or stored fingerprint evidence.
- To consider operational deployment, first label diverse positive
  transformations and truly independent negative tracks by human
  review; calculate confusion matrices, false-positive cost and
  review workload. Assess pitch- and speed-invariant fingerprints
  independently, without reusing the existing 0.998 limit by assumption.
- The unrelated Alembic 0038 divergence, archived signature persistence,
  commercial rights provenance and publishing approvals remain open.
