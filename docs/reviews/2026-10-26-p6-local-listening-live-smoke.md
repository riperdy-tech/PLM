# P6 read-only live archive listening-route smoke (2026-10-26)

**State: real 8-album development archive smoke PASSED.** This verifies technical accessibility of a score-blind local review page and original WAV routes, not human listening accuracy, reviewer identity, copyright ownership or release permission.

## Reason and change

The existing `scripts/serve_p6_texture_listening.py` had working synthetic localhost tests, but the six selected review pairs had not been exercised through its HTTP routes using the actual archived source WAVs. To close that evidence gap without exposing or keeping a listening server running, the CLI now accepts `--smoke-only`.

The one-shot mode uses the **same** source binding, explicitly selected P6 IDs, repeatable-read/READ ONLY metadata query, MinIO read store, randomized session token and loopback-bound HTTP handler as the operator's normal local review screen. It verifies the HTML contains two audio controls for each queue pair. It then requests the first 16 WAV bytes from the A and B route for every pair using HTTP `Range: bytes=0-15`. Each response can succeed only after `fetch_verified` has retrieved the **complete** corresponding original recording and verified its SHA256. The smoke mode requires a 206 response, the expected bounded 16-byte body, WAV magic, Content-Range and no-store caching headers. It never POSTs a reviewer label or exports a CSV.

The temporary HTTP server runs in a local thread solely for the requests, is always shut down in a `finally` block, and never prints the private token. Requests do not write media files to disk. A test also verifies shutdown after one synthetic review pair. The mode makes no claim about audible playback by a human or browser-specific codec acceptance.

## Actual archived-source evidence

Input: `docs/reviews/2026-10-26-p6-eight-album-review-queue.json`, verified raw file SHA256 `20a3a30691d4e7ec00ae59ed1e13ad0f8ce6e62ba8d756e0e644785d0efdd0f4`, same 8 explicit P6 package IDs as the earlier integrity replay, `--samples-per-album 2`.

The real connected local development command returned exit code **0** and the structured output:

```json
{
  "status": "local_audio_smoke_passed",
  "selected_pair_count": 6,
  "sha256_verified_audio_http_responses": 12,
  "reviews_submitted": 0,
  "media_files_saved": 0,
  "server_persisted": false,
  "human_rights_or_accuracy_verified": false
}
```

No source URI, private URL token or WAV sample was saved to this report. The source media were downloaded into memory for SHA256 validation for each request; only 16 bytes per original were returned to the same-process loopback client. This is **12 successful audio route responses, not 12 independently heard songs**. These are the review candidates from 16 sampled tracks out of 168 archived tracks, not an entire catalog readiness certification.

## Verification / remaining limitations

- New smoke-only unit test added to `tests/unit/test_media_texture_listening_review.py`.
- Focused listening suite: **14 tests passed**, exit 0.
- Repository-wide Ruff: **passed**.
- mypy: **462 source files passed**.
- Full non-integration pytest regression: **2,702 passed, 1 skipped, 80 deselected, 15 warnings** (exit code 0).
- Integration, actual interactive browser listening, real separate human judgments and provider/production tests have not been run.

The existing operating 0.998 matching threshold and stored P6 fingerprints remain untouched. Rights review, real human agreement, calibration of false-positive/missed-copy rates, Alembic 0038 branch reconciliation and separately authorized fingerprint backfill remain outstanding. No DB, MinIO or external publishing writes, and no Git commit were made by this smoke.
