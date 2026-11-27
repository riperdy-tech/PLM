# Full repository review, 2026-11-26

Code reviewed: `review/r6-live-adapters` at `e126647`. Read-only review in seven areas
(pipeline core, dashboard/Studio, P6 media, P7–P9 and the learning loop, P1–P3,
P4–P5, ops/tests/docs). Every finding below was checked against the source by its
reviewer; the ones marked ✔ were re-checked a second time before this report.
Confidence is high unless stated. Line numbers are at `e126647`.

Priority: **P0** fix before the next real run · **P1** fix soon · **P2** worth doing ·
**P3** cleanup.

---

## P0 — fix before the next real run

| # | Area | Problem | Repair |
|---|---|---|---|
| 1 ✔ | Tests / DB | `make.ps1 test` and `make test` run plain `pytest -q`; integration tests are not excluded and skip the isolation fixture (`tests/support/platform_env.py:41-45`), so they read the checkout's `.env` and write to the **real** database and the shared Temporal (gate tests, `test_r5_http_steps.py`, `ar/test_temporal_jobs.py` on the production AR queue, `test_deployed_pipeline_worker.py` starts a real run with brand `longform_signal`). `ops.pipeline_transitions` / `exception_events` rows can never be deleted (triggers). Most likely source of the fake 2026-11-15 rows. | `test` target: `pytest -m "not integration"`. Session fixture in `tests/conftest.py` that refuses any integration test unless the DB is a disposable target and `PLM_INTEGRATION_ISOLATED_STACK=1`. Fix `AGENTS.md:66`. Tag test-written rows with `created_by="test:*"`. |
| 2 ✔ | Ops | **No backups** of Postgres (which also holds Temporal), MinIO (all WAV masters, renders, art) or the art inboxes. Plain named volumes on C:, D: already failing. | Scheduled `pg_dumpall` + `mc mirror` to another disk or cloud with retention; `docs/runbooks/backup-restore.md` with a tested restore. |
| 3 ✔ | Dashboard security | Service token is embedded in `GET /` for **any Host header**; no `TrustedHostMiddleware`, CORS allows every loopback port (`dashboard/app.py:68-118`). DNS rebinding or any local page on another port can read the token and call approve / cancel, and the same token opens the phase APIs. | `TrustedHostMiddleware(["127.0.0.1","localhost","[::1]"])`; refuse mutating requests whose `Origin`/`Sec-Fetch-Site` isn't the page's own; drop CORS; embed a per-launch dashboard secret instead of the platform token. |
| 4 ✔ | Studio | Review video cache is keyed by `package_id` only (`studio.py:812-817`). After a picture remake the review keeps playing the **old** video — the owner can approve something they never saw. | Key by `(video_master_uri, checksum)`; clear on remake/import; Approve sends the checksum it showed and is refused if it changed. |
| 5 ✔ | Pipeline / P7 | A `rewind_to=assemble` resume is accepted on **any** upload-step hold (`workflow.py:815-816`), including a platform-fault hold raised after the video is already on YouTube (processing deadline, second of two packages). The rewind re-uploads under new keys → a **second public upload**. A gate test codifies it. | Honour the rewind only on `PUBLISH_APPROVAL_AWAITED` and the assembly thumbnail hold, or only when the upload step produced no publication and no `execute` receipt; `workflow.patched`; change the test. |
| 6 ✔ | P7 | In real mode a retry of any attempt with a ledger entry is refused forever: the ledger answers "replayed" for RESERVED and COMMITTED entries (`ledger.py:567`) and the guard turns that into DENY `budget_reservation_replayed` (`execution.py:410-414`). The engine re-authorizes the thumbnail even when `thumbnail_uploaded` is already set. A failed chunk or a playlist 5xx leaves a **live video with no publication record**. Tests use a mock guard that always says EXECUTE. | Skip thumbnail/playlist when already done (`performed_earlier`); treat a RESERVED entry of the same key with a stored `upload_session_uri` as a resume; engine tests against the real guard and ledger. |
| 7 ✔ | P7 | `httpx` timeouts / connection errors are never mapped to transient (`client/real.py`), so they become FAILED_PERMANENT with no video id. A lost response to the last chunk leaves a public video P7 doesn't know about. | Map `httpx.TransportError` to `YouTubeTransientError`; on resume with a session URI and no video id, send `PUT Content-Range: bytes */total` and resume from the server's offset or take the returned video. |
| 8 ✔ | P4 | If no inbox variable resolves, real music mode falls back to `"suno"` — the automated cookie client that extracts audio from the video to get around Suno's download limit (`music_factory/config.py:52`, `suno_client.py:397`). `.env` is resolved relative to the start folder. Only a `music.generate` grant stands in the way, and `.env.example` shows that grant as its example. | Refuse (or use mock) when real mode has no inbox. Delete `suno_client.py`, `SunoAdapter`, the `"suno"` factory branch and registry entry, `scripts/produce_full_playlist.py`; remove `SUNO_COOKIE`/`SUNO_API_KEY` from `.env`; revoke any `music.generate` grant. |

