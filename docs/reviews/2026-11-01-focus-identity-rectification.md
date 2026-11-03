# Focus-identity rectification register (2026-11-01)

Decision being applied: ADR-0044, locked by the owner on 2026-11-01. The owner then
ruled that every part of P1–P9 that conflicts with the identity is to be fixed
without further approval, and that design choices are decided by fitness for the
revenue target.

Sources: three read-only audits (P1; P3–P5; P6–P9 plus brand) run on 2026-11-01 at tip 75b0acd.
Status: **open**, **in progress**, **fixed** (code, tests passing), **deferred** (reason given).
"Fixed" means the tests pass on fixtures and mocks. It does not mean a real album has been made.

## Data (dev DB `plm`)

| ID | Item | Status |
|---|---|---|
| D-1 | Held vocal R&B run `b0eb2a4f` | fixed: cancelled; sheets in `data/flow-music-inbox-archive/run-b0eb2a4f` |
| D-2 | 64 of 77 arms are outside the identity | fixed: set `blocked`, reason `out_of_identity:ADR-0044`; backup `data/backups/ar_arm_state_before_identity_block_20261031T195007Z` (sha256 02d2326c…) |
| D-3 | Old lofi/rnb seed queries stay active in `market.market_queries` | fixed: 22 focus queries installed, the 20 old ones set inactive (2026-11-01) |

## P1 market intelligence

| ID | Item | Status |
|---|---|---|
| P1-1 | The genre and subgenre lists are closed and have no focus-electronic values | fixed: 5f87cb8 |
| P1-2 | Substring matching: "no vocals" maps to vocal, "chillstep" to chill, "sleepless" to sleep | fixed: 5f87cb8 (classifier 3-word-boundary) |
| P1-3 | Two values on a single-value dimension make the whole video unclassifiable | fixed: 5f87cb8 (one value kept, the dropped one recorded) |
| P1-4 | Seed pack is all old concepts, and removed seeds are never deactivated | fixed: 5f87cb8 |
| P1-5 | YouTube search sends no regionCode or relevanceLanguage (audience target: US and other English-speaking markets) | fixed: 5f87cb8 (US / en) |
| P1-6 | `production_fit` copyright-risk table scores by genre; focus_electronic gets the 0.5 default | not a conflict: the same default as every unlisted genre |

## P2 allocation

| ID | Item | Status |
|---|---|---|
| P2-1 | P2 has no identity concept | fixed: cd5332d; c303313 narrows the identity to focus_electronic (v2); 6 of 77 arms eligible |
| P2-2 | The p2-v1 caps (genre/use case 0.6) would leave a single-identity cycle partial | fixed: 866734a and 5e80082; p2-v2 stored and active in the dev DB |
| P2-3 | Manual overrides (`pause_arm`/`force_arm`) are stored but never read | open: not an identity conflict (the identity uses lifecycle `blocked`) |
| P2-4 | No sub-style reaches P3: the C2 brief has no subgenre | fixed: 866734a (C2 2.1) |

## P3 experiments

| ID | Item | Status |
|---|---|---|
| P3-1 | No focus_electronic template and no sub-style templates | fixed: 866734a |
| P3-2 | Inference sends study_focus/focus to lofi, ambient and jazz | fixed: 866734a |
| P3-3 | The duration policy is 45–75 min; ADR-0044 sets 90–120 min | fixed: 866734a (90_120m default; ADR-0036 amended); revised to 20–30 min (20_30m default, templates v7) after the owner ruled out a paid API |
| P3-4 | No focus palette; existing palettes carry vinyl_crackle, trap and neon textures | fixed: 866734a (contrast v6, per-sub-style) |
| P3-5 | A BPM treatment can shift tracks outside 70–110 | fixed: 866734a (bpm_limits); c303313 adds energy_limits (low/medium) |

## P4 / P5

| ID | Item | Status |
|---|---|---|
| P4-1 | Default request genre is `rnb` (`music_factory/domain/models.py:79`); the provider registry is not used at runtime | not a conflict: the only production caller (`generation/engine.py:129`) always sets genre and vocal mode from the C3 spec, so the defaults never reach a provider |
| P4-2 | Hand-making 33–44 tracks per 90–120 min video by hand is a heavy owner workload; there is no Lyria (Gemini API) adapter | resolved by the 20–30 min revision: about 6–9 tracks per video by hand on free Gemini credit; no paid adapter |
| P5-1 | No audio check for vocal presence, BPM or sudden drops; an instrumental track with sung vocals would pass | partly fixed: tempo outside 70–110 BPM (half/double counted) rejects, a sudden level rise warns (p5-identity-audio-v1, calibrated on 40 real tracks: 0 findings). Vocals: won't do (owner, 2026-11-01): no detector; the owner hears every hand-made track, and lyrics on an instrumental are already caught |

## P6 media

