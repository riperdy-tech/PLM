"""Playlist sequencing: beam search over key, tempo, energy and vocal density (P6 §9, R4).

P6 does not re-decide which recordings may be released (P5 did). From the cleared pool
it chooses an ordered subset whose timeline lands inside the declared range, minimising a
declared cost.

**Cost.** Every term is in ``[0, 1]`` and is multiplied by its declared weight
(:attr:`SequencingPolicy.weights`):

* ``key``: Camelot-wheel distance between consecutive tracks (:func:`camelot_cost`);
* ``bpm``: relative tempo distance, half/double time counted as equal, saturating at
  :attr:`SequencingPolicy.bpm_full_penalty_ratio`;
* ``energy_flow``: the jump from one track's outro energy to the next one's intro energy;
* ``energy_arc``: each track's mean energy against the declared target curve
  (:attr:`SequencingPolicy.energy_arc`) at the track's midpoint on the timeline;
* ``vocal``: the change in vocal density between consecutive tracks.

A sequence's score is its mean weighted cost per scored term, so playlists of different
lengths compare fairly.

**Unknown features are scored as worst case** (``unknown_rule = "worst_case"``): any
term that needs a feature one of its tracks lacks costs the full ``1.0`` of that term.
Unknown is never ``0``, and a track is never excluded for an unknown feature: a cleared
track without a measurable key (ambient, atonal) is still eligible, it just costs the
most wherever it sits. The sequence report lists every unknown with its reason.

**Search.** Beam search over partial sequences (width
:attr:`SequencingPolicy.beam_width`). A partial sequence may not exceed the range
maximum; a sequence whose timeline lies inside ``[min_seconds, max_seconds]`` is
complete. At most :attr:`SequencingPolicy.max_candidates_per_track_spec` candidates of
one P3 track spec are used (alternate takes of one song are not two songs). Ties, in
pruning and in the final choice, are broken by the sequence of candidate ids, so the
result never depends on input order.

**Duration (R5, owner decision 2026-10-15).** The playlist's length is the range P3's
playlist spec declares (``duration_range_seconds``, e.g. 2700–4500 s for ``45_75m``); P6
keeps no tolerance of its own. Among the complete sequences the final choice adds
:attr:`SequencingPolicy.duration_target_weight` × the timeline's distance from the
range midpoint (normalised by the half-width) to the sequencing cost, so the sequencer
prefers the midpoint without giving up a much better order for it. The energy arc is
laid over the midpoint. Refusals:

* ``playlist_supply_short``: every eligible track together (one per track spec) is
  shorter than the range minimum. The refusal carries ``missing_seconds`` (minimum minus
  what is available) so the caller can top the package up with P3 and retry.
* ``playlist_range_unmet``: there is enough audio, but no ordering lands inside the range
  (for example, every combination overshoots the maximum).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from types import MappingProxyType
from typing import Any, Final

from plm_contracts.seams.c3 import DurationRangeSeconds

from ..audio.features import TrackFeatures
from ..errors import MediaAssemblyError

__all__ = [
    "DEFAULT_SEQUENCING_POLICY",
    "PLAYLIST_RANGE_UNMET",
    "PLAYLIST_SUPPLY_SHORT",
    "SequenceItem",
    "SequenceResult",
    "SequencingPolicy",
    "camelot_cost",
    "evaluate_order",
    "range_midpoint",
    "sequence_tracks",
    "timeline_duration",
]

PLAYLIST_SUPPLY_SHORT: Final[str] = "playlist_supply_short"
PLAYLIST_RANGE_UNMET: Final[str] = "playlist_range_unmet"
_TIE_DECIMALS: Final[int] = 9


@dataclass(frozen=True, slots=True)
class SequencingPolicy:
    """Declared sequencing weights and search parameters (versioned data)."""

    version: str = "seq-beam-v2"
    beam_width: int = 16
    weights: Mapping[str, float] = field(default_factory=lambda: MappingProxyType({
        "key": 0.30, "bpm": 0.25, "energy_flow": 0.15, "energy_arc": 0.20, "vocal": 0.10,
    }))
    bpm_full_penalty_ratio: float = 0.12
    energy_arc: tuple[tuple[float, float], ...] = ((0.0, 0.45), (0.6, 0.85), (1.0, 0.5))
    """Target mean energy (0..1) at relative timeline positions; linear in between."""
    duration_target_weight: float = 0.5
    """Weight of the timeline's normalised distance from the range midpoint (0..1)."""
    max_candidates_per_track_spec: int = 1
    unknown_rule: str = "worst_case"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "beam_width": self.beam_width,
            "weights": dict(sorted(self.weights.items())),
            "bpm_full_penalty_ratio": self.bpm_full_penalty_ratio,
            "energy_arc": [list(p) for p in self.energy_arc],
            "duration_target_weight": self.duration_target_weight,
            "max_candidates_per_track_spec": self.max_candidates_per_track_spec,
            "unknown_rule": self.unknown_rule,
        }


