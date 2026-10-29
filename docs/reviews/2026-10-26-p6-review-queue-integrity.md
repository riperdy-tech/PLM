# P6 archived listening queue integrity recheck (2026-10-26)

**State: real source/selection replay, score recheck and whole-project non-integration regression passed.** This is a read-only operator diagnostic, not a live gate or source-rights approval.

## Why this work matters

The six cross-release review candidates generated from 8 local development P6 albums are recorded in editable JSON. The label evaluator checks syntax and the score fields, but an edited queue file cannot itself demonstrate that its identifiers still match saved source records, nor that the numbers and selected strata came from original audio.

## Added checks

- `services/media_factory/audio/texture_review_integrity.py` binds the saved unlabelled pair report to the selected P6 package/song/SHA256 metadata and strictly validates report provenance flags, count limits, schema, exact pair order, numerical bounds and source identifiers. Labels or external rights are never inferred.
- `scripts/verify_p6_texture_review_queue.py` requires the queue JSON and 2..8 explicit package IDs. It loads original playlist/track metadata using `REPEATABLE READ, READ ONLY`; reads the saved report with a 300 KB hard cap, duplicate-key rejection and nonfinite number rejection. The verifier checks the current source identities, fetches SHA256-verified original WAVs, and recomputes each pair's baseline, offset-only and playback-rate-plus-offset scores in both directions (allowed absolute score difference `1e-8`).
- To verify that the candidates themselves were not substituted, the verifier also re-extracts P6 four-window textures from the selected first/last (or first/middle/last) source tracks, replays the deterministic relative high/middle/low review-pair selection, then checks pair order, band and prescreen score against the saved file. This remains a relative, nonrandom selection and makes **no statistical calibration claim**.
- The checker reports a SHA256 of the exact saved JSON file bytes for audit linkage. That hash identifies the file; it is **not** a digital signature, and the operator still needs an independent trust process for the source and listening reports.
- Both score preparation and verification use the same `score_pair` function from the media service, avoiding script-invocation path differences. No original audio bytes are copied to local files or written to MinIO.

## Safety boundaries

All reads use explicit selected packages and checksum-verified artifact fetches; `store.put`, DB writes, fingerprint backfill, production rule updates, external APIs and publication are not invoked. The verifier fails closed on missing/changeable archived source identifiers, mismatched scores, altered candidate selection, invalid score reports, missing objects and decode errors.

An executed unit-test run covered invalid/tampered reports, source checksum drift, duplicate review cases, incomplete/altered selection and a read-only, mocked CLI path. **Focused review-queue and integrity tests: 28 passed** before final recheck. No humans were asked to listen, no reviewer labels were manufactured, and neither rights safety nor real-world false-positive rate has been determined.

Actual connected local development recheck completed with exit code 0: selected 8 P6 packages, 16 original WAVs remeasured for candidate selection, 6 cross-release pair scores recomputed, 28 SHA256-verified object reads in total, and all selected pair IDs/strata/score values reproduced within tolerance. Saved input JSON SHA256: `20a3a30691d4e7ec00ae59ed1e13ad0f8ce6e62ba8d756e0e644785d0efdd0f4`. Verified checks for the current implementation: focused queue/integrity unit suite **28 passed**, repository-wide Ruff **passed**, mypy **461 source files passed**, and the full non-integration regression **2,688 passed, 1 skipped, 80 integration deselected, 15 warnings** (exit 0). Integration and live provider checks were not run. The archive has 168 source tracks; this specific review queue still covers 16 sampled originals and 6 cross-release comparisons, not the entire catalog.