## P1 — fix soon

### Pipeline core
| # | Problem | Repair |
|---|---|---|
| 9 ✔ | A failed **platform** activity (`record_transition`, `raise_hold`, `authorize_resume`, `write_costs`) fails the whole Temporal run (`workflow.py:944-958`, 10 attempts, 30 s cap). A ~3 min DB/Docker restart, or a missing ledger currency at `write_costs` after the video is public, kills the run; `REJECT_DUPLICATE` means that directive can never run again. | Unlimited retries for idempotent platform ops (cap ~5 min); turn business refusals into a platform-fault hold; `workflow.patched`. |
| 10 | Waiting steps (`await_processing`, `dispatch_reward`) get 5 attempts at 1–8 s ≈ 15 s, so slow YouTube processing ends in `STEP_RETRIES_EXHAUSTED` that needs a manual resume. | Own retry policy for these steps (30 s initial, 10 min cap, unlimited, bounded by schedule-to-close). |
| 11 | Clearing a QA quarantine hold never resolves the P5 case, so the "cleared" songs still aren't usable and assembly reports supply short (`workflow.py:406-413`). | On clear, call P5 `/qa/quarantine/{id}/resolve` with the evidence in an activity. |
| 12 | Cancel does not stop a running step: an upload in progress still publishes after Cancel (`workflow.py:650-659`). | Race the activity against the cancel signal and cancel it; P7 step checks `is_cancelled()` before each POST. |
| 13 | A resume sent right after a hold is raised can be dropped (signals ignored until `_state` is HELD; cases are already OPEN in the DB). Studio says "sent". | Accept signals while a hold is forming, or use a Temporal update with a validator. |
| 14 ✔ | Start sends `POST /ar/cycles` without an Idempotency-Key (`pipeline/start.py`); a double click makes two runs. | Client-generated request id as Idempotency-Key. |

### Studio / dashboard
| # | Problem | Repair |
|---|---|---|
| 15 | Import commits, then the resume fails (restart, P6 hiccup) → the run is stuck at the thumbnail step: re-import is refused `thumbnail_art_not_awaited`, Try again refuses too. | If the package is already `ready_for_publish` and a thumbnail hold names it, only resume. |
| 16 | A failed or interrupted remake (in-memory task) leaves the review with no Import button and Approve refused — only Reject/Cancel. | Show the step-2 controls whenever the package is `awaiting_thumbnail_art` and nothing is running, including during review. |
| 17 | "Continue" on one project's songs resumes **every** project waiting for songs (`studio.py:522-530`). | Take the project/case id; resume only that hold. |
| 18 | Approve writes 2 h capability-wide YouTube grants **before** the resume; on a refused resume they stay live; never revoked after the upload; a longer manual grant is silently replaced. | Decide the case first, then grant, then signal; remove grants on refusal and after the publish is verified; scope grants to the package. |
| 19 | "Try again" fills required evidence with boilerplate for **any** non-hard-stop hold, so one click resumes `SIMULATED_PUBLICATION` or `EXTERNAL_WRITE_NOT_AUTHORIZED` with fake evidence. | Allow only known retryable codes; others show "ask Claude", no button. |
| 20 | Async Studio routes do blocking DB / P6-HTTP work on the event loop; a slow P6 freezes every poll and the video stream. | `asyncio.to_thread` for the sync parts, or plain `def` handlers. |
| 21 | Whole video held in RAM; each range request copies it; cache dict not thread-safe (`KeyError` → 500). | Verified temp file per checksum served with `FileResponse`; one download under a lock. |

