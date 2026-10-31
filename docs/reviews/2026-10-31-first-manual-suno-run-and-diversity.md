# First manual-Suno run, local 0042 rollout and catalog diversity — 2026-10-31

Session `plm-handover-audit-277422`, branch `review/r6-live-adapters`. Each item names the
register entry it advances (`2026-10-29-unified-remaining-work-roadmap.md` §5,
`2026-10-28-audit-remediation-register.md`, `2026-10-23-content-quality-audit.md`).
Nothing here is a production or audience result: no song has been imported, rendered or
published yet.

## Owner decisions recorded

| Decision | Effect |
| --- | --- |
| D10 "PLM gives me prompts" (manual Suno), Pro plan, account on file, model v6 | `.env` (git-ignored): manual inbox `data/suno-inbox`, music provider real, `music.import` LIVE, grant to 2026-11-30; ledger: `music.import` rate 0 USD, **global ceiling 0 USD** to 2026-11-30 (only free writes pass) |
| "Back up, then migrate" | Local dev DB `plm` migrated 0039 → 0042 (below) |
| 23 songs, not 92 | `PLM_EXPERIMENTS_MANUAL_SUPPLY=true`: control package only, one candidate per track |
| Per-song length need not be watched | Manual supply declares 90–300 s per track; P6 still fits the playlist to 45–75 min |
| Styles text must carry the whole brief | Prompt sheets give every prompt descriptor as one Styles text |
| Vary production, hook, structure (and arm cooldown) — "do both" | Album contrast v4; P2 cooldown/decay fix |
| "Clear" the fake P2 learning | Two fabricated reward rows removed (below) |

## U3 / U4 — local 0040–0042 rollout (B02, B03, B04)

`tests/support/migration_local_0042_rollout.py` (commits `20e37fc`…`c7ee078`; `migrations/env.py`
accepts one extra approved step). Prepare: full `pg_dump` of `plm`, restore into an isolated
PostgreSQL 16 container, every one of 107 tables (22,929 rows) matched the source, clone
upgraded to 0042, every pre-existing column of every table unchanged, re-upgrade idempotent.
Apply: one transaction through the approved in-process Alembic connection; the result equals
the upgraded clone and pre-existing data is unchanged. Retained backup:
`data/migration_backups/0042_local_plm_20261031T042152Z_a90e7dab5c6e` (12,143,812 bytes,
sha256 `1a8889b5…`; host only, not off-site). Two earlier folders from failed prepares are
marked `UNVERIFIED_DO_NOT_RESTORE`. `plm doctor`: 16 PASS. Scope: local dev DB only; no
other environment, no off-site copy, no WAL/whole-host recovery.

## U11 — first manual-Suno run (D10)

P2 cycle → pipeline on the compose stack held at `GENERATING` with
`manual_audio_awaited` as designed, after P4 wrote one prompt sheet per track. Runs
`169dce95` (92 songs), `c04dedcb`, `28ccb32b`, `be743454`, `9edd6b44` were cancelled as the
fixes below landed; their sheets are under `data/suno-inbox-archive/`. Live run:
`3f7c2ff7-40e3-5bee-9184-148f0cd70a08`, 23 sheets. Not yet shown: a real WAV through import,
P5, P6, the hold being cleared on Temporal.

## D10 changed — Google Flow Music (Lyria 3.5) by hand instead of Suno