DEFAULT_SEQUENCING_POLICY: Final = SequencingPolicy()


@dataclass(frozen=True, slots=True)
class SequenceItem:
    """One cleared track offered to the sequencer."""

    candidate_id: str
    track_spec_id: str
    duration_seconds: float
    features: TrackFeatures


@dataclass(frozen=True, slots=True)
class SequenceResult:
    order: tuple[str, ...]
    """Candidate ids in playlist order."""
    score: float
    """Mean weighted cost per scored term (lower is better)."""
    timeline_seconds: float
    window: tuple[float, float]
    """The declared range ``(min_seconds, max_seconds)`` the timeline lies in."""
    target_seconds: float
    """The range midpoint the sequencer preferred."""
    not_selected: tuple[str, ...]
    report: dict[str, Any]


def _parse_camelot(code: str) -> tuple[int, str]:
    return int(code[:-1]), code[-1]


def camelot_cost(a: str | None, b: str | None) -> float:
    """Harmonic distance on the Camelot wheel, 0 (same key) .. 1 (clash); unknown is 1."""
    if a is None or b is None:
        return 1.0
    na, la = _parse_camelot(a)
    nb, lb = _parse_camelot(b)
    step = min((na - nb) % 12, (nb - na) % 12)
    if la == lb:
        return {0: 0.0, 1: 0.1, 2: 0.45}.get(step, 1.0)
    return {0: 0.15, 1: 0.6}.get(step, 1.0)


def _bpm_cost(a: float | None, b: float | None, policy: SequencingPolicy) -> float:
    if a is None or b is None:
        return 1.0
    ratio = min(abs(a - b) / min(a, b), abs(2 * a - b) / min(2 * a, b),
                abs(a - 2 * b) / min(a, 2 * b))
    return min(1.0, ratio / policy.bpm_full_penalty_ratio)


def _diff_cost(a: float | None, b: float | None) -> float:
    if a is None or b is None:
        return 1.0
    return min(1.0, abs(a - b))


def _arc_target(position: float, policy: SequencingPolicy) -> float:
    points = policy.energy_arc
    position = min(1.0, max(0.0, position))
    for (x0, y0), (x1, y1) in pairwise(points):
        if x0 <= position <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (position - x0) / (x1 - x0)
    return points[-1][1]


def _transition_terms(a: TrackFeatures, b: TrackFeatures,
                      policy: SequencingPolicy) -> dict[str, float]:
    return {
        "key": camelot_cost(a.camelot, b.camelot),
        "bpm": _bpm_cost(a.bpm, b.bpm, policy),
        "energy_flow": _diff_cost(a.energy_out, b.energy_in),
        "vocal": _diff_cost(a.vocal_density, b.vocal_density),
    }


def _arc_term(features: TrackFeatures, position: float, policy: SequencingPolicy) -> float:
    if features.energy_mean is None:
        return 1.0
    return min(1.0, abs(features.energy_mean - _arc_target(position, policy)))


def _weighted(terms: Mapping[str, float], policy: SequencingPolicy) -> float:
    return sum(policy.weights.get(name, 0.0) * value for name, value in terms.items())


