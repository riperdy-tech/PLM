"""Localized titles and descriptions (growth spec 2026-11-03, ADR-0045 decision 2).

English stays the default language; YouTube shows a localization only to viewers whose
language it is. The owner chose German, Dutch, Swedish, Korean, Traditional Chinese and
Japanese. Each language says what the scenario title says, in that language's own way
of searching for focus music (작업용 BGM, 作業用BGM, 讀書音樂, Musik zum Konzentrieren), not
word for word. The scene of every focus world is written per language; a scene without a
translation leaves that language out rather than mixing English into it.

Written by the pipeline's author, not by native speakers: each language's text should be
read once by a native speaker (ADR-0045).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .packaging import SCENES

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
    music: str
    """``{brand}``."""
    visuals: str
    notice: str
    tracklist: str
    hashtags: str
    separator: str = " – "


LANGUAGES: Final[dict[str, _Language]] = {
    "de": _Language(
        head="Musik zum Konzentrieren, Lernen und Arbeiten", minutes="{} Minuten",
        hook="{scene}: {length} ruhige instrumentale elektronische Musik zum Lernen, "
             "Programmieren und konzentrierten Arbeiten.",
        calm="Keine Texte, kein Gesang, ein sanfter, gleichmäßiger Puls im Hintergrund.",
        music="Musik: mit KI-Musikwerkzeugen erstellte Instrumentals, ausgewählt und "
              "angeordnet von {brand}.",
        visuals="Bilder: eigens für {brand} gestaltet.",
        notice="Hinweis: Musik und Bilder wurden mit KI-Werkzeugen erstellt.",
        tracklist="Titelliste", hashtags="#konzentrationsmusik #lernmusik #focusmusic"),
    "nl": _Language(
        head="Muziek om te focussen, studeren en werken", minutes="{} minuten",
        hook="{scene}: {length} rustige instrumentale elektronische muziek om te "
             "studeren, programmeren en geconcentreerd te werken.",
        calm="Geen tekst, geen zang, een zachte, gelijkmatige puls op de achtergrond.",
        music="Muziek: instrumentals gemaakt met AI-muziektools, gekozen en gerangschikt "
              "door {brand}.",
        visuals="Beelden: speciaal gemaakt voor {brand}.",
        notice="Let op: muziek en beelden zijn gemaakt met AI-tools.",
        tracklist="Tracklijst", hashtags="#concentratiemuziek #studeermuziek #focusmusic"),
    "sv": _Language(
        head="Fokusmusik för plugg och arbete", minutes="{} minuter",
        hook="{scene}: {length} lugn instrumental elektronisk musik för plugg, "
             "programmering och koncentrerat arbete.",
        calm="Ingen text, ingen sång, en mjuk och jämn puls i bakgrunden.",
        music="Musik: instrumentala spår gjorda med AI-musikverktyg, valda och ordnade "
              "av {brand}.",
        visuals="Bilder: skapade för {brand}.",
        notice="Obs: musik och bilder är skapade med AI-verktyg.",
        tracklist="Låtlista", hashtags="#fokusmusik #pluggmusik #focusmusic"),
    "ko": _Language(
        head="집중할 때 듣는 음악 | 공부·작업용 BGM", minutes="{}분", separator=" | ",
        hook="{scene}: 공부, 코딩, 몰입 작업을 위한 {length} 차분한 일렉트로닉 연주곡.",
        calm="가사도 보컬도 없이, 배경에 잔잔하게 깔리는 부드러운 비트.",
        music="음악: AI 음악 도구로 만든 연주곡 · 선곡과 구성 {brand}",
        visuals="이미지: {brand} 오리지널 아트워크",
        notice="안내: 음악과 이미지는 AI 도구로 제작되었습니다.",
        tracklist="트랙리스트", hashtags="#집중음악 #공부할때듣는음악 #작업용BGM"),
    "zh-Hant": _Language(
        head="專注音樂・讀書工作用", minutes="{} 分鐘", separator="｜",
        hook="{scene}：{length}平靜的電子器樂，適合讀書、寫程式與專注工作。",
        calm="沒有歌詞、沒有人聲，只有背景裡柔和而穩定的節奏。",
        music="音樂：以 AI 音樂工具製作的器樂，由 {brand} 挑選與編排。",
        visuals="影像：為 {brand} 製作的原創圖像。",
        notice="說明：音樂與影像皆以 AI 工具製作。",
        tracklist="曲目", hashtags="#專注音樂 #讀書音樂 #工作音樂"),
    "ja": _Language(
        head="作業用BGM・集中できる音楽", minutes="{}分", separator="｜",
        hook="{scene}：勉強、プログラミング、集中作業のための{length}の穏やかな"
             "エレクトロニック・インストゥルメンタル。",
        calm="歌詞もボーカルもなく、やわらかく一定のリズムが流れ続けます。",
        music="音楽：AI音楽ツールで制作したインストゥルメンタルを {brand} が選び、構成しました。",
        visuals="映像：{brand} のために制作したオリジナル画像。",
        notice="お知らせ：音楽と映像はAIツールで制作されています。",
        tracklist="トラックリスト", hashtags="#作業用BGM #勉強用BGM #集中BGM"),
}
"""YouTube language codes (``zh-Hant``: Traditional Chinese, for Taiwan and Hong Kong)."""

_SCENES: Final[dict[str, tuple[str, ...]]] = {
    # in the order of packaging.SCENES, world by world
    "de": ("Spätabends im Architekturbüro", "Bernsteinlicht im stillen Atelier",
           "Nach Feierabend im Atelier",
           "Morgengrauen am Zeichentisch", "Erstes Licht, erster Entwurf",
           "Frühe Stunden am Zeichentisch",
           "Ein Tisch, eine Aufgabe, kein Lärm", "Aufgeräumter Tisch vor tiefer Arbeit",
           "Leerer Tisch, klarer Kopf",
           "Spät arbeiten in der Sternwarte", "Nachtschicht in der Sternwarte",
           "Sterne oben, Arbeit am Bildschirm",
           "Nachtschicht im Serverraum", "Serverlichter und stiller Fokus",
           "Summende Racks, tiefe Konzentration",
           "Im Inneren des Schaltkreises", "Signalwege und ruhiger Fokus",
           "Im Raster versunken, im Flow",
           "Baupläne in der ganzen Halle", "Planungsstunden in der Planhalle",
           "Große Pläne, stille Halle"),
    "nl": ("Laat in het architectenatelier", "Amberlicht in een stil atelier",
           "Na sluitingstijd in het atelier",
           "Ochtendgloren aan de tekentafel", "Eerste licht, eerste schets",
           "Vroege uren aan de tekentafel",
           "Eén bureau, één taak, geen lawaai", "Opgeruimd bureau voor diep werk",
           "Minimaal bureau, helder hoofd",
           "Laat doorwerken in de sterrenwacht", "Nachtdienst in de sterrenwacht",
           "Sterren boven, werk op het scherm",
           "Nachtdienst in de serverhal", "Serverlichtjes en stille focus",
           "Zoemende racks, diep werk",
           "Binnen in het circuitraster", "Signaalpaden en rustige focus",
           "Verdwaald in het raster, in de flow",
           "Bouwtekeningen door de hele hal", "Planuren in de blauwdrukhal",
           "Grote plannen, stille hal"),
    "sv": ("Sent på kvällen i arkitektstudion", "Bärnstensljus i en tyst studio",
           "Efter arbetstid i studion",
           "Gryning vid ritbordet", "Första ljuset, första utkastet",
           "Tidiga timmar vid ritbordet",
           "Ett skrivbord, en uppgift, inget brus", "Rent skrivbord inför djupt arbete",
           "Minimalt skrivbord, klart huvud",
           "Sena arbetstimmar i observatoriet", "Nattskift i observatoriet",
           "Stjärnor ovanför, arbete på skärmen",
           "Nattskift i serverhallen", "Serverljus och stilla fokus",
           "Surrande rack, djupt arbete",
           "Inuti kretsnätet", "Signalvägar och stadigt fokus", "Försjunken i nätet, i flow",
           "Ritningar över hela hallen", "Planeringstimmar i ritningshallen",
           "Stora planer, tyst hall"),
    "ko": ("늦은 밤, 건축가의 작업실에서", "조용한 작업실의 호박빛 조명", "모두 퇴근한 뒤의 작업실",
           "새벽, 제도판 앞에서", "첫 햇살, 첫 스케치", "이른 아침의 제도판",
           "책상 하나, 할 일 하나, 소음 없이", "깊은 집중 전, 깨끗한 책상", "미니멀한 책상, 맑은 머리",
           "천문대에서 늦게까지 일하는 밤", "천문대의 야간 근무", "머리 위엔 별, 화면 위엔 일",
           "서버실의 야간 근무", "서버 불빛과 고요한 집중", "웅웅대는 서버랙, 깊은 몰입",
           "회로 격자 속으로", "신호의 길, 흔들림 없는 집중", "격자 속에 빠져, 몰입의 흐름",
           "홀 가득 펼쳐진 설계도", "설계도 홀에서 보내는 계획의 시간", "큰 계획, 고요한 홀"),
    "zh-Hant": ("深夜的建築師工作室", "安靜工作室裡的琥珀色燈光", "下班後的工作室",
                "黎明時分的製圖桌", "第一道光，第一份草稿", "清晨的製圖桌前",
                "一張桌子、一件事、沒有雜音", "深度工作前的整潔桌面", "極簡桌面，清晰思緒",
                "在天文台工作到深夜", "天文台的夜班", "頭頂星空，螢幕上的工作",
                "伺服器機房的夜班", "伺服器燈光與安靜的專注", "低鳴的機櫃，深度專注",
                "走進電路網格", "訊號路徑與穩定的專注", "沉浸網格之中，進入心流",
                "鋪滿大廳的藍圖", "在藍圖大廳規劃的時光", "遠大計畫，安靜大廳"),
    "ja": ("深夜の建築家のアトリエで", "静かなアトリエの琥珀色の灯り", "誰もいない夜のアトリエ",
           "夜明けの製図台", "最初の光、最初の下書き", "製図台に向かう早朝",
           "机ひとつ、タスクひとつ、雑音なし", "深い集中の前の、片づいた机", "ミニマルな机、クリアな頭",
           "天文台で過ごす遅い作業時間", "天文台の夜勤", "頭上に星、画面に仕事",
           "サーバールームの夜勤", "サーバーの灯りと静かな集中", "低く唸るラック、深い集中",
           "回路グリッドの内側へ", "信号の道と、揺るがない集中", "グリッドに没頭、フロー状態",
           "ホールに広がる設計図", "設計図ホールで過ごす計画の時間", "大きな計画、静かなホール"),
}

_ENGLISH: Final[tuple[str, ...]] = tuple(s for scenes in SCENES.values() for s in scenes)


def localize(scene: str, *, seconds: float, chapter_block: str, brand_name: str,
             ) -> dict[str, dict[str, str]]:
    """``{language: {title, description}}`` for an English ``scene`` of
    :data:`.packaging.SCENES`; empty for any other scene (nothing is half translated)."""
    if scene not in _ENGLISH:
        return {}
    index = _ENGLISH.index(scene)
    minutes = max(1, round(seconds / 60))
    out: dict[str, dict[str, str]] = {}
    for code, lang in LANGUAGES.items():
        local = _SCENES[code][index]
        length = lang.minutes.format(minutes)
        description = "\n".join((
            lang.hook.format(scene=local, length=length), lang.calm, "",
            f"{lang.tracklist}:\n{chapter_block}", "",
            lang.music.format(brand=brand_name), lang.visuals.format(brand=brand_name),
            lang.notice, "", lang.hashtags))
        out[code] = {"title": f"{local}{lang.separator}{lang.head}"[:100],
                     "description": description}
    return out
