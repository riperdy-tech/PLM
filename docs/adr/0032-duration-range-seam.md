# ADR-0032: The spec's duration range travels to P5, and P5 judges against it

- Status: `accepted`
- Date: `2026-10-14`
- Ruling: `R5` (C3 track spec), `R6` (C4 candidate event); remediation stage R2
- Seams: C3 (P3 → P4, schema 2.0), C4 (P4 → P5, schema 2.0)
- Supersedes / superseded by: `—`

## Context

The R0 review (`docs/reviews/2026-10-13-system-review-and-remediation-plan.md`, P4/P5
finding **H**) found that a real P4 → P5 run rejects every mock candidate.

- The genome declares a track-duration range, for example 160–220 s. P3 reduced it to
  one point, `requested_duration` (the target, else the rounded midpoint; R5), and the
  range itself never left P3.
- P4's mock provider clamped audio to 30 s, so a 180 s spec produced 30 s of audio.
- P5's technical evaluator compared the measured duration with the point
  `requested_duration_seconds` and blocked any gap above a 1.0 s tolerance. P5 invented
  that tolerance; no spec declared it.

So every mock candidate for a normal spec was rejected. The e2e test hand-built the P5
manifest to avoid this, which meant no test ran the P4 → P5 handoff through the real
gate. A real provider fares no better. A 200 s track inside a 160–220 s genome is
blocked against a 190 s point, while a track 0.9 s from the point but outside the range
would pass.

## Decision

The allowed duration is a declared fact that travels on the seam. P5 judges against
that fact and keeps no tolerance of its own.

- **C3 (schema 2.0).** `TrackSpecPayload` gains the required
  `duration_range: {min_seconds, max_seconds}` (inclusive, integer seconds, shared model
  `plm_contracts.seams.c3.DurationRangeSeconds`). P3 copies it from the genome's
  `track_duration` as declared; it is not derived. `requested_duration` stays: it is the
  point P4 asks the provider for, and the model refuses a spec whose
  `requested_duration` lies outside `duration_range`.
- **C4 (schema 2.0).** `CandidateReadyPayload` gains the required
  `requested_duration_range`. It is the C3 `duration_range`, which P4 forwards unchanged
  from the stored spec. `requested_duration_seconds` stays as information and must lie
  inside the range.
- **P5.** `validate_technical_audio` takes the range (`allowed_duration`, required).
  A measured duration inside it, bounds included, passes the `duration_in_range` check.
  A duration outside it fails the check and records the blocking finding
  `DURATION_OUT_OF_RANGE`, so the candidate is rejected. The 1.0 s
  `duration_tolerance_seconds` is gone. The evaluation snapshot records the requested
  point and range.
- **Missing range.** A C3 spec or C4 event without the range is refused at the seam with
  `missing_required_field`. An inverted range, or a point outside the range, is refused
  with `invalid_field_value`. Nothing defaults.
- **P4 mock provider.** The mock now synthesises the requested duration with no 30 s cap,
  at 44.1 kHz stereo, the format P5's technical checks expect. To stay fast it computes
  one second of PCM and repeats the bytes. It stays deterministic: the seed comes from
  `stable_seed`, and samples are packed little-endian explicitly.

### Why a major version

ADR-0025 permits only optional fields in a minor bump. Both new fields are required,
because a range that may be absent would have to be defaulted, and a default is exactly
the invented tolerance this ADR removes. So C3 and C4 move to 2.0. Consumers still
accept major 1 by version, as ADR-0025 requires. A 1.x payload is nonetheless refused,
because it lacks the range, and it is refused with a named field rather than a version
error. No 1.x producer remains in the repository.

## Evidence

- `docs/contracts/CONTRACT_REGISTRY.md` C3: P3 §20 names `duration_range`, and R5 mapped
  it to the single `requested_duration`. The range was dropped at that mapping, not
  carried.
- `services/quality_gate/evaluators/technical.py` (before this ADR): it used
  `duration_tolerance_seconds: float = 1.0` and the finding `DURATION_DISCREPANCY`
  against `expected_duration_seconds`.
- `services/music_factory/providers/mock_provider.py` (before this ADR): it clamped
  with `min(float(request.duration_seconds), 30.0)`.

## Consequences

- P3 `materialize_package` emits `duration_range`. P4's `AdaptedTrackSpec` carries it,
  and `build_candidate_ready_event` forwards it.
- A stored P4 plan whose `track_spec_wire` predates this ADR cannot be exported. The
  export fails with `stored_track_spec_invalid` and names `duration_range`. Stored P3
  packages from before this ADR fail to load for the same reason. Both are fail-closed,
  and the remedy is to re-materialise.
- The P5 check type is renamed `duration_match` → `duration_in_range`, and its
  `expected_value` is the range as `"[min, max]"`. The finding code is renamed
  `DURATION_DISCREPANCY` → `DURATION_OUT_OF_RANGE`. Nothing outside P5 read either name.
- Mock runs now produce full-length audio, about 31.8 MB for 180 s, so a mock P4 → P5
  run exercises the real duration gate.
- Real providers that cannot hit an exact length are judged against the genome's range,
  which is the creative tolerance the genome already declares.

## Alternatives considered

- *Keep the point and widen P5's tolerance (for example ±10 %).* Rejected. It is still a
  constant invented in P5, and it disagrees with the genome both ways: it rejects
  in-range tracks and accepts out-of-range ones.
- *P5 fetches the genome or the C3 spec to find the range.* Rejected. Phases talk over
  frozen seams. C4 already carries every fact P5 judges on (ADR-0027), and the range is
  one more such fact.
- *Make the range optional (minor bump) and fall back to the point.* Rejected. The
  fallback would need a tolerance, which brings back the invented constant.
- *Keep the mock short and scale the expected duration in tests.* Rejected. A mock pass
  would then not exercise the gate production runs through.

## Cost if wrong

If a brand needs a tighter tolerance than the genome range, the genome range is the
place to tighten, since it is the declared creative constraint. If P5 should also
enforce a policy bound, such as a platform maximum, it belongs as a separate, named
policy check, not a widened duration tolerance. Either change follows the usual shape:
a new ADR, the registry amendment, and producer and consumer together.
