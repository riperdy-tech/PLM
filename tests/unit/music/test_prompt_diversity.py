"""The template path (no AI) writes clearly varied Suno Styles texts, song to song and
video to video, inside the channel identity (ADR-0044).

Owner, 2026-11-27: the 12 newest real sheets all opened with the same seven terms
(``focus_electronic, dreamy mood, for study focus music, <BPM>, instrumental, no vocals,
key of ...``), one mood per video, tempos only 88-98 and the lead named twice. Suno
weights the opening words most, so each song now opens with its own character (mood,
energy shape, lead, texture) and the genre, purpose and "no vocals" come later.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from experiments.experiments.engine import design_experiment
from experiments.genome.engine import build_genome_from_directive, default_template_library
from experiments.ingestion.p2_adapter import AdaptedDirective, adapt_directive
from experiments.materialization.builder import extend_package, materialize_package
from experiments.validation.engine import promote_to_validated, validate_experiment
from music_factory.domain.models import GenerationRequest
from music_factory.generation.prompts import build_generation_prompt
from music_factory.ingestion.p3_adapter import adapt_track_spec
from music_factory.providers.suno_manual import STYLES_LIMIT, suno_styles
from plm_contracts.seams.c2 import ProductionDirectivePayload

from tests.support.seams import directive_fields

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
FOCUS_MOODS = ("focused", "calm", "dreamy", "hopeful", "uplifting")


def _directive(directive_id: str) -> AdaptedDirective:
    return adapt_directive(ProductionDirectivePayload.build(**directive_fields(
        directive_id=directive_id, allocation_type="exploit", quantity=2,
        brief={"use_case": ["study_focus"], "vocal_mode": "instrumental", "mood": ["dreamy"]},
    )).to_wire())


def _video(directive_id: str, recent: tuple[str, ...], songs: int = 12) -> list[dict[str, Any]]:
    """One video's songs as P4 writes them on the sheet (manual supply: one take each)."""
    directive = _directive(directive_id)
    genome = build_genome_from_directive(directive, recent_subgenres=recent)
    exp = design_experiment(directive, genome, created_at=NOW, control_only=True)
    exp = promote_to_validated(exp, validate_experiment(exp, genome))
    package = materialize_package(exp, exp.treatments[0], genome, created_at=NOW,
                                  candidate_target=1)
    specs = list(package.track_specs)
    if len(specs) < songs:
        specs += extend_package(package, count=songs - len(specs), idempotency_key="more",
                                created_at=NOW)[1]
    out = []
    for spec in specs[:songs]:
        adapted = adapt_track_spec(spec.to_wire())
        compiled = build_generation_prompt(adapted)
        styles = suno_styles(GenerationRequest(
            track_spec_id=spec.track_spec_id, experiment_id=exp.experiment_id,
            prompt=compiled.prompt, vocal_mode="instrumental"))
        out.append({"spec": spec, "styles": styles, "genome": genome,
                    "direction": spec.creative_latitude["track_arrangement"]})
    return out


def _three_videos() -> list[list[dict[str, Any]]]:
    videos: list[list[dict[str, Any]]] = []
    recent: tuple[str, ...] = ()
    for directive_id in ("dir_div_a", "dir_div_b", "dir_div_c"):
        video = _video(directive_id, recent)
        recent = (str(video[0]["genome"].subgenre), *recent)
        videos.append(video)
    return videos


def _terms(styles: str) -> list[str]:
    return [term.strip() for term in styles.split(",")]


def test_songs_of_one_video_open_differently_and_stay_in_identity() -> None:
    video = _video("dir_div_a", ())
    assert len(video) == 12
    openings = [tuple(_terms(song["styles"])[:3]) for song in video]
    assert len(set(openings)) == 12, openings
    assert len({opening[0] for opening in openings}) >= 3
    for song in video:
        styles = song["styles"]
        assert len(styles) <= STYLES_LIMIT
        assert "instrumental, no vocals" in styles
        assert "focus electronic" in styles and "for study focus music" in styles
        assert "_" not in styles
        # genre, purpose and "no vocals" come after the song's own character
        assert not styles.startswith(("focus electronic", "instrumental", "for "))
        head = styles[:120]
        assert "no vocals" not in head and "study" not in head
        assert re.sub(r"\bno vocals\b", "", styles).count("vocal") == 0
        lead = str(song["direction"]["lead_instrument"]).replace("_", " ")
        assert styles.count(lead) == 1, (lead, styles)