Owner: Suno allows 20 downloads a month, so manual Suno cannot supply one 23-song album
("it's a no-go"); the owner makes the songs by hand in Flow Music (Google AI Pro includes
Flow Music's Plus plan: about 2,000 songs a month, free WAV/MP3/M4A downloads, "Commercial
use rights" per support.google.com/googleone/answer/14534406). Flow Music has no official
API; the only automated route to the same model is the paid Gemini API (`lyria-3.5`,
$0.08 a song), not chosen. A third-party wrapper of the owner's account (useapi.net) was
rejected as unofficial.

Evidence for the choice, one brief (`trk_02fc8adf5c275c54`) on three services, files in
`output_media/` (owner-downloaded): Suno 198 s, Lyria 169 s, Stable Audio 360 s.
`scripts/probe_song_repetition.py` (P5 plan §24, `6d1ae5b`, 30 s windows): Stable Audio's
most alike windows are all 120 s apart (rhythm and pitch-motion up to 0.99), a repeating
2-minute cycle; Suno and Lyria show no such cycle. Uncalibrated proxies; the owner's
listening is the quality judgement.

`554602a`: `FlowMusicManualAdapter` (`services/music_factory/providers/flow_music_manual.py`)
imports owner-made songs exactly like manual Suno, as provider `lyria`, model
`flow-music-web-manual`, with Google's terms (only a declared `google_ai_pro` plan and an
account yield terms; Flow Music's own terms page was not found). `PLM_FLOW_MUSIC_MANUAL_INBOX`
selects it over the Suno inbox; P3 allows `lyria`. Not yet shown: the live run switched to
Flow Music sheets, a real Flow Music WAV through import, P5, P6.

## U7 / B06 and audit "creative quality and differentiation"

The 2026-10-23 audit traced the sameness of the first albums to one sonic universe (warm
tape texture, Rhodes, rain, riff over a loop), no energy variation or style budget, and P2
narrowing to the same lofi arm. Reading the first run's 23 sheets showed the same pattern
still present: 15 tracks named vinyl crackle or the kick as the lead; every track F major
with three instruments, tape production, riff hook, loop structure, low energy.

* `0aa1cb3` prompt sheets: one Styles text with every prompt descriptor.
* `77ba200` album contrast v3: the lead is always melodic.
* `03968d5` album contrast v4 (lofi palette): per track key (8), lead (6), bass, drums
  (some none), colour, production (3), hook (3), structure (~30 % ambient) and energy
  (~20 % medium) rotate with no two dimensions in lockstep; only vinyl crackle stays on
  every track; rain removed; a dimension the experiment tests is never varied.
* `a81f7b3` ambient tracks get a calm groove and a soft kit or none.

Checked on the live run's 23 sheets: balanced counts, no ambient/busy-groove or
medium-energy/ambient conflicts. **Not proven:** that Suno follows the brief, or that the
songs are measurably distinct (audio embeddings / listening review, QA-LISTEN).

## P2-01 / RC3 — fabricated rewards and the arm narrowing

* `1a5049d`: every selection recorded `cooldown_until=None`, so the policy's 72 h cooldown
  never applied; the stored exposure score only grew, so the fatigue penalty stayed at its
  maximum after four picks. An AUTONOMOUS pick now sets the cooldown; exposure decays from
  the last selection.
* P2's only learning was two `lofi_study_focus` reward rows written 2026-10-19 by
  `scripts/autonomous_loop_worker.py` / `scripts/run_phase_p9_closed_loop.py`
  (`exp_midnight_rhodes_rain_01`, `exp_continuous_cycle_01`): every metric 0, marked mature
  28 d on the day of the experiment, scored 0.5. They made lofi the only arm with measured
  evidence, so with one slot the exploit quota picked it in every cycle. With owner
  approval they, their two results and two update-log rows were deleted and lofi's
  posterior reset to its prior (backup of schema `ar`:
  `data/backups/ar_before_clear_20261031T053134Z/ar.pgdump`, sha256 `b6a813b1…`). No arm
  now holds learned evidence. `scripts/autonomous_loop_worker.py` was already disabled;
  `scripts/run_phase_p9_closed_loop.py` was still runnable and is now disabled too (it
  refuses before any call; `tests/unit/test_prototype_cycles_disabled.py`).
* **Open: 36 of P1's 77 arms cannot be produced.** 23 have no genre (P3 refuses
  `genre_unspecified`), including P1's nine highest-scoring arms; 12 synthwave and 1
  deep-house arms have no P3 template; P3's house and jazz templates have no P1 arm. P2 does
  not know which arms P3 can produce, so with the fake evidence gone its next pick is likely
  to fail at P3. This needs an owner decision (filter in P2, map genre-less arms in P3, or
  add templates) before the next album.

## Producible arms (owner: "Map them in P3")

Running P3's genome build over all 77 P1 arms (as P2 briefs them) found only 24 producible:
23 named no genre (`genre_unspecified`), 13 were synthwave/deep house without a template,
14 asked for `gt_75m` (unknown bucket) and 3 for `10_45m` (outside the 45–75 min policy).

* Genre (templates v4): a brief with no genre takes it from its first use case
  (`brief_genre_inference` in `genome_templates.json`; none: an open list of seven genres),
  picked by a stable hash of the directive id so repeated arms spread across genres; the
  experiment records `genre_inferred:<genre>:use_case=<case>`. New `synthwave_neon`
  template; `deep_house` is an alias of `house`. A library without the table still refuses.
* Duration (owner: "Make at 45-75 min", ADR-0036 amended): an unknown or out-of-policy
  bucket gets the policy range and the experiment records
  `duration_bucket_mapped:<bucket>->policy`.
* Result: all 77 arms build (lofi 25, synthwave 14, ambient 13, R&B 10, city pop 10,
  jazz 3, house 2), up from 24.
* Album contrast v5 (owner: "add palettes"): every template genre (lofi, ambient,
  synthwave, jazz, city pop, R&B, house) has a palette; production, hook and structure
  values come from the genre's template alternatives. House keeps a four-on-the-floor kick
  and its own grooves; ambient stays drumless or soft and keeps `very_low` energy; synthwave
  ambient tracks get soft drums. A test checks every genre for six or more keys, melodic
  leads, no two dimensions in lockstep and no lifted calm track.
* P6 visual worlds (owner: "fix P6"): P3 sent each package's `visual_world` but P6 drew
  every background and waveform from the brand palette and thumbnail A always drew rain;
  P3 also stored the same indigo/cyan/rain `palette_guide` for every album (read by
  nothing; now dropped). `media_factory/visuals/worlds.py` (`p6-worlds-v1`) gives all 22
  worlds the templates name a sky gradient, waveform colour (>= 3:1 contrast on its sky),
  light, weather (rain only for rainy worlds; snow, stars, mist) and skyline (city, hills,
  waves, trees, room, road). The visual plan records `visual_world.known`; an unknown world
  keeps the brand look. Thumbnail layout `thumb-layout-v4`. Checked by eye on six worlds.
* Plan-level only: whether Suno follows these briefs is unproven; no video has been
  rendered with the new worlds on the compose stack yet.

## Verification

Full non-integration suite after the last code change: 3,430 passed, 1 skipped; Ruff and
mypy (`packages services`, 466 files) clean. CI on the pushed commits not checked here.