def timeline_duration(durations: Sequence[float], transition_offset_seconds: float) -> float:
    """Length of a timeline: track durations plus the signed offset between tracks
    (negative for a crossfade overlap, positive for a silence gap)."""
    if not durations:
        return 0.0
    return sum(durations) + transition_offset_seconds * (len(durations) - 1)


@dataclass(frozen=True, slots=True)
class _State:
    order: tuple[int, ...]
    cost: float
    terms: int
    elapsed: float

    def mean(self) -> float:
        return self.cost / self.terms if self.terms else 0.0


def _rank_key(state: _State, ids: Sequence[str]) -> tuple[float, tuple[str, ...]]:
    return round(state.mean(), _TIE_DECIMALS), tuple(ids[i] for i in state.order)


def range_midpoint(duration_range: DurationRangeSeconds) -> float:
    """The planning target of a declared range: its midpoint."""
    return (duration_range.min_seconds + duration_range.max_seconds) / 2


def _target_distance(elapsed: float, duration_range: DurationRangeSeconds) -> float:
    half = (duration_range.max_seconds - duration_range.min_seconds) / 2
    distance = abs(elapsed - range_midpoint(duration_range))
    if half <= 0:
        return 0.0 if distance == 0 else 1.0
    return min(1.0, distance / half)


def evaluate_order(items: Sequence[SequenceItem], order: Sequence[str], *,
                   target_duration_seconds: float, transition_offset_seconds: float,
                   policy: SequencingPolicy = DEFAULT_SEQUENCING_POLICY) -> float:
    """The ordering score :func:`sequence_tracks` assigns to an order (lower is better),
    with the energy arc laid over ``target_duration_seconds``."""
    arc_seconds = target_duration_seconds
    by_id = {item.candidate_id: item for item in items}
    cost, terms, elapsed = 0.0, 0, 0.0
    previous: SequenceItem | None = None
    for candidate_id in order:
        item = by_id[candidate_id]
        start = elapsed + (transition_offset_seconds if previous is not None else 0.0)
        position = (start + item.duration_seconds / 2) / arc_seconds
        cost += policy.weights.get("energy_arc", 0.0) * _arc_term(item.features, position, policy)
        terms += 1
        if previous is not None:
            cost += _weighted(_transition_terms(previous.features, item.features, policy),
                              policy)
            terms += 1
        elapsed = start + item.duration_seconds
        previous = item
    return cost / terms if terms else 0.0