def test_one_video_has_distinct_combinations_moods_and_tempos() -> None:
    video = _video("dir_div_a", ())
    combos = [(s["direction"]["lead_instrument"], s["direction"]["texture"],
               s["direction"]["energy_shape"]) for s in video]
    assert len(set(combos)) == len(combos)
    moods = [s["spec"].bounded_preferences.mood for s in video]
    flat = [mood for track in moods for mood in track]
    assert set(flat) <= set(FOCUS_MOODS)
    assert len(set(flat)) >= 3  # not all one mood
    assert Counter(flat).most_common(1)[0][0] == "dreamy"  # weighted to the video's mood
    genome = video[0]["genome"]
    bpms = [s["spec"].bounded_preferences.bpm for s in video]
    assert all(genome.bpm.min_bpm <= bpm <= genome.bpm.max_bpm for bpm in bpms)
    assert all(70 <= bpm <= 108 for bpm in bpms)
    assert len(set(bpms)) >= 6 and max(bpms) - min(bpms) >= 12
    for song, bpm in zip(video, bpms, strict=True):
        assert f"{bpm} BPM" in song["styles"]  # sheet and spec agree (P5 checks the spec)


def test_consecutive_videos_sound_different() -> None:
    videos = _three_videos()
    assert len({video[0]["genome"].subgenre for video in videos}) == 3
    combos = [{(s["direction"]["lead_instrument"], s["direction"]["texture"],
                s["direction"]["energy_shape"]) for s in video} for video in videos]
    assert not (combos[0] & combos[1]) and not (combos[1] & combos[2])
    textures = [{s["direction"]["texture"] for s in video} for video in videos]
    assert textures[0] != textures[1] != textures[2]
    openings = [tuple(_terms(s["styles"])[:3]) for video in videos for s in video]
    assert len(set(openings)) == len(openings) == 36
    ranges = {(v[0]["genome"].bpm.min_bpm, v[0]["genome"].bpm.max_bpm) for v in videos}
    assert len(ranges) == 3


def test_the_diverse_prompts_are_reproducible() -> None:
    assert [s["styles"] for s in _video("dir_div_a", ())] == [
        s["styles"] for s in _video("dir_div_a", ())]


def test_every_sub_style_has_its_own_wider_palette_and_tempo_range() -> None:
    from experiments.materialization.arrangements import _SUBGENRE_PALETTES

    library = default_template_library()
    subs = library.subgenres("focus_electronic")
    for sub in subs:
        palette = _SUBGENRE_PALETTES[sub]
        assert len(palette["lead"]) >= 8 and len(palette["texture"]) >= 7, sub
        assert len(palette["color"]) >= 6 and len(palette["production_style"]) >= 3, sub
        bpm = library.for_subgenre("focus_electronic", sub).genome.bpm
        assert bpm.max_bpm - bpm.min_bpm >= 14, sub
        # P5 (p5-identity-audio-v2): 70-110 +-2, and no tempo whose double or half lands
        # in the held 112-136 zone
        assert 70 <= bpm.min_bpm and bpm.max_bpm <= 108, sub
    assert len({frozenset(_SUBGENRE_PALETTES[s]["texture"]) for s in subs}) == len(subs)


def test_a_spec_without_the_song_character_compiles_exactly_as_before() -> None:
    """Old plans (track arrangements before v9) keep their prompt word for word."""
    spec = _video("dir_div_a", (), songs=1)[0]["spec"]
    wire = spec.to_wire()
    direction = wire["creative_latitude"]["track_arrangement"]
    for key in ("track_mood", "album_moods", "energy_shape", "texture", "scene"):
        direction.pop(key)
    prompt = build_generation_prompt(adapt_track_spec(wire)).prompt
    assert prompt.startswith("FOCUS_ELECTRONIC track. mood: ")
