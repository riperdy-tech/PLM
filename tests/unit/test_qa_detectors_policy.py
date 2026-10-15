"""The protected-artist policy shared by P4 and P5 (R4): one file, one loader."""

from __future__ import annotations

import pytest
from plm_platform.protected_artists import (
    ProtectedArtistPolicyError,
    load_protected_artist_policy,
    normalise_for_screening,
    parse_protected_artist_policy,
)
from quality_gate.evaluators import policy as p5_policy
from quality_gate.evaluators.policy import screen_policy_and_risk

from services.music_factory.generation import prompts as p4_prompts
from tests.support.seams import candidate_event  # noqa: F401  (fixtures share the seam)

POLICY = load_protected_artist_policy()


def test_p4_and_p5_load_the_same_shipped_policy() -> None:
    """Proves: neither phase keeps its own list; both read plm_platform's loader, and the
    old hardcoded P5 list is gone."""
    assert p4_prompts.load_protected_artist_policy is load_protected_artist_policy
    assert p5_policy.load_protected_artist_policy is load_protected_artist_policy
    assert not hasattr(p5_policy, "PROTECTED_ARTIST_NAMES")


@pytest.mark.parametrize(
    "text",
    ["in the style of TAYLOR SWIFT", "Beyoncé-like runs", "drake-style flow", "eminem"],
)
def test_names_match_on_normalised_whole_tokens(text: str) -> None:
    """Proves: case, diacritics and punctuation do not hide a listed name."""
    assert POLICY.screen(text).blocked


@pytest.mark.parametrize("text", ["a mandrake root", "taylor made swiftly", "lofi chill"])
def test_partial_tokens_do_not_match(text: str) -> None:
    """Proves: a name only matches as complete tokens (no substring false positives)."""
    assert not POLICY.screen(text).blocked


def test_names_are_not_assembled_across_separate_texts() -> None:
    """Proves: 'taylor' at the end of the prompt and 'swift' in a tag are not a match."""
    assert not POLICY.screen("cover art by taylor", "swift tempo").blocked


def test_ai_policy_markers_raise_their_own_code() -> None:
    """Proves: a voice-clone request is AI_POLICY_CONFLICT even when no artist is named."""
    result = POLICY.screen("make an AI cover with a voice clone")
    assert result.reason_codes == ("AI_POLICY_CONFLICT",)


def test_a_policy_that_would_screen_nothing_is_refused() -> None:
    """Proves: the loader fails closed on an empty list or a name that normalises away."""
    data = POLICY.model_dump()
    with pytest.raises(ProtectedArtistPolicyError):
        parse_protected_artist_policy({**data, "artists": []})
    with pytest.raises(ProtectedArtistPolicyError):
        parse_protected_artist_policy(
            {**data, "artists": [{"artist_id": "x", "names": ["---"]}]}
        )
    assert normalise_for_screening("  Ed   SHEERAN!! ") == "ed sheeran"


def test_p5_screens_prompt_tags_and_lyrics_as_hard_stop_holds() -> None:
    """Proves: P5 finds a protected artist in the prompt, a genre tag or the lyrics, as a
    non-blocking hold (quarantine with a hard stop), not a rejection or a warning."""
    from datetime import UTC, datetime

    now = datetime(2026, 10, 1, tzinfo=UTC)
    for prompt, tags, lyrics in (
        ("lofi like rihanna", ["lofi"], None),
        ("lofi", ["the weeknd"], None),
        ("lofi", ["lofi"], "and we sang ariana grande songs all night"),
    ):
        res = screen_policy_and_risk("ev", prompt, tags, now=now, lyrics_text=lyrics)
        assert not res.is_compliant
        assert res.hold_codes == ("PROTECTED_ARTIST_REFERENCE_DETECTED",)
        assert all(not f.blocking for f in res.findings)
