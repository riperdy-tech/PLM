# tests/unit/market/test_canonical_text.py
from services.market_intelligence.normalization.canonical import (
    channel_canonical_url,
    is_valid_channel_id,
    is_valid_video_id,
    normalize_text,
    text_fingerprint,
    video_canonical_url,
)


def test_normalization_folds_case_unicode_and_punctuation_but_keeps_words() -> None:
    assert normalize_text("2AM Seoul 🌙") == "2am seoul"
    assert normalize_text("  2am   seoul  ") == "2am seoul"
    assert normalize_text("Ｒ＆Ｂ — Late-Night") == "r&b late-night"
    assert normalize_text("lofi/study") == "lofi study"
    assert "rnb" in normalize_text("R’n’B")


def test_fingerprint_is_stable_across_the_two_spellings_the_spec_gives() -> None:
    assert text_fingerprint("2AM Seoul 🌙") == text_fingerprint("2am seoul")
    assert text_fingerprint("night drive") != text_fingerprint("night drives")


def test_provider_ids_are_validated_with_the_documented_shapes() -> None:
    assert is_valid_video_id("vid000000001")
    assert not is_valid_video_id("vid1")  # malformed ids are quarantined, never repaired
    assert is_valid_channel_id("UC" + "a" * 22)
    assert not is_valid_channel_id("UCaaaaaaaaaaaaaaaaaaaaa")


def test_canonical_urls_are_built_for_youtube_entities() -> None:
    assert video_canonical_url("vid000000001") == "https://www.youtube.com/watch?v=vid000000001"
    assert channel_canonical_url("UC" + "a" * 22).endswith("/channel/UC" + "a" * 22)
