"""Localized titles and descriptions (growth spec 2026-11-03, ADR-0045 decision 2).

English stays the default language; YouTube shows a localization only to viewers whose
language it is. The owner chose German, Dutch, Swedish, Korean, Traditional Chinese and
Japanese. Each title leads with that language's own way of searching for focus music (작업용 BGM,
作業用BGM, 讀書音樂, Musik zum Konzentrieren), then the genre and the length, as the
English title does (owner 2026-11-17: no scene line in titles). The scene of every focus
world is written per language for the description; a world without a translation leaves
the languages out rather than mixing English into them.

Written by the pipeline's author, not by native speakers: each language's text should be
read once by a native speaker (ADR-0045).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["LANGUAGES", "localize"]


@dataclass(frozen=True, slots=True)
class _Language:
    head: str
    """The local search phrase the title ends with."""
    minutes: str
    """``{}`` is the number of minutes."""
    hook: str
    """First description line: ``{scene}``, ``{length}``."""
    calm: str
    tracklist: str
    hashtags: str
    separator: str = " – "


LANGUAGES: Final[dict[str, _Language]] = {
    "de": _Language(
        head="Musik zum Konzentrieren, Lernen und Arbeiten", minutes="{} Minuten",
        hook="{scene}: {length} ruhige instrumentale elektronische Musik zum Lernen, "
             "Programmieren und konzentrierten Arbeiten.",
        calm="Keine Texte, kein Gesang, ein sanfter, gleichmäßiger Puls im Hintergrund.",
        tracklist="Titelliste", hashtags="#konzentrationsmusik #lernmusik #focusmusic"),
    "nl": _Language(
        head="Muziek om te focussen, studeren en werken", minutes="{} minuten",
        hook="{scene}: {length} rustige instrumentale elektronische muziek om te "
             "studeren, programmeren en geconcentreerd te werken.",
        calm="Geen tekst, geen zang, een zachte, gelijkmatige puls op de achtergrond.",
        tracklist="Tracklijst", hashtags="#concentratiemuziek #studeermuziek #focusmusic"),
    "sv": _Language(
        head="Fokusmusik för plugg och arbete", minutes="{} minuter",
        hook="{scene}: {length} lugn instrumental elektronisk musik för plugg, "
             "programmering och koncentrerat arbete.",
        calm="Ingen text, ingen sång, en mjuk och jämn puls i bakgrunden.",
        tracklist="Låtlista", hashtags="#fokusmusik #pluggmusik #focusmusic"),
    "ko": _Language(
        head="집중할 때 듣는 음악 | 공부·작업용 BGM", minutes="{}분", separator=" | ",
        hook="{scene}: 공부, 코딩, 몰입 작업을 위한 {length} 차분한 일렉트로닉 연주곡.",
        calm="가사도 보컬도 없이, 배경에 잔잔하게 깔리는 부드러운 비트.",
        tracklist="트랙리스트", hashtags="#집중음악 #공부할때듣는음악 #작업용BGM"),
    "zh-Hant": _Language(
        head="專注音樂・讀書工作用", minutes="{} 分鐘", separator="｜",
        hook="{scene}：{length}平靜的電子器樂，適合讀書、寫程式與專注工作。",
        calm="沒有歌詞、沒有人聲，只有背景裡柔和而穩定的節奏。",
        tracklist="曲目", hashtags="#專注音樂 #讀書音樂 #工作音樂"),
    "ja": _Language(
        head="作業用BGM・集中できる音楽", minutes="{}分", separator="｜",
        hook="{scene}：勉強、プログラミング、集中作業のための{length}の穏やかな"
             "エレクトロニック・インストゥルメンタル。",
        calm="歌詞もボーカルもなく、やわらかく一定のリズムが流れ続けます。",
        tracklist="トラックリスト", hashtags="#作業用BGM #勉強用BGM #集中BGM"),
}
"""YouTube language codes (``zh-Hant``: Traditional Chinese, for Taiwan and Hong Kong)."""

_SCENES: Final[dict[str, dict[str, str]]] = {
    "rainy_night_desk": {
        "de": "Schreibtisch am verregneten Fenster bei Nacht",
        "nl": "Bureau bij een regenachtig raam 's nachts",
        "sv": "Skrivbord vid ett regnigt fönster en natt",
        "ko": "비 오는 밤, 창가의 책상", "zh-Hant": "雨夜窗邊的書桌", "ja": "雨の夜、窓辺の机"},
    "sunrise_home_office": {
        "de": "Homeoffice bei Sonnenaufgang", "nl": "Thuiskantoor bij zonsopgang",
        "sv": "Hemmakontor i soluppgången",
        "ko": "해 뜰 무렵의 홈오피스", "zh-Hant": "日出時的居家辦公室", "ja": "日の出のホームオフィス"},
    "late_night_library": {
        "de": "Stille Bibliothek bei Nacht", "nl": "Stille bibliotheek 's nachts",
        "sv": "Tyst bibliotek en natt",
        "ko": "밤의 조용한 도서관", "zh-Hant": "夜晚安靜的圖書館", "ja": "夜の静かな図書館"},
    "city_apartment_night": {
        "de": "Stadtwohnung über den Lichtern", "nl": "Stadsappartement boven de lichtjes",
        "sv": "Stadslägenhet ovanför ljusen",
        "ko": "도시의 불빛 위, 아파트 책상", "zh-Hant": "城市燈火之上的公寓", "ja": "街の灯りを見下ろす部屋"},
    "snowy_cabin_desk": {
        "de": "Hüttenschreibtisch an einem verschneiten Abend",
        "nl": "Bureau in een hut op een besneeuwde avond",
        "sv": "Skrivbord i en stuga en snöig kväll",
        "ko": "눈 내리는 저녁, 오두막의 책상", "zh-Hant": "雪夜小木屋裡的書桌", "ja": "雪の夜、山小屋の机"},
    "train_window_journey": {
        "de": "Arbeiten am Zugfenster", "nl": "Werken bij een treinraam",
        "sv": "Arbete vid ett tågfönster",
        "ko": "기차 창가에서 일하기", "zh-Hant": "在火車窗邊工作", "ja": "列車の窓辺で作業"},
    "minimal_desk_monolith": {
        "de": "Aufgeräumter, minimalistischer Schreibtisch", "nl": "Strak, minimalistisch bureau",
        "sv": "Rent, minimalistiskt skrivbord",
        "ko": "깔끔한 미니멀 책상", "zh-Hant": "整潔的極簡書桌", "ja": "すっきりしたミニマルな机"},
}
"""Each focus world's scene (:data:`.packaging.SCENES`) per language."""


def localize(visual_world: str | None, *, genre: str, seconds: float, chapter_block: str,
             ) -> dict[str, dict[str, str]]:
    """``{language: {title, description}}`` for a focus world of
    :data:`.packaging.SCENES`; empty for any other world (nothing is half translated).
    The title leads with the language's own search phrase, then the genre (kept in
    English, as listeners search it) and the length."""
    scenes = _SCENES.get(visual_world or "")
    if scenes is None:
        return {}
    minutes = max(1, round(seconds / 60))
    out: dict[str, dict[str, str]] = {}
    for code, lang in LANGUAGES.items():
        length = lang.minutes.format(minutes)
        description = "\n".join((
            lang.hook.format(scene=scenes[code], length=length), lang.calm, "",
            f"{lang.tracklist}:\n{chapter_block}", "", lang.hashtags))
        out[code] = {"title": f"{lang.head}{lang.separator}{genre} · {length}"[:100],
                     "description": description}
    return out
