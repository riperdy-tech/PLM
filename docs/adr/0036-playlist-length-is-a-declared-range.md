# ADR-0036: A playlist's length is a declared range that P3 plans supply for and P6 enforces

- Status: `accepted`
- Date: `2026-10-15`
- Ruling: owner decision 2026-10-15 (playlist duration); remediation stage R5
- Seams: P3 package API (`playlist_spec`, read by the pipeline for P6), P6
  `POST /media/packages/assemble` (the C5 consumer section of the registry)
- Supersedes / superseded by: amends the registry's "`required_duration_minutes` comes
  from the playlist spec" (C5, R7)

## Context

`docs/STATUS.md` ("Playlist duration feasibility"): P3 materialized a fixed 5 tracks of
about 2.5 min for a 60 min playlist (`track_count=5`, `total_duration_minutes=60`, both
request defaults), and P6 accepted only `required × (1 ± 5 %)`, a tolerance P6 invented.
A real pipeline run therefore failed `required_duration_unreachable`, and nothing could
ask P3 for more tracks.

The owner decided (2026-10-15): a playlist video's length is a declared **range**,
accepted anywhere within 45–75 min (the directive bucket `45_75m`); the target is only
the planning midpoint.

## Decision

- **The range is a genome fact.** `genome_templates.json` declares
  `playlist_duration_buckets` (`45_75m` = 2700–4500 s, `10_45m` = 600–2700 s, target =
  midpoint). A directive whose brief names a bucket gets that range as the genome's
  `playlist_duration`; without a bucket the genre template's declared range applies. A
  bucket the table does not declare (`gt_75m`, which has no maximum) is refused
  (`playlist_duration_bucket_unknown`); a template without a playlist range fails to load.
  Nothing defaults.
- **P3's playlist spec declares it** (`duration_range_seconds {min_seconds, max_seconds}`,
  shared model `DurationRangeSeconds`), with `target_duration_seconds` (midpoint) and the
  supply planned for it: `base_track_count = ceil(min_seconds / planned_track_seconds)`
  (the track spec's `requested_duration`), `spare_track_count = ceil(base × spare_ratio)`
  for P5 rejections, and `max_extension_track_count = ceil(track_count ×
  max_extension_ratio)`. The ratios are versioned data
  (`services/experiments/materialization/supply_policy.json`, `p3-supply-v1`: 0.25 and
  0.5). `total_duration_minutes` and the `track_count` / `total_duration_minutes`
  materialize inputs are removed. The package budget is split over planned plus
  extension specs, so the directive's ceiling holds after every top-up.
- **Top-ups.** `POST /experiments/v1/packages/{id}/extend {count, idempotency_key}` adds
  `count` specs (copies of the package's spec under new deterministic ids, role
  `secondary`). The key replays the same specs; the key with another count is 409
  `idempotency_key_reused`; past the bound is 409 `package_extension_limit_exceeded`.
- **P6 enforces the range.** The assemble request takes `duration_range_seconds` instead of
  `required_duration_seconds`; P6's 5 % tolerance is removed. The sequencer completes
  inside the range and prefers the midpoint (declared weight `duration_target_weight`,
  policy `seq-beam-v2`). Too little cleared supply is 422 `playlist_supply_short` with
  `detail.facts.missing_seconds`; enough audio that no ordering fits is
  `playlist_range_unmet`. A short supply is topped up and retried, never published short.

## Consequences

- Packages stored before migration `0034_experiments_playlist_range` have no range and are
  refused on load (409 `legacy_package_without_duration_range`); re-materialize them.
- P6's `PlaylistSession.requested_duration_seconds` now records the range midpoint, and
  the sequence report records the range as `window_seconds`.
- The pipeline's assemble step reads `playlist_spec.duration_range_seconds` and, on
  `playlist_supply_short`, calls `extend` sized from `missing_seconds`.

## Alternatives considered

- *Keep an exact length and widen P6's tolerance.* Rejected: the tolerance is still a
  constant P6 invents, and the owner declared a range.
- *Plan to the midpoint instead of the minimum.* Rejected for the base count: the range
  minimum is the requirement; spares and top-ups absorb rejections, and the sequencer's
  midpoint preference uses whatever extra supply clears.
