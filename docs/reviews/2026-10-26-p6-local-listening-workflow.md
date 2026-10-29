# P6 local-only listening review workflow (2026-10-26)

**Status: implemented and synthetic loopback-HTTP tested; no real human has listened or submitted a verified judgment.** Read-only diagnostic support only; no release approval, calibrated detector decision, rights assurance or deployment integration.

## What was missing

The earlier eight-album queue identified six cross-release listening pairs and repeatedly verified their source WAV checksums, selection strata and recorded fixed/offset/rate scores. However, a person could not easily play the original A/B recordings without locating the MinIO objects. The existing label evaluator also accepted only one CSV file at a time, making independent reviews awkward.

## New local-only workflow

- `services/media_factory/audio/texture_listening_review.py` renders an **A/B, score-blinded** HTML page and validates a reviewer-pseudonym form. It omits album/song identities, hashes, similarity scores and model predictions from the HTML. It fetches an original only when the local page asks to play it, uses the pre-existing `fetch_verified` SHA256 check, and refuses objects outside the bounded WAV/64 MiB policy.
- `scripts/serve_p6_texture_listening.py` starts **only when explicitly invoked** by a local operator, listening on `127.0.0.1` with an OS-chosen port, a randomly generated per-run session token, and strict loopback/Host/path checks. It requires the 2–8 exact P6 package IDs, read-only `REPEATABLE READ` metadata lookups, saved queue schema/source binding, and `--expected-report-sha256` to pin the queue file to the previously checked bytes. A digest pin is **not a digital signature**.
- The server sends `no-store` caching and no-referrer/security headers, uses no external JavaScript, stylesheets or telemetry, and rejects unknown paths. Audio is delivered in memory and supports single `Range: bytes=...` playback/seeking requests (partial HTTP 206). Multi-range and invalid ranges are refused; it does not implement broad internet media hosting or unbounded streaming.
- For each listening pair the human explicitly chooses `same_recording`, `different_music`, `uncertain`, or leaves it unreviewed. Decisive labels require a separate opaque evidence reference. Export is a direct, user-initiated CSV response: **no audio bytes or review rows are stored on the server, in the P6 database or in MinIO**. The browser may save the CSV to the operator's local Downloads folder only on the human's action.
- `scripts/evaluate_p6_texture_labels.py` now supports **repeating `--reviews`** for each independent reviewer CSV. The existing strict validator still requires distinct reviewer pseudonyms and agreement for each pair; duplicate reviewers, missing evidence and disputed votes do not become passing labels.

## Operator sequence and limits

First run the read-only `verify_p6_texture_review_queue.py` command on the queue and P6 original package IDs. For the previously verified eight-album development sample, the exact saved queue digest was `20a3a30691d4e7ec00ae59ed1e13ad0f8ce6e62ba8d756e0e644785d0efdd0f4`. The operator-only listening command then takes `--report`, that digest via `--expected-report-sha256`, the same repeated `-p` IDs and `--samples-per-album 2`. It prints a private URL that can be opened **only on the same PC** while the server is running. Exit with Ctrl+C.

Different human reviewers should complete the page in separate sessions and preserve distinct exported CSVs. The evaluator can accept the verified queue JSON with two or more `--reviews` CSV inputs directly, without a manual copy/paste merge. The CSV contains source hashes, reviewer pseudonym, chosen relationship and evidence reference; it is operator-managed and needs appropriate privacy/access controls. The code validates *format*, not real identity, independent judgment or the truth of a claimed reference.

**Security and copyright boundaries:** Do **not** tunnel, port-forward or bind this HTTP service beyond localhost. Anyone able to observe the local URL/token and reach the machine's loopback port can access audio during that run. It is not a multi-user authenticated or encrypted review product and cannot verify rights to hear, copy or distribute recordings. Stop the process when not actively reviewing. There is no active server after code/test completion, and no user-facing audio link was created by this development stage.

## Validation

- Synthetic sources and in-process local HTTP checks confirmed HTML score blinding, SHA256 verification before audio delivery, denied hostile hosts/session paths, non-cached response headers, byte-range handling and rejected invalid CSV submissions.
- Separate synthetic reviewer exports were accepted by the existing evaluation tool as two `--reviews` inputs while maintaining `operational_threshold_ready = false`.
- Focused listening/evaluator tests: **38 passed** (exit 0), including synthetic end-to-end loopback HTTP/WAV byte ranges and separately exported CSV submissions. Repository-wide Ruff **passed**; mypy **462 source files passed**. Full non-integration regression **2,701 passed, 1 skipped, 80 integration deselected, 15 warnings** (exit 0). The listening CLI direct `--help` invocation also passed. Actual human browser review and live provider integration were not run.

The previous real-source integrity recheck independently reproduced 6 review-pair scores and source selection after 28 checksum-verified reads; this new loopback service has **not been operated as a live human listening session**. Real hearing judgments, false-positive calibration, copyright, 0038 migration resolution, fingerprint backfill and outside publication remain separate tasks.