### P6 media
| # | Problem | Repair |
|---|---|---|
| 22 | Any render failure after the claim blocks that package id **for good**: quarantined attempt → 409 `render_job_reconciliation_required`; `queue_reconciled_render_retry` exists but nothing calls it (`service.py:115-147`, `render_claims.py`). | Studio/CLI "retry render" that queues the reconciled retry and re-runs `assemble_and_persist(recovery_job_id=…)`; turn the `ValueError` into `render_intent_conflict`. |
| 23 | `_write_art_job` runs outside the fenced `try` after persist; a write failure rolls back the finish but leaves the RUNNING claim → 409 forever (`service.py:209`). | Write the art job after commit, non-fatal. |
| 24 ✔ | Packages `awaiting_thumbnail_art` are invisible to the near-copy / variation history (`repositories.py:446-448`), so two runs rendered before art import can share recordings, worlds and titles. | Include `AWAITING_THUMBNAIL_ART` in `list_catalog_albums`; test. |
| 25 | Whole WAV/MP4 masters (a 120-min WAV is ~2 GB) read into RAM in renderer, importer and store. | Streamed `put_file` / `fetch_verified_to(path)`; consider FLAC masters. |
| 26 | `atrim` only shortens; a source a few ms shorter than its manifest shortens the mix → QA `render_duration_mismatch` → failed package (see 22). | `apad=whole_dur=D,atrim=end=D`, or refuse when decoded length differs > 20 ms. |
| 27 | `loudnorm linear=true` silently falls back to dynamic (pumping); `normalization_type` is recorded but never checked. | Refuse or flag `dynamic`, or apply the linear gain with `volume`+`alimiter`. |
| 28 | Reordering thumbnails at review makes the thumbnail differ from the video picture. | For owner-art packages, refuse the reorder (reopen + pick + import) or re-render the still. |
| 29 | `--rewrite` keeps a stale `pick.txt`; a second rewrite overwrites the archived images. | Move `pick.txt` too; unique `previous-<ver>-<n>` folder. |
| 30 | Unused songs (`not_selected`) are only in the render report; paid hand-made songs vanish silently. | Return them in the response and pipeline result; keep an unused pool. |

### P7–P9 and the learning loop
| # | Problem | Repair |
|---|---|---|
| 31 | Reservations never released when the write definitely did not happen (quota 403, auth 4xx) → the attempt is wedged (see 6). | Classify "definitely not performed" and settle `performed=False`; 429/`rateLimitExceeded` retryable. |
| 32 | `OperatingMode.PRIVATE_ONLY`/`STAGED` not enforced; privacy always comes from the profile — a `private_only` publish can go public. | Force `private` in private_only, test it. |
| 33 | `published_at` = P7 completion time and privacy is a frozen snapshot; a video uploaded private and made public later is measured from the wrong start. | Window start from YouTube's own publish time, refreshed before measuring; refuse to measure a non-public video. |
| 34 | Any edit to series/playlist config or the channel profile while a video is processing makes the next poll 409 `candidate_selection_conflict` → uploaded video with no record. | Once a manifest is stored, resume it and compare only explicit selections. |
| 35 | **No YouTube-only video can produce a reward** — see the chain below. | See the design below. |

