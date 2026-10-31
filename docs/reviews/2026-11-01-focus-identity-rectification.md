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
| D-3 | Old lofi/rnb seed queries stay active in `market.market_queries` | open: deactivated by P1-4 once that is deployed |

## P1 market intelligence

| ID | Item | Status |
|---|---|---|
| P1-1 | The genre and subgenre lists are closed and have no focus-electronic values | in progress |
| P1-2 | Substring matching: "no vocals" maps to vocal, "chillstep" to chill, "sleepless" to sleep | in progress |
| P1-3 | Two values on a single-value dimension make the whole video unclassifiable | in progress |
| P1-4 | Seed pack is all old concepts, and removed seeds are never deactivated | in progress |
| P1-5 | YouTube search sends no regionCode or relevanceLanguage (audience target: US and other English-speaking markets) | in progress |
| P1-6 | `production_fit` copyright-risk table scores by genre; focus_electronic gets the 0.5 default | open |

## P2 allocation

| ID | Item | Status |
|---|---|---|
| P2-1 | P2 has no identity concept | fixed: cd5332d (`brand_identity.json`, `out_of_identity` blocking) |
| P2-2 | The p2-v1 caps (genre/use case 0.6) would leave a single-identity cycle partial | in progress (p2-v2) |
| P2-3 | Manual overrides (`pause_arm`/`force_arm`) are stored but never read | open |
| P2-4 | No sub-style reaches P3: the C2 brief has no subgenre | in progress |

## P3 experiments

| ID | Item | Status |
|---|---|---|
| P3-1 | No focus_electronic template and no sub-style templates | in progress |
| P3-2 | Inference sends study_focus/focus to lofi, ambient and jazz | in progress |
| P3-3 | The duration policy is 45–75 min; ADR-0044 sets 90–120 min | in progress |
| P3-4 | No focus palette; existing palettes carry vinyl_crackle, trap and neon textures | in progress |
| P3-5 | A BPM treatment can shift tracks outside 70–110 | in progress |

## P4 / P5

| ID | Item | Status |
|---|---|---|
| P4-1 | Default request genre is `rnb` (`music_factory/domain/models.py:79`); the provider registry is not used at runtime | open (low impact) |
| P4-2 | Hand-making 33–44 tracks per 90–120 min video by hand is a heavy owner workload; there is no Lyria (Gemini API) adapter | open: needs an API adapter, and each paid run needs authorization |
| P5-1 | No audio check for vocal presence, BPM or sudden drops; an instrumental track with sung vocals would pass | open |

## P6 media

| ID | Item | Status |
|---|---|---|
| P6-1 | The only brand is "Neon Seoul" with a neon palette; the default motifs are neon/rain/cityscape | in progress (display name now "Longform Signal") |
| P6-2 | None of the 22 visual worlds is a workspace or technical environment; the fallback is a rainy night city | in progress |
| P6-3 | Titles use "Official Mix" / "[Full Album Mix]"; tags are lofi | in progress |
| P6-4 | Every track is titled "Track N" | in progress |
| P6-5 | The thumbnail has a "FULL ALBUM" badge and no logo | in progress |
| P6-6 | AI disclosure is on only if P5 flags a track | in progress |
| P6-7 | The FFmpeg step timeout of 3600 s is too short for 120-min renders | in progress |

## P7 / P8 / P9

| ID | Item | Status |
|---|---|---|
| P7-1 | `containsSyntheticMedia` is not forced on | in progress |
| P7-2 | The upload sends `madeForKids`; YouTube's writable field is `selfDeclaredMadeForKids` | in progress |
| P7-3 | `defaultLanguage` / `defaultAudioLanguage` are never sent | in progress |
| P8-1 | Label and publisher defaults are "Neon Records" | in progress |
| P9-1 | KRW is the reporting currency | not a conflict: internal accounting for the owner |

## Brand id

| ID | Item | Status |
|---|---|---|
| B-1 | The internal brand id `neon_seoul` is the default in about 10 places and on every DB row | deferred: rename needs a data migration across schemas; done last |
| B-2 | The real YouTube channel's display name | open: changing it is an external write and needs authorization |