| ID | Item | Status |
|---|---|---|
| P6-1 | The only brand is "Neon Seoul" with a neon palette; the default motifs are neon/rain/cityscape | fixed: 8c76c6c ("Longform Signal", palette v2) |
| P6-2 | None of the 22 visual worlds is a workspace or technical environment; the fallback is a rainy night city | fixed: 8c76c6c |
| P6-3 | Titles use "Official Mix" / "[Full Album Mix]"; tags are lofi | fixed: 8c76c6c |
| P6-4 | Every track is titled "Track N" | fixed: 8c76c6c |
| P6-5 | The thumbnail has a "FULL ALBUM" badge and no logo | fixed: 8c76c6c |
| P6-6 | AI disclosure is on only if P5 flags a track | fixed: 8c76c6c |
| P6-7 | The FFmpeg step timeout of 3600 s is too short for 120-min renders | fixed: 8c76c6c |

## P7 / P8 / P9

| ID | Item | Status |
|---|---|---|
| P7-1 | `containsSyntheticMedia` is not forced on | fixed: 8c76c6c |
| P7-2 | The upload sends `madeForKids`; YouTube's writable field is `selfDeclaredMadeForKids` | fixed: 8c76c6c |
| P7-3 | `defaultLanguage` / `defaultAudioLanguage` are never sent | fixed: 8c76c6c |
| P8-1 | Label and publisher defaults are "Neon Records" | fixed: 8c76c6c |
| P9-1 | KRW is the reporting currency | not a conflict: internal accounting for the owner |

## Brand id

| ID | Item | Status |
|---|---|---|
| B-1 | The internal brand id `neon_seoul` is the default in about 10 places and on every DB row | fixed at the owner's request (40dc6f2): code, config and tests use `longform_signal`; dev DB renamed by `scripts/rename_brand_id.py` (1,024 rows in 15 identifier columns; backup `data/backups/plm_before_brand_rename_20261101T095129Z.dump`, sha256 bf6a82a4…). JSON snapshots, idempotency responses and storage URIs keep the old id on purpose (records of what was sent, real object paths) |
| B-2 | The real YouTube channel's display name | approved by the owner 2026-11-01: rename "Rhodes & Rain Records" (UC3Ps0F0z8Mw9ftakIcZ1cWA, @riperdsz1446) to "Longform Signal". Not done: the Chrome profile is signed in to a different Google account, so the owner renames it in YouTube Studio |

## Found during verification