### P1–P3
| # | Problem | Repair |
|---|---|---|
| 36 ✔ | A visual world in the P2 brief (mined from title words: "rainy" → `rainy_city`, "tokyo"/"neon" → `neon_tokyo`) is used as is, bypassing the recent-worlds rule and the template's focus worlds (`genome/engine.py:696-703`). Off-identity art and P6 world-gap refusals. | Accept a brief world only if it is in the template's allowed worlds and not recent; else `choose_fresh`. Or drop `visual_world` from the brief. |
| 37 | The owner's style choice fails within 72 h: cooldown filters before the owner's sub-style; P2 reads only P1's first 200 opportunities (`limit=200`, no paging). | Keep cooled arms of the chosen style as a fallback with a warning; page P1 or filter by brand; mark cooling styles in the Start menu. |
| 38 | One-slot cycles always go to "exploit"; the exploration policy does nothing — exploration only comes from the cooldown. | Below ~3 slots draw the class at random with the policy weights (seeded), or plain Thompson sampling. |
| 39 | Reward scale vs model: rewards cluster 0.5–0.75, observation variance 0.09 swamps that, P1 priors span 0.05–0.95, retirement (0.25) never fires. Learning barely moves. | Rank rewards against the channel's own videos; set observation variance from observed rewards; centre priors on the channel baseline; relative retirement threshold. |
| 40 | Retirement overwrites BLOCKED (out-of-identity) arms and reopening can make an old lofi arm eligible again. | Evaluate only CANDIDATE/ACTIVE arms; reopen only `WEAK_POSTERIOR` retirements. |
| 41 | P1: when YouTube says `quotaExceeded`, every due query is marked done; scores stamped with recompute time so P2 thinks the data is fresh. `RequestBudgetExceededError` not caught → rollback + Temporal retry repeats searches. | Stop and reschedule on quota; mark a query run only on success; stamp `as_of` with the newest observation; catch both errors. |
| 42 | P1 demand score adds views-per-day-per-subscriber (~1e-4..1) to a raw sighting count, so "demand" measures seed coverage. | Rank each part separately and average, or drop the count. |
| 43 | P3 takes P1's mood/language unchecked (sensual, romantic, "korean" on an instrumental spec). | Focus mood allowlist; force `language="instrumental"`; add language to the identity check. |

