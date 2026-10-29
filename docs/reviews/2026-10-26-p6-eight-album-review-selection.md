# P6 bounded eight-album human-review queue (2026-10-26)

**Status: read-only real-source selection and rate/offset scoring completed; no human labels.** This is not a copyright, rights, originality, listening-quality or production-gate decision.

## Purpose and implementation

After the real-source transformation experiment and unlabelled two-track audit, the missing input to empirical classifier calibration was a defensible listening-review set. The previous single unlabelled pair could not measure false positives. This change adds:

- `services/media_factory/audio/texture_review_selection.py`: a deterministic, strictly bounded review-candidate sampler using measured 192-bin P6 four-window signatures. It rejects missing/invalid texture, repeated identities and exact reused SHA256 inputs (these should be investigated separately); selects only between distinct release IDs. Within the **selected source subset**, candidate spectral scores are sorted into relative high/middle/low thirds. It cycles these strata and preferentially avoids reused source checksums and release pairings. A requested maximum is capped to the available number of cross-release pairs; no truth labels or random-sampling inference are produced.
- `scripts/prepare_p6_texture_review_queue.py`: requires **2–8 explicitly named eligible P6 packages**, samples the first+last (or first/middle/last) source tracks per package, loads matching playlist/source metadata under `REPEATABLE READ, READ ONLY`, SHA256-verifies each original audio object with the existing MinIO read interface, and measures textures. It then scores each chosen pair in both directions with the existing fixed 7-shift and 21 rate+shift diagnostic. These scores are *maxima across directions* for each method, not adjudications. Limits: 2–3 sampled tracks per album, at most 24 measured tracks, at most 12 chosen pairs. Each selected original is fetched with checksum verification again for alignment and is kept in memory only while needed.
- `tests/unit/test_media_texture_review_selection.py`: deterministic selection, stratum coverage, checksum/source refusals, bounded input, score monotonicity, read-only CLI behavior and compatibility with the label evaluator.

The JSON output's `unlabelled_pair_score_report` can be read by `scripts/evaluate_p6_texture_labels.py --report <report.json>`. Its `review_queue` gives P6 package ID, source song ID, original SHA256, and relative prescreen band for a local listening reviewer to locate the original. **The tool does not provide playable links, attest review identities, or produce any new WAV/media files.** The score files are ordinary operator-managed JSON without a cryptographic signature; changing them later is not validated by the evaluator.

## Actual local development run, all eight eligible neon_seoul albums

The review preparer ran successfully on the eight explicit packages used in the prior archive audit. The source metadata passed the read-only validation and each measured WAV passed `fetch_verified` SHA256 verification.

| Observation | Actual result |
| --- | ---: |
| Selected eligible P6 albums | **8** |
| Source tracks in those albums | **168** |
| Original tracks measured and SHA256 verified (first and last per album) | **16** |
| All unmeasured source tracks in this audit | **152** |
| Selected cross-album review pairs | **6** |
| Relative high / middle / low spectral pairs | **2 / 2 / 2** |
| Distinct originals in the six pairs | **12** |
| Distinct archived albums represented in the six pairs | **8** |
| Largest fixed-position score among these six pairs | **0.6454168320** |
| Largest best-rate-plus-offset score among these six pairs | **0.6531942745** |
| Human labels supplied | **0** |

The six pair scores and exact source identities are saved to `docs/reviews/2026-10-26-p6-eight-album-review-queue.json`. A *pending-input* CSV at `docs/reviews/2026-10-26-p6-eight-album-review-input-pending.csv` repeats each selected source pair **twice** with blank `reviewer_id,relationship,evidence_ref` for future separate human reviewers. Blank entries are **not** valid reviews; they deliberately fail validation until supplied, and neither reviewer identity nor the evidence reference is fabricated. The existing human-label validator needs at least two distinct reviewer pseudonyms with unanimous decisive labels on a pair to count it.

The actual evaluator run on the saved JSON without human CSV succeeded with **6 source pairs, 6 unadjudicated, 0 human-reviewed**. True-positive and false-positive sample fractions remained JSON `null` for all methods; the operational threshold is not ready. No one may infer that any of the six independent source checksums proves musically distinct recordings or that six nonmatches certify the original 168 tracks.

## Limits and next evidence

This is a deliberate **stratified, nonrandom candidate selection**: it emphasizes distinct source pairs and varied spectral scores, not a population-representative sample. The categories describe relative four-window scores, not human-judged sonic similarity. With 16/168 recordings tested using the expanded method and no human labels, recall, specificity, false-positive cost, independent reviewer agreement and rights remain unverified. Six pairs do not suffice for operational threshold calibration. Human listeners need secure access to the existing original media through approved local workflows, and reviewer identities/evidence must be validated outside the code. A future expanded set should separately include known derivatives and independent difficult negatives.

This activity did not modify existing P6 features, the active 0.998 hold, archived fingerprints, database contents or MinIO objects, and did not publish or commit. The two-branch Alembic `0038` conflict and controlled historical fingerprint backfill remain separately gated.

**Checks:** six new focused unit tests passed; repository-wide Ruff and mypy (**460 source files**) passed. Full non-integration regression **2,666 passed, 1 skipped, 80 deselected, 15 warnings** (exit code 0). Integration, full 168-source rate+offset comparisons, independent human listening, provider and production checks were not run.
