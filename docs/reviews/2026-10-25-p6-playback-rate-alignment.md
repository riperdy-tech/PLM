# P6 bounded playback-rate plus offset diagnostic (2026-10-25)

**State: read-only two-source experiment and full non-integration regression passed.** This work is **diagnostic only** and does not change any production near-copy release gate or the 0.998 operating threshold.

## Confirmed reason for the change

The earlier checksum-verified two-source experiment showed that the existing four-position spectral signature missed known same-source variants played 5% faster with pitch changed (fixed scores 0.828515 and 0.885356). Offset alignment recovered 0.5 s silence insertions but did not recover the faster playback. The old measurements are documented in `2026-10-25-p6-texture-robustness.md` and `2026-10-25-p6-time-offset-alignment.md`; they are *not* new measurements.

## New diagnostic logic

- `audio/texture_alignment.py:offset_aligned_texture` accepts optional `playback_rates`, defaulting to `(1.0,)` to preserve the established seven-offset-only diagnostic. An opt-in grid of `(0.95, 1.0, 1.05)` considers candidates that were played 5% slower or faster **with corresponding pitch changes**.
- For each rate, the decoded and bounded analysis waveform is linearly interpolated back to the original time axis; the same seven shifts (-0.75..+0.75 s) and existing four-window `p6-features-v2` signature are then evaluated. This is not pitch-independent time stretching, chroma or melody analysis.
- The normal-speed, zero-shift baseline remains required. The result reports both winning shift and playback-rate hypothesis and the number of valid combinations (up to 21). Invalid, duplicate, nonfinite or out-of-grid rates fail closed.
- The read-only `scripts/diagnose_p6_texture_robustness.py` retains `offset_search_diagnostic` and adds a separate `rate_offset_search_diagnostic` result. Existing DB metadata remains read-only, object fetches remain checksum-verified, and variants and compensation are memory-only. No uploads, provider calls or fingerprint writes were added.

## Actual results on two SHA256-verified archived originals

The read-only CLI completed with exit code 0 using the same explicit two archived package IDs as the preceding offset-only study. It read P6 metadata in a read-only transaction, checksum-verified each fetched original object, and generated the altered candidates and compensated waveforms in memory.

| Known same-source transformation | Original fixed score, source A / B | Best offset + playback-rate score, source A / B | Selected playback rate and shift |
| --- | --- | --- | --- |
| 5% faster playback, pitch also increased | **0.828514841 / 0.885356264** | **0.999973303 / 0.999982548** | 1.05, 0.0 s (both) |
| 0.5 s leading silence | 0.392217091 / 0.364477269 | 0.999998073 / 0.999999740 | 1.0, +0.5 s (both) |
| 65% gain | ~1.000000 / ~1.000000 | ~1.000000 / ~1.000000 | 1.0, 0.0 s (both) |

All **21 rate/shift combinations** were measurable per transformed candidate. These are known-positive transformations of two source recordings; their relationship was determined independently when the variant was created. They show recovery of these particular copies, **not sensitivity or specificity for real-world independent recordings**. Existing threshold 0.998 is shown only for comparison and is not calibrated for the rate/offset search.

## Validation and remaining limitations

- New alignment unit tests: **25 passed**, exit code 0.
- Robustness/CLI unit tests: **9 passed**, exit code 0.
- Ruff on all repository modules: **all checks passed**.
- mypy across `packages services`: **458 source files passed**.
- Nexus Python syntax validation: **four related Python files valid**.
- Full non-integration regression: **2,634 passed, 1 skipped, 80 deselected, 15 warnings; exit code 0**. Integration and live production tests were not run.

Before any operational integration, quantify recall and false-positive rates on independently human-labelled real positive and negative examples. A maximum over 21 hypotheses may inflate unrelated-track scores. Obtain separate rights and listening reviews; do not reuse the existing threshold without calibration.

Migration branch reconciliation, stored historical fingerprint backfill and external publication continue to require their own approvals and are outside this read-only diagnostic.