| ID | Item | Status |
|---|---|---|
| V-1 | First focus collection failed: `arm_slug_identity_conflict`, because v2 ids are batch-order slugs | fixed: 53abcdc, taxonomy v3 (digest ids) is the default |
| V-2 | Old v1/v2 arms kept being served on their last scores | fixed: 83eaf55, recompute retires arms from other taxonomy namespaces |
| V-3 | A `focus_electronic` arm with no use case was refused | fixed: 00d2b02 |
| V-4 | All focus arms fall in one similarity cluster, so p2-v2's limit of 2 per cluster gives 2 directives per cycle, with the `ALL_SELECTED_ONE_CLUSTER` alert | not a defect: matches the planned cadence of 2 uploads a week; revisit when sub-style arms carry more evidence |
| V-5 | Genre-less focus arms always got the plain base template, so their videos all sounded alike | fixed: P3 picks a sub-style not used by the latest experiments |
| V-6 | Inside the api-ar container P2 asked P1 at 127.0.0.1 (itself), so container cycles always fell back to the stored snapshot (MARKET_UNAVAILABLE); `plm ar cycle` defaulted to p2-v1 | fixed: PLM_SERVICE_URL__MARKET in compose; CLI defaults to p2-v2; container cycle e6a8fd6a live, 10 eligible, no warning |
| V-7 | First real focus run `pipeline.1d4447ac…`: the owner saved the 8 Flow Music songs under Flow Music's titles, not the sheet names | handled: each title was matched to its sheet through the prompt shown in the owner's Flow Music session (read only), then the files were renamed. Measured keys disagreed with several prompt keys, so the key estimator alone could not have matched them |
| V-8 | Resuming the held run was refused (`reviewer_not_authorized`): no container had a reviewer directory, so no hold could ever be cleared | fixed: compose passes `PLM_PIPELINE_REVIEWERS_FILE`; local `data/pipeline-reviewers.json` grants `owner` the `production_owner` role (git-ignored) |
| V-9 | The first real Flow Music import crashed on PostgreSQL: the rights basis (190 chars) and terms version (87) exceed `rights_snapshots.basis` (128) and `terms_version` (64). P4 recorded the plan as failed, the pipeline treated it as a non-retryable `CreativeRejection`, and run 1d4447ac ended FAILED | fixed: shorter texts plus a column-length test; all SQLite test engines now refuse strings longer than their VARCHAR (`tests/support/sqlite_lengths.py`), which found no other code path over its limit. The failed run cannot be retried (workflow id reuse is REJECT_DUPLICATE; P3 would design a different genome for the same directive), so its 8 songs are archived in `data/flow-music-inbox-archive/run-1d4447ac` and a new run was started |
| V-10 | Sheets repeated the kind word ("airy synth lead lead") when the lead value already names it | fixed for future sheets; harmless to Lyria |
| V-11 | Local stack rendered at 360p preview by default, unfit for a real upload | `.env` sets `PLM_MEDIA_RENDER_PROFILE=yt_longform_1080p_v1` |
| V-12 | A platform defect after the owner made songs still ended the run: P4 counted a crashed import's journaled attempt as a paid job to reconcile, so the retry answered a failed plan (a `CreativeRejection`), and a step whose retryable failure outlasted its retries moved the run to FAILED | fixed: P4 treats an import that crashed before its candidate was stored as abandoned (nothing is spent; the WAV is still in the inbox) and imports it again under the next attempt id without using up a retry (`test_an_import_that_crashed_mid_way_is_imported_again`, fails without the fix). The pipeline holds a step that exhausts its retries as `STEP_RETRIES_EXHAUSTED` (hold policy v7, platform owner, evidence `platform_fix_ref`) instead of failing; the resume retries the step under the same key. Proven on Temporal's test server with a disposable PostgreSQL (`test_a_step_that_exhausts_its_retries_holds_and_resumes_after_the_fix`); the gate R3 and R5 workflow tests pass there except the worker-kill test, which fails the same way without this change (the test server times out `describe`). Not proven against a real P4 crash inside the live stack |
| V-13 | Code review of V-9/V-12 (2026-11-02) found: a P4 plan that failed for a platform reason (not a P29 refusal) still ended the run as a `CreativeRejection`; two runs of one import plan at once could import a song twice (the second took the first's in-flight attempt for abandoned); "free" was inferred from the capability name, so a future paid capability would have skipped reconciliation; a crashed import left its guard reservation held; a step out of retry time (`RetryState.TIMEOUT`) still failed; the SQLite length check missed multi-row `values([...])`, custom bind parameters and `TypeDecorator` columns; a bare `lead` became `lead lead` | fixed: P4's failed-plan answer carries `refusal_codes`; without them the pipeline raises the new `PlatformFault`, which holds the run as `STEP_PLATFORM_FAULT` (hold policy v8, platform owner, `platform_fix_ref`) (`test_a_platform_fault_holds_the_run_instead_of_ending_it`, Temporal test server). An adapter declares `costs_nothing`; such a run is one transaction, so the plan row lock makes a concurrent run wait (`test_a_concurrent_run_waits_and_imports_nothing_twice`, disposable PostgreSQL, fails on the old engine); its reservations are committed after the commit and released if it raises, so a crashed import reruns under the same attempt id; a key left held by a killed run is skipped. Each fix has a test that fails without it. Not proven against the live stack |
| V-14 | A platform defect that surfaced as a `ValidationFailure` (a phase answer another phase refuses, e.g. a P6 render its own post-render QA blocked) still ended the run for good | fixed: only a `CreativeRejection` or `PolicyRejection` ends a run; every other permanent step failure holds it as `STEP_PLATFORM_FAULT` (hold policy v9) (`test_a_platform_fault_holds_the_run_instead_of_ending_it[validation_failure]`, `test_a_decided_rejection_still_ends_the_run`, `test_p6_timing_failure_holds_real_temporal_run_before_p7`, Temporal test server and disposable PostgreSQL; each fails on the old workflow). Limit: P6 stores a failed package under an id derived from its inputs and replays it, so a resume after a P6 render fix gets the same failed package and holds again; P6 has no re-render path for a failed package. Not proven against the live stack |

## Verification (2026-11-01)

- Unit, contract, e2e and simulation suites: 3554 passed, 1 skipped. Postgres, MinIO and integration tests that need `DATABASE_URL` were not run.
- The PLM containers were restarted on the new code and are healthy.
- P1 daily run (YouTube read calls only) with the focus seeds: 252 videos classified under v3, 26 of them focus_electronic. They formed 5 focus_electronic arms: deep_work_chillstep ×2, future_garage_focus ×2 and minimal_pulse. 64 v3 arms are served; all v1/v2 arms are retired.
- Final shadow P2 cycle 65949721: 10 eligible, 2 selected, both genre-less study_focus arms, which P3 builds as focus_electronic. SHADOW, so not actionable.
- No real album has been made yet. Templates, prompts and visuals are proven on fixtures and mocks only.

## Still open

- Session compilations are built (`plm media compile-session`) but none has been rendered from real released albums yet; publishing one needs authorization.
- The drop warning is uncalibrated against real EDM drops.
- First real focus video: run `pipeline.c1f32e48-1e16-5271-ae43-0aed12f107e9` (AUTONOMOUS cycle 37ebfbb6, arm study_focus, 90 BPM) is HELD at GENERATING with 8 Flow Music sheets in `data/flow-music-inbox`. The earlier run 1d4447ac FAILED (V-9).
- A run that does reach FAILED (a creative or policy rejection) still cannot be retried for the same directive. V-12 to V-14 keep platform defects out of that path: only a creative or policy rejection now reaches it. A held failed P6 render cannot yet be re-rendered (V-14).
- B-2: the real channel's display name (an external write).
- P2-3: manual overrides are never read (not an identity conflict).