def sequence_tracks(items: Sequence[SequenceItem], *, duration_range: DurationRangeSeconds,
                    transition_offset_seconds: float,
                    policy: SequencingPolicy = DEFAULT_SEQUENCING_POLICY) -> SequenceResult:
    """Choose and order tracks to land inside the declared range at minimum declared cost,
    preferring the range midpoint."""
    if policy.unknown_rule != "worst_case":
        raise ValueError(f"unsupported unknown rule {policy.unknown_rule!r}")
    ranked = sorted(items, key=lambda item: item.candidate_id)
    ids = [item.candidate_id for item in ranked]
    if len(set(ids)) != len(ids):
        raise MediaAssemblyError("duplicate_track", "a candidate appears twice in the pool")
    minimum = float(duration_range.min_seconds)
    maximum = float(duration_range.max_seconds)
    arc_seconds = range_midpoint(duration_range)

    per_spec: dict[str, list[float]] = {}
    for item in ranked:
        per_spec.setdefault(item.track_spec_id, []).append(item.duration_seconds)
    longest = sorted(
        (d for durations in per_spec.values()
         for d in sorted(durations, reverse=True)[:policy.max_candidates_per_track_spec]),
        reverse=True,
    )
    reachable = timeline_duration(longest, transition_offset_seconds)
    if reachable < minimum:
        missing = math.ceil(minimum - reachable)
        raise MediaAssemblyError(
            PLAYLIST_SUPPLY_SHORT,
            f"all eligible tracks together ({len(longest)} track specs) give "
            f"{reachable:.1f}s; the declared range is [{minimum:.0f}s, {maximum:.0f}s], so "
            f"{missing}s are missing",
            ["duration_range_seconds.min_seconds"],
            facts={
                "missing_seconds": missing,
                "available_seconds": round(reachable, 3),
                "min_seconds": duration_range.min_seconds,
                "max_seconds": duration_range.max_seconds,
                "target_seconds": round(arc_seconds, 3),
                "eligible_track_specs": len(longest),
                "transition_offset_seconds": transition_offset_seconds,
            },
        )

    arc_weight = policy.weights.get("energy_arc", 0.0)
    beams = [_State(order=(), cost=0.0, terms=0, elapsed=0.0)]
    complete: list[_State] = []
    for _ in range(len(ranked)):
        expansions: list[_State] = []
        for state in beams:
            used_specs: dict[str, int] = {}
            for i in state.order:
                spec = ranked[i].track_spec_id
                used_specs[spec] = used_specs.get(spec, 0) + 1
            for i, item in enumerate(ranked):
                if i in state.order:
                    continue
                if used_specs.get(item.track_spec_id, 0) >= policy.max_candidates_per_track_spec:
                    continue
                start = state.elapsed + (transition_offset_seconds if state.order else 0.0)
                end = start + item.duration_seconds
                if end > maximum:
                    continue
                position = (start + item.duration_seconds / 2) / arc_seconds
                cost = state.cost + arc_weight * _arc_term(item.features, position, policy)
                terms = state.terms + 1
                if state.order:
                    previous = ranked[state.order[-1]]
                    cost += _weighted(
                        _transition_terms(previous.features, item.features, policy), policy)
                    terms += 1
                expansions.append(_State(order=(*state.order, i), cost=cost, terms=terms,
                                         elapsed=end))
        if not expansions:
            break
        complete.extend(s for s in expansions if s.elapsed >= minimum)
        expansions.sort(key=lambda s: _rank_key(s, ids))
        beams = expansions[:policy.beam_width]

    if not complete:
        raise MediaAssemblyError(
            PLAYLIST_RANGE_UNMET,
            f"no ordering of the eligible tracks fits [{minimum:.1f}s, {maximum:.1f}s]",
            ["duration_range_seconds"],
        )

    def final_key(state: _State) -> tuple[float, tuple[str, ...]]:
        score = state.mean() + policy.duration_target_weight * _target_distance(
            state.elapsed, duration_range)
        return round(score, _TIE_DECIMALS), tuple(ids[i] for i in state.order)

    best = min(complete, key=final_key)
    order = tuple(ids[i] for i in best.order)
    chosen = set(order)
    transitions = [
        {"from": ranked[a].candidate_id, "to": ranked[b].candidate_id,
         **{k: round(v, 4) for k, v in
            _transition_terms(ranked[a].features, ranked[b].features, policy).items()}}
        for a, b in pairwise(best.order)
    ]
    report = {
        "algorithm": "beam_search",
        "policy": policy.to_dict(),
        "score": round(best.mean(), 6),
        "input_order_score": None if not set(ids) <= chosen else round(evaluate_order(
            items, [item.candidate_id for item in items],
            target_duration_seconds=arc_seconds,
            transition_offset_seconds=transition_offset_seconds, policy=policy), 6),
        "window_seconds": [round(minimum, 3), round(maximum, 3)],
        "target_seconds": round(arc_seconds, 3),
        "target_distance": round(_target_distance(best.elapsed, duration_range), 6),
        "timeline_seconds": round(best.elapsed, 3),
        "order": list(order),
        "not_selected": [i for i in ids if i not in chosen],
        "transitions": transitions,
        "features": [item.features.to_report() for item in ranked],
        "unknown_features": {
            item.candidate_id: dict(sorted(item.features.unknown_reasons.items()))
            for item in ranked if item.features.unknown_reasons
        },
    }
    return SequenceResult(
        order=order, score=best.mean(), timeline_seconds=best.elapsed,
        window=(minimum, maximum), target_seconds=arc_seconds,
        not_selected=tuple(i for i in ids if i not in chosen),
        report=report,
    )
