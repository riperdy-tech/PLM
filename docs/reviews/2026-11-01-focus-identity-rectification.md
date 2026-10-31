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
| P3-3 | The duration policy is 45–75 min; ADR-0044 sets 90–120 min | fixed: 866734a (90_120m default; ADR-0036 amended) |
| P3-4 | No focus palette; existing palettes carry vinyl_crackle, trap and neon textures | fixed: 866734a (contrast v6, per-sub-style) |
| P3-5 | A BPM treatment can shift tracks outside 70–110 | fixed: 866734a (bpm_limits); c303313 adds energy_limits (low/medium) |

## P4 / P5

| ID | Item | Status |
|---|---|---|
| P4-1 | Default request genre is `rnb` (`music_factory/domain/models.py:79`); the provider registry is not used at runtime | open (low impact) |
| P4-2 | Hand-making 33–44 tracks per 90–120 min video by hand is a heavy owner workload; there is no Lyria (Gemini API) adapter | open: needs an API adapter, and each paid run needs authorization |
| P5-1 | No audio check for vocal presence, BPM or sudden drops; an instrumental track with sung vocals would pass | open |

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
| B-1 | The internal brand id `neon_seoul` is the default in about 10 places and on every DB row | deferred: rename needs a data migration across schemas; done last |
| B-2 | The real YouTube channel's display name | open: changing it is an external write and needs authorization |

## Found during verification

| ID | Item | Status |
|---|---|---|
| V-1 | First focus collection failed: `arm_slug_identity_conflict`, because v2 ids are batch-order slugs | fixed: 53abcdc, taxonomy v3 (digest ids) is the default |
| V-2 | Old v1/v2 arms kept being served on their last scores | fixed: 83eaf55, recompute retires arms from other taxonomy namespaces |
| V-3 | A `focus_electronic` arm with no use case was refused | fixed: 00d2b02 |
| V-4 | All focus arms fall in one similarity cluster, so p2-v2's limit of 2 per cluster gives 2 directives per cycle, with the `ALL_SELECTED_ONE_CLUSTER` alert | not a defect: matches the planned cadence of 2 uploads a week; revisit when sub-style arms carry more evidence |

## Verification (2026-11-01)

- Unit, contract, e2e and simulation suites: 3554 passed, 1 skipped. Postgres, MinIO and integration tests that need `DATABASE_URL` were not run.
- The PLM containers were restarted on the new code and are healthy.
- P1 daily run (YouTube read calls only) with the focus seeds: 252 videos classified under v3, 26 of them focus_electronic. They formed 5 focus_electronic arms: deep_work_chillstep ×2, future_garage_focus ×2 and minimal_pulse. 64 v3 arms are served; all v1/v2 arms are retired.
- Final shadow P2 cycle 65949721: 10 eligible, 2 selected, both genre-less study_focus arms, which P3 builds as focus_electronic. SHADOW, so not actionable.
- No real album has been made yet. Templates, prompts and visuals are proven on fixtures and mocks only.

## Still open

- P4-2: a Lyria (Gemini API) adapter for 33+ tracks per video; each paid run needs authorization.
- P5-1: audio detection of vocals, BPM and drops.
- B-1: internal brand id rename. B-2: the real channel's display name (an external write).
- P2-3: manual overrides are never read (not an identity conflict).