### P4–P5
| # | Problem | Repair |
|---|---|---|
| 44 | Song file names are `<track>-<sha256(prompt)[:8]>`; any prompt-code change while songs are awaited renames them and the owner's saved WAVs become "missing" / refused. | Name by track id + variant; store the prompt text when the sheet is first written. |
| 45 | Problems other than "missing" (32-bit float WAV, duplicate audio) are invisible: Studio counts the file as saved, P4 rewrites the sheet without a reason, the pipeline keeps only the first plan's problems. | `Problem:` line per song on the sheet; Studio runs the same check on save; all plans' problems in the hold; read WAVs with soundfile. |
| 46 | The sheet never states the accepted length (90–300 s); a long Suno extension is made, imported, then rejected. | Print "Accepted length 1:30–5:00"; check length before import. |
| 47 | A rejected song has no reason shown and no way to replace just it; transient measurement UNKNOWNs become permanent rejections (fixed evaluation id). | Return blocking finding codes; P4 "replace candidate" with `-r2` file name; UNKNOWN → hold or `re_evaluate`. |
| 48 | Quarantine cases never close (other candidates' cases stay open; `RE_EVALUATE` only withdraws). | Close as `CONFIRMED_REJECTED`; real re-evaluation; sweep orphans. |
| 49 | P4 duplicate check only spans one plan (one song each in manual mode) → the same WAV saved under two names imports twice. | Hash index over the whole inbox plus stored artifacts. |
| 50 | Tempo band with half/double plus tolerance only rejects ~112–136 BPM; 140–174 BPM drum & bass clears. | Accept half-time only when the half-tempo peak is comparably strong; warn above 136; test tracks at 120/128/140/170. |
| 51 | No vocal or stuck-loop detection; a forgotten Instrumental toggle clears. | Vocal-presence estimate as a hold; self-similarity repetition check. |
| 52 | Old sheets/WAVs of cancelled runs stay forever and show in the Studio as waiting. | Plan id in the sheet header; move to `imported/<plan>` / `cancelled/`; Studio lists only running plans. |

### Ops
| # | Problem | Repair |
|---|---|---|
| 53 ✔ | `docker-compose.yml` fixes `name: plm` and mounts `.`; any `docker compose up` from a worktree takes over the live stack with unmerged code and kills a render. | `name: ${COMPOSE_PROJECT_NAME:-plm}` + launcher guard (main checkout only); CI writes `.env` only when `$CI`. |
| 54 | The bind-mounted `.env` silently sets provider modes, policies and grants inside containers; `authorize_youtube.py` rewrites it non-atomically. | Explicit container keys in compose; log effective policy at boot; atomic `.env` writes. |
| 55 | Renders and WAV masters in MinIO grow forever on C:. | Retention command (keep published masters); free-disk check in `plm doctor` and the launcher. |
| 56 | Container image ignores `constraints.txt`; floating `python:3.12-slim` / `postgres:16`. | `pip install -c constraints.txt`; pin base images by digest. |
| 57 | No `restart:` policy, memory limits or stop grace period; every launcher click runs `docker compose up -d`, which can recreate containers mid-render. | `restart: unless-stopped`, `mem_limit` on `api-media`, `stop_grace_period: 120s`; launcher skips `up` when healthy. |

## The learning loop (finding 35)

What must change, in order, for a published YouTube-only video to reach P2:

0. P7 must finish the publication record (findings 6, 7, 34, plus a stale-attempt return at `engine.py:438-440`).
1. `distribution.enabled=false` (otherwise the mock P8 holds as `SIMULATED_PUBLICATION`).
2. `evaluate_outcome` refuses "no release was submitted" (`http_outcome.py:156-158`).
3. A real P7 has `analytics=None` → 409 `analytics_source_not_configured`; P9's real `RealYouTubeAnalyticsClient` is not wired in.
4. P9 `/evaluation/measure` requires ≥1 ISRC (`schemas.py:191`).
5. Economics withholds the net contribution without realized revenue (`calculator.py:78-79`).
6. Measurement runs exactly at window end; YouTube Analytics lags 2–3 days; "no rows" becomes a non-retryable 409.

Smallest sound design (no invented data):
- Wrap `RealYouTubeAnalyticsClient` as P7's real analytics source (Pacific-time date windows; 409 `analytics_not_final` until end + 3 days, mapped to `StepNotReady`).
- Workflow waits `matures_at + 72h`.
- `evaluate_outcome` skips release linking when distribution is disabled and sends `isrcs: []`; P9 accepts that only for channels in a `youtube_only_channels` setting.
- An operator-attested, dated "channel not monetized" fact recorded as realized revenue 0 (source `operator_attested_non_monetized`), refused when missing or expired. The existing reward formula and P2 then work unchanged. Flip it when the channel joins YPP.
- End-to-end test of a YouTube-only run with distribution disabled.

## P2 / P3 — smaller improvements

- **Dashboard:** no upload size limit (cap by `Content-Length`); notes file read-modify-write with no lock and a shared temp name; a new DB engine per call in `import_art`/`_p6`; `start-options` recomputes 104 plans every 5 s; review page stops auto-refreshing once the change panel shows (`description.defaultValue` unset); `SourceUnavailable` becomes a plain 500 and `actionButton` hangs on network errors.
- **Launchers:** `PLM_Control_Deck.bat` and `scripts/plm_desktop_control.py` hard-code `change-me-dev-service-token`, check a `/healthz` that doesn't exist, pipe stdout nobody reads, and show a fake lo-fi GO/NO-GO; `run_dashboard.bat` says "DEMO DATA" but runs the live dashboard. Delete them; keep `PLM_Studio.vbs`. Refuse `change-me*` tokens outside CI.
- **Pipeline:** a cancelled held run leaves its cases OPEN; song exclusions at an upload hold are dropped without an approval hold; a Temporal reset kills the run (transition key); `put_content` treats a same-digest retry as an error; dead branches (`reward_not_delivered`, `_Failure.no_effect`).
- **P7:** a new `httpx.Client` per 1 MiB chunk (~800 TLS handshakes per video) — reuse one client, 8–16 MiB chunks; re-reading and hashing the 821 MB master on every poll; `cli reconcile` always uses the mock; VERIFIED attempts keep stale error codes; the daily report treats yesterday-UTC as the latest Analytics day.
- **P6:** waveform path is dev-only and slow — delete or align with the still path; Pillow thumbnails still generated in inbox mode; `DecompressionBombError` not caught; picked image not required to be ≥ 2560 px for 1440p; importer holds a DB transaction through a 20-min render; package still says 1080p after the 1440p still swap; edited titles never reach variation history; replay ignores a changed visual profile.
- **P1–P3:** every track gets the template's target BPM (whole channel ~90 BPM) — vary within range; regime-shift uses a relative change on percentiles; ties sort by `arm_id`; cold-start priors never update; weekly P2 schedule and `scripts/autonomous_loop_worker.py` could start unrequested AUTONOMOUS cycles (delete or force SHADOW); SHADOW picks still record exposure; manual supply keeps 25 % spare songs (33 songs for a 2-hour video).
- **P4–P5:** WAV duration from the header (truncated files); each WAV read and hashed every poll and decoded ~5× by P5; Styles text not checked against Suno's ~1000-character limit; prompt variant text written for an AI reader; per-run `?provider=` not stored on the plan, and equal inbox paths not refused; Flow Music rights record cites terms the module says it never found (`COMMERCIAL_LICENSE_UNCERTAIN` until recorded); failed imports use up retries for good.
- **Ops / docs:** migrations need a code edit to `migrations/env.py` each time — move to one `plm migrate --approved-backup` command; CI caches on `pyproject.toml` not `constraints.txt`, no Windows job, mypy skips scripts/tests; README says the dashboard is demo-only; `docs/STATUS.md` header from 2026-10-15; ADR-0044 still says "Longform Signal"; distribution defaults (label/copyright) say "Longform Signal"; missing runbooks (backup/restore, stuck run, OAuth rotation, full disk); `.env.example` lacks `YOUTUBE_OAUTH_REFRESH_TOKEN`; `authorize_youtube.py` sends no OAuth `state` and prints the token response on failure; unrelated `docs/Self-Improving Trading System Research.md` and superseded handoff files.

## Checked and fine

C6 seam versioning; manifest hash after the video swap; title variation never dead-ends (simulated); reopen → import; chapter rounding; crossfade timeline; render timeouts for 2-hour programmes; the one `workflow.patched` in use; no OAuth token logging found.

## Suggested order

1. Findings 1, 2, 8 (data safety, and the automated Suno path) — small changes, large risk.
2. Findings 3–7, 9 (security, wrong approvals, double/orphan uploads, runs dying).
3. Findings 15–19, 22–24, 44–47 (the stuck states the owner hits in the Studio).
4. The learning loop (35) — the program cannot learn until this lands.
5. Everything else.

---

## Fix status, 2026-11-27

Every finding was worked on `review/r6-live-adapters` (commits after `6696193`). "Fixed" means
changed and covered by a test that fails on the old code; nothing here was proven against
live providers. Limits are stated where a fix is narrower than the suggested repair.

| # | Status | Notes |
|---|---|---|
| 1 | Fixed | `make test` excludes integration; integration refused unless `PLM_INTEGRATION_ISOLATED_STACK=1` + disposable DB. |
| 2 | Fixed | `scripts/backup_plm.ps1` (full) and P6 archive after every live upload (renders + DB dump to `E:\PLM-backups`); runbook. |
| 3 | Fixed | Loopback hosts only, same-origin writes, per-launch page secret, no CORS. |
| 4 | Fixed | Video cache keyed by checksum; stale approval refused (`video_changed`). |
| 5 | Fixed | Rewind only at the approval hold (`rewind-only-at-approval`). |
| 6 | Fixed | Held reservations resume (`resuming=True`); done thumbnail/playlist skipped. |
| 7 | Fixed | Transport errors transient; upload status query resumes from YouTube's offset. |
| 8 | Fixed | Automated Suno client removed; real music mode needs an owner inbox. |
| 9 | Fixed | Unlimited platform retries; `write_costs` refusal holds; a permanent `record_transition`/`raise_hold` refusal ends the run with a clear failure record (a hold is impossible without them). |
| 10 | Fixed | Waiting steps poll with a long, time-bounded policy. |
| 11 | Fixed | Clearing a quarantine hold resolves the P5 case (`qa_resolve`). |
| 12 | Fixed | Cancel cancels the running step; the step's client stops before its next request. A single P7 call already in flight is not interrupted. |
| 13 | Fixed | Signals kept while a hold forms. |
| 14 | Fixed | One request id per Start form → P2 Idempotency-Key; a repeat replays the first cycle. |
| 15–21 | Fixed | Studio: import resume, step-2 controls, per-project Continue, grants decided→granted→signalled and revoked after publish (capability-scoped, 2 h, named run), retryable codes only, no blocking I/O on the loop, video served from a verified temp file. |
| 22 | Fixed | `retry-render` (API/CLI); the assemble step retries a failed render automatically on its retry/resume. |
| 23–29 | Fixed | See commits (art job non-fatal, catalog includes awaiting art, streamed masters, apad, linear loudnorm, owner-art reorder refused, rewrite moves `pick.txt`). |
| 30 | Fixed | `not_selected` in P6 response and the pipeline's assemble result. |
| 31–34 | Fixed | Refused writes released; private_only/staged enforced; window from YouTube's publish time; stored manifest resumed. |
| 35 | Fixed (owner setting needed) | Real Analytics source, final-data wait (up to 5 days), YouTube-only evaluation with a dated non-monetized attestation; zero-row final windows measured as zero. Needs `PLM_ANALYTICS_CHANNEL_FACTS_FILE`. |
| 36–43 | Fixed | See P1–P3 commits (versions moved: P1 `p1-v2`, prior `p2-prior-v2`, identity v3, templates v9). |
| 44–46, 48–51 | Fixed | Stable song names, sheet problems + Studio check on save, accepted length, quarantine closing/sweep, inbox-wide duplicate index, tempo half-time rule, vocal hold + stuck-loop warning (synthetic calibration only). |
| 47 | Fixed | Blocking codes returned; unmeasurable songs re-evaluated then held (`MEASUREMENT_UNAVAILABLE`), never rejected; when the playlist runs short, the run asks P4 to replace each rejected owner-made song (`-r2`, `-r3`; at most 2 per song) through the normal song hold, and only then tops up with new songs (`topup-replaces-rejected-songs`). |
| 52 | Fixed | Plan id on sheets; a fully imported plan's files move to `<inbox>/imported/<plan>/`, an ended run's to `imported/` or `cancelled/`; the Studio, playback and P4's duplicate index read the moved files. |
| 53 | Fixed | Compose project from `COMPOSE_PROJECT_NAME`, worktree guard in make/launcher, required secrets. |
| 54 | Fixed | Containers no longer read `/app/.env` (`PLM_DOTENV_DISABLED`); every key passed by compose; boot logs effective policy; atomic `.env` writes. |
| 55 | Fixed | Archive frees published renders; `plm doctor` and launcher disk checks. |
| 56 | Fixed | `-c constraints.txt`; base images pinned by digest. |
| 57 | Fixed | `restart: unless-stopped`, `mem_limit` on api-media, grace period; launcher skips `up` when healthy. |

P2/P3 list: done. mypy now type-checks `packages/`, `services/`, `scripts/` (three offline audio probes
excluded) and `tests/` (447 files, 0 errors; tests import services by their top-level names) in CI.
A Temporal reset continues the run (runs started before the deploy included).

Owner actions: replace the `change-me` service token (refused now); set `PLM_FLOW_MUSIC_TERMS_REF`
(Flow Music songs hold as `COMMERCIAL_LICENSE_UNCERTAIN` until then); create the P9 channel-facts file
for the learning loop.
