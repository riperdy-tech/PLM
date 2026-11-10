# ADR-0045: Adopting the playlist-channel growth spec

- Status: accepted (owner, 2026-11-03), in progress
- Source: `docs/Playlist Channel Growth Spec for an Automated YouTube Pipeline (2025–2026).md`
- Related: ADR-0044 (channel identity), ADR-0036 (playlist length)

## Context

The owner asked for the growth spec to be implemented. The spec was written around a
late-night café/city jazz channel aimed at Korea and Taiwan. This channel's identity is
locked by ADR-0044 (instrumental deep-focus electronic, English-first). So the spec's
principles apply and its examples do not.

## Decisions

1. **Identity and length stay as ADR-0044 sets them.** The spec's Seoul/Taipei, café and
   jazz examples are not used. Videos stay 20–30 minutes (the owner makes songs by hand),
   with 1–2 h session compilations. The spec itself says its 60–120 min default is a
   heuristic.
2. **Localized metadata (owner, 2026-11-03):** English default plus German, Dutch,
   Swedish, Korean, Traditional Chinese and Japanese. Each language is written in its own
   search phrasing, not translated word for word; a native speaker should review each
   language's templates once.
3. **Build order:** the variation check; three scenario-first titles and a Test & Compare
   kit (launched by hand: no API); the description layout and wider search phrases; the
   localizations; series and playlists; the watch-minutes-per-1,000-impressions score and
   the daily decision rules; strongest track first, 2–4 s crossfades, moving visuals.
   Shorts and 24/7 live wait for 20 or more videos, as the spec says.
4. **Variation check (rank 1, built 2026-11-03):** P6 refuses, before rendering, a package
   whose concept repeats one of the brand's last 10 fingerprinted packages, whose visual
   world one of the last 3 used, or whose title differs from an earlier one by one swapped
   word (`upload-variation-v1`, `services/media_factory/playlist/variation.py`). Reused
   recordings were already refused outright (stricter than the spec's 20% overlap). P3
   moves a brief off its template's visual world when one of the genre's last 3 videos used
   it, so the check only fires on a brief that names a recent world.
   *Amended 2026-11-10 (Gate R5):* the world rule skips a package of the same experiment
   (the fingerprint records `experiment_id`): an experiment's treatments differ only in
   its factor and share the base genome's world, so a second treatment was refused after
   its songs were made. Concept and title rules still apply to siblings. P3 also counts
   the worlds of experiments designed but not yet materialized, and designs one at a
   time, so runs started together by one P2 cycle take different worlds.
5. **Titles and description (ranks 3 and 5.1, built 2026-11-03):** P3's title pattern
   (`{mood} {genre} for {use_case}`) gave focus videos raw codes as their concept
   ("focused focus_electronic for study_focus") and the same concept every time, which the
   variation check would have refused from the second video. P6 now builds three titles
   per video from a scene hook that describes its visual world, a feeling hook, and search
   words seen in YouTube's autocomplete (`metadata/packaging.py`): scenario first
   (`meta_a_scenario`, uploaded by default), keyword first, feeling first. Hooks are at
   most 40 characters and are not reused while the brand's last 10 packages hold
   unused ones. The description follows the spec's layout (hook line, secondary search
   line, tracklist, music and visual origin, AI notice, three hashtags) and the tags are
   eight autocomplete phrases plus the brand. `plm youtube test-kit` (and the publish
   output) lists the three titles and thumbnails to start in Studio's Test & Compare
   within 24 h; P7 has no metadata-update path, so it never stops a running test.
6. **Localized metadata (built 2026-11-03):** every candidate carries
   `localizations` for the six languages (`metadata/localization.py`; C6 hop 1 schema
   1.1). Each of the 21 scenes is written in each language, with a local search phrase
   (작업용 BGM, 作業用BGM, 讀書音樂, Musik zum Konzentrieren, concentratiemuziek, fokusmusik).
   P7 stores them on the manifest (`localizations_json`, migration
   `0043_youtube_localizations`, code-only like 0039–0042; the owner approved the local
   rollout on 2026-11-03, `tests/support/migration_local_0043_rollout.py`) and sends them
   with `part=localizations`. Unverified until the first live upload: the
   `zh-Hant` code (YouTube may want `zh-TW`) and `defaultAudioLanguage: zxx` for
   instrumentals (left at the channel language for now).

7. **Series (rank 4, built 2026-11-03):** four series, each a pair of visual worlds so it
   keeps one visual motif: Night Desk Sessions (studio, observatory), First Light Focus
   (drafting table, minimal desk), Signal Room (server hall, circuit grid), Blueprint
   Hours (blueprint hall). P6 names the series in each candidate (C6 hop 1 schema 1.2) and
   the description; P7 adds the upload to the series' playlist listed in
   `PLM_YOUTUBE_SERIES_PLAYLISTS_FILE` and links it. P7 does not create playlists: the
   owner creates them once in Studio (plus a "Start Here" playlist, curated by hand from
   the best performers, as the spec's channel-page layout is Studio-only). Series stay
   out of the titles, so a repeated series never makes two titles a one-word template
   fill of each other.

## Limits

- A refused package holds the run for the platform owner (`STEP_PLATFORM_FAULT`), not for
  a production owner, because P6 answers with a generic 422.
- Packages rendered before `upload-variation-v1` carry no fingerprint and are counted,
  not compared.
