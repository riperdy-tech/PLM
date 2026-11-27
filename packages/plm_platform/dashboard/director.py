"""The Claude director: Claude's judgment in the owner's song prompts (owner 2026-11-27).

The locally installed Claude Code CLI runs headless (``claude -p``, JSON schema output,
no tools, no session, from an empty temporary folder so no project context is loaded):

* **Style (Claude advises, the owner chooses).** The Start menu shows Claude's suggested
  sub-style, from P2's available list only, with a one-line reason, worked out from the
  last videos (style, title, prompts, YouTube counters). Computed in the background and
  cached: the Start page never waits on Claude.
* **Song prompts ("free wording, checked").** When a run's song sheets appear (P4's
  ``<inbox>/<track>.prompts.txt`` with the texts recorded in ``<track>.songs.json``), one
  request per video asks Claude for every song's Styles (Suno) or Prompt (Flow Music)
  text in its own words, keeping P3's fixed facts (tempo, key, mood, length,
  instrumental), with a short review of the variety from song to song and from the last
  videos. Every text is checked (:func:`styles_problem`); a song whose text fails keeps
  the template text. Claude unavailable, slow, out of usage or answering nonsense: every
  template text is kept and the run is never blocked.
* **Stored once.** An accepted text replaces the song's ``lines`` in ``songs.json`` (the
  template is kept as ``template_prompt`` and ``template_lines``), the record says
  ``written_by`` with model, time and review, and the sheet is re-rendered. P4 renders a
  song from its recorded ``lines``, so its later re-renders keep Claude's text. A record
  that says ``written_by`` is never written again.
* **Off switch.** ``PLM_STUDIO_DIRECTOR=off``. Every call is logged to
  ``data/director/log.jsonl`` (``PLM_STUDIO_DIRECTOR_LOG``).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from ..clock import Clock, SystemClock
from ..dotenv import load_env_file

__all__ = [
    "Brief", "Director", "DirectorUnavailable", "Facts", "Reply", "Song", "claude_argv",
    "claude_command", "cli_runner", "director_from_env", "exclude_problem",
    "parse_cli_output", "recorded_prompts", "styles_problem", "waiting_songs"]

SWITCH_VAR: Final = "PLM_STUDIO_DIRECTOR"
MODEL_VAR: Final = "PLM_STUDIO_DIRECTOR_MODEL"
LOG_VAR: Final = "PLM_STUDIO_DIRECTOR_LOG"
DEFAULT_MODEL: Final = "sonnet"
DEFAULT_LOG: Final = "data/director/log.jsonl"
TIMEOUT_SECONDS: Final = 180.0
RETRY_SUGGESTION_SECONDS: Final = 1800.0
"""A failed style suggestion is asked again after half an hour at the earliest."""
MOODS: Final = ("focused", "calm", "dreamy", "hopeful", "uplifting")
"""The focus identity's moods (P3's ``mood_limits`` for focus_electronic)."""
TEXT_LIMIT: Final = 1000
"""Characters Suno's Styles box takes (P4's ``STYLES_LIMIT``)."""
RECENT_VIDEOS: Final = 3


class DirectorUnavailable(Exception):
    """Claude gave no usable answer; ``reason`` is a short code, ``detail`` why."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


@dataclass(frozen=True, slots=True)
class Reply:
    answer: dict[str, Any]
    model: str


Runner = Callable[[str, str, Mapping[str, Any], float], Reply]
"""``(system prompt, prompt, JSON schema, timeout seconds) -> Reply``; raises
:class:`DirectorUnavailable`."""


# -- the CLI ---------------------------------------------------------------------------------


def claude_command() -> list[str] | None:
    """The ``claude`` executable. On Windows npm installs a ``claude.cmd`` wrapper around
    a native ``claude.exe``; the exe is called directly, so no argument (the JSON schema)
    goes through ``cmd.exe``'s quoting."""
    found = shutil.which("claude")
    if found is None:
        return None
    path = Path(found)
    if path.suffix.lower() in (".cmd", ".bat", ".ps1"):
        native = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if native.is_file():
            return [str(native)]
        if path.suffix.lower() == ".ps1":
            return None
    return [str(path)]


def claude_argv(command: Sequence[str], model: str, system: str,
                schema: Mapping[str, Any]) -> list[str]:
    """Headless, one answer as JSON matching ``schema``, no tools, no saved session, no
    CLAUDE.md, skills, plugins, hooks or MCP servers (``--safe-mode``); the prompt goes in
    on stdin."""
    return [*command, "-p", "--output-format", "json",
            "--json-schema", json.dumps(schema, separators=(",", ":")),
            "--model", model, "--tools", "", "--no-session-persistence", "--safe-mode",
            "--strict-mcp-config", "--system-prompt", system]


def parse_cli_output(stdout: str, returncode: int, stderr: str) -> Reply:
    """The structured answer of ``claude -p --output-format json``."""
    try:
        out = json.loads(stdout)
    except ValueError:
        raise DirectorUnavailable("invalid_json", (stdout or stderr).strip()[:300]) from None
    if isinstance(out, list):  # an event list: the result is the last one
        out = next((e for e in reversed(out) if isinstance(e, dict)
                    and e.get("type") == "result"), {})
    if not isinstance(out, dict):
        raise DirectorUnavailable("invalid_json", "not a JSON object")
    if out.get("is_error") or returncode != 0:
        raise DirectorUnavailable("claude_error", str(out.get("result") or stderr
                                                      or out.get("subtype"))[:300])
    answer = out.get("structured_output")
    if answer is None and isinstance(out.get("result"), str):
        try:
            answer = json.loads(out["result"])
        except ValueError:
            answer = None
    if not isinstance(answer, dict):
        raise DirectorUnavailable("invalid_json", "no structured answer")
    usage = out.get("modelUsage")
    model = next(iter(usage), "") if isinstance(usage, dict) and usage else ""
    return Reply(answer, str(model))


def cli_runner(model: str, *, command: Sequence[str] | None = None,
               run: Callable[..., Any] = subprocess.run) -> Runner:
    """The real runner: the ``claude`` CLI in an empty temporary folder."""
    def ask(system: str, prompt: str, schema: Mapping[str, Any], timeout: float) -> Reply:
        exe = list(command) if command else claude_command()
        if not exe:
            raise DirectorUnavailable("claude_not_installed", "no claude on PATH")
        with tempfile.TemporaryDirectory(prefix="plm-director-") as cwd:
            try:
                done = run(claude_argv(exe, model, system, schema), input=prompt,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=cwd, check=False)
            except subprocess.TimeoutExpired:
                raise DirectorUnavailable("timeout", f"no answer in {timeout:.0f} s") from None
            except OSError as exc:
                raise DirectorUnavailable("claude_not_runnable", str(exc)) from exc
        return parse_cli_output(done.stdout or "", done.returncode, done.stderr or "")

    return ask


# -- the checks ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Facts:
    """P3's facts of one song, which Claude's text must not change."""

    bpm: int | None
    key: str | None
    """A named key (``"D minor"``), or ``None`` when the spec names none."""
    moods: tuple[str, ...]
    seconds: int | None
    instrumental: bool


_VOCAL: Final = (r"vocals?|vocalists?|singers?|singing|sung|lyrics?|rap|rapping|rappers?|"
                 r"choirs?|choral|chants?|chanting|a\s?cappella|acapella|spoken\s+word")
_VOCAL_LIST: Final = (rf"(?:{_VOCAL})(?:\s*(?:/|\bor\b|\band\b|\bnor\b)\s*(?:{_VOCAL})"
                      rf"|\s*,\s*no\s+(?:{_VOCAL}))*")
"""``vocals or lyrics``, ``vocals, no choir``: a comma needs its own ``no``."""
_VOCAL_NEGATED: Final = re.compile(
    rf"\b(?:no|without|free\s+of|zero|never)\s+(?:[a-z-]+\s+){{0,2}}?{_VOCAL_LIST}\b"
    rf"|\b(?:{_VOCAL})[-\s]free\b", re.IGNORECASE)
_VOCAL_WORD: Final = re.compile(rf"\b(?:{_VOCAL})\b", re.IGNORECASE)
_NO_VOCALS: Final = re.compile(r"\b(?:no|without)\s+vocals?\b|\bvocal[-\s]free\b",
                               re.IGNORECASE)
_INSTRUMENTAL: Final = re.compile(r"\binstrumental\b", re.IGNORECASE)
_OFF_MOODS: Final = ("dark", "sad", "melancholic", "melancholy", "gloomy", "somber",
                     "sombre", "aggressive", "angry", "tense", "ominous", "sinister", "eerie",
                     "haunting", "epic", "euphoric", "frantic", "anxious", "depressing",
                     "scary", "romantic", "sensual", "nostalgic", "mysterious", "moody")
_OFF_MOOD: Final = re.compile(rf"\b({'|'.join(_OFF_MOODS)})\b", re.IGNORECASE)
_NEGATED_WORD: Final = re.compile(r"\b(?:no|not|without|never|avoid(?:ing)?)\s+(?:[a-z-]+\s+)?"
                                  rf"(?:{'|'.join(_OFF_MOODS)})\b", re.IGNORECASE)
_MOOD_BEFORE: Final = re.compile(r"\b([a-z-]+)\s+mood\b", re.IGNORECASE)
_MOOD_LIST: Final = re.compile(r"\bmoods?:\s*([a-z-]+(?:\s*(?:/|&|\band\b)\s*[a-z-]+)*)",
                               re.IGNORECASE)
_NOT_A_MOOD: Final = frozenset({"a", "an", "the", "its", "their", "overall", "general",
                                "same", "this", "that", "of", "and", "steady", "consistent"})
_BPM: Final = re.compile(r"\b(\d{2,3})\s*-?\s*(?:bpm|beats per minute)\b", re.IGNORECASE)
_KEY: Final = re.compile(r"\b([A-G])(#|♯|b|♭|-sharp|-flat| sharp| flat)?[\s_-]*"
                         r"((?i:major|minor|maj|min))\b")
_SECONDS: Final = re.compile(r"\b(\d{2,4})\s*-?\s*(?:seconds?|secs?)\b", re.IGNORECASE)
_MINUTES: Final = re.compile(r"\b(\d+(?:\.\d+)?)\s*-?\s*(?:minutes?|mins?)\b", re.IGNORECASE)
_URL: Final = re.compile(r"https?://|\bwww\.|\b[a-z0-9-]+\.(?:com|net|org|io|fm|co|ai)\b",
                         re.IGNORECASE)
_ARTIST: Final = (re.compile(r"\b(?:in the style of|style of|inspired by|reminiscent of|"
                             r"sounds? like|feat\.|featuring)\b", re.IGNORECASE),
                  re.compile(r"\b(?:[Ll]ike|[àa] la)\s+(?:the\s+)?[A-Z]"))


def normal_key(text: str | None) -> str | None:
    """``"D_minor"``, ``"d minor"``... as ``"D minor"``; ``None`` when no key is named."""
    found = _KEY.search((text or "").replace("_", " "))
    if found is None:
        return None
    note, accidental, mode = found.groups()
    sign = "#" if accidental and ("#" in accidental or "♯" in accidental or "sharp" in accidental) \
        else "b" if accidental else ""
    return f"{note}{sign} {'major' if mode.lower().startswith('maj') else 'minor'}"


def _artist_problem(text: str) -> str | None:
    if any(pattern.search(text) for pattern in _ARTIST):
        return "names an artist or a style of someone (\"in the style of\", \"like ...\")"
    try:
        from ..protected_artists import load_protected_artist_policy

        screening = load_protected_artist_policy().screen(text)
    except Exception:  # noqa: BLE001 - the patterns above still apply
        return None
    return "names a protected artist" if screening.blocked else None


def styles_problem(text: str, facts: Facts) -> str | None:
    """Why Claude's Styles (or Prompt) text cannot be used, or ``None``."""
    if len(text) > TEXT_LIMIT:
        return f"is more than {TEXT_LIMIT} characters ({len(text)})"
    if not text.strip():
        return "is empty"
    if not _INSTRUMENTAL.search(text):
        return "does not say instrumental"
    if not _NO_VOCALS.search(text):
        return "does not say no vocals"
    vocal = _VOCAL_WORD.search(_VOCAL_NEGATED.sub(" ", text))
    if vocal:
        return f"names a vocal part ({vocal.group(0)!r})"
    allowed = {*MOODS, *(m.lower() for m in facts.moods)}
    named = [m.group(1) for m in _MOOD_BEFORE.finditer(text)]
    for listed in _MOOD_LIST.finditer(text):
        named += re.split(r"\s*(?:/|&|\band\b)\s*", listed.group(1).strip())
    off = [m for m in named if m and m.lower() not in allowed | _NOT_A_MOOD]
    off_word = _OFF_MOOD.search(_NEGATED_WORD.sub(" ", text))
    if off or off_word:
        return (f"names a mood outside {', '.join(sorted(allowed))} "
                f"({(off or [off_word.group(0) if off_word else ''])[0]!r})")
    for found in _BPM.finditer(text):
        if facts.bpm is None or int(found.group(1)) != facts.bpm:
            return f"changes the tempo to {found.group(1)} BPM (the spec says {facts.bpm})"
    for found in _KEY.finditer(text.replace("_", " ")):
        named_key = normal_key(found.group(0))
        if named_key != facts.key:
            return f"names the key {named_key} (the spec says {facts.key or 'no key'})"
    if facts.seconds is not None:
        for found in _SECONDS.finditer(text):
            if int(found.group(1)) != facts.seconds:
                return f"changes the length to {found.group(1)} seconds ({facts.seconds})"
        for found in _MINUTES.finditer(text):
            if abs(float(found.group(1)) * 60 - facts.seconds) > 30:
                return f"changes the length to {found.group(0)} ({facts.seconds} seconds)"
    if _URL.search(text):
        return "contains a URL"
    return _artist_problem(text)


def exclude_problem(text: str) -> str | None:
    """Why Claude's Exclude styles text cannot be used (it may name vocals: excluding
    them is the point), or ``None``."""
    if len(text) > TEXT_LIMIT:
        return f"Exclude is more than {TEXT_LIMIT} characters"
    if _INSTRUMENTAL.search(text):
        return "Exclude names the instrumental itself"
    if _URL.search(text):
        return "Exclude contains a URL"
    problem = _artist_problem(text)
    return None if problem is None else f"Exclude {problem}"


# -- P4's sheets and records -----------------------------------------------------------------

_HEAD: Final = re.compile(r"\A(Styles|Prompt) \((\d+) characters;([^)]*)\):\Z")
_EXCLUDE: Final = "Exclude styles: "
_SAVE_AS: Final = re.compile(r"^Save as: ([A-Za-z0-9_-]+)\.wav$", re.MULTILINE)
_LENGTH: Final = re.compile(r"^Length: about (\d+) seconds", re.MULTILINE)


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)  # P4's file-name rule


@dataclass(frozen=True, slots=True)
class Song:
    """One song still to be made, as P4 asked for it."""

    stem: str
    code: str
    sheet: Path
    record: Path
    field: str
    """``Styles`` (Suno) or ``Prompt`` (Flow Music): the box the text is pasted into."""
    text: str
    exclude: str | None
    lines: tuple[str, ...]
    facts: Facts


@dataclass(frozen=True, slots=True)
class Brief:
    """What one request about one video tells Claude."""

    direction: dict[str, Any]
    songs: list[Song]
    recent: list[dict[str, Any]]


def _load(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) and isinstance(loaded.get("songs"), dict) else None


def _field(lines: Sequence[str]) -> tuple[str, str, str | None] | None:
    if len(lines) < 2:
        return None
    head = _HEAD.match(lines[0])
    if head is None:
        return None
    exclude = next((x[len(_EXCLUDE):] for x in lines[2:] if x.startswith(_EXCLUDE)), None)
    return head.group(1), lines[1], exclude


def facts_of(prompt: str, block: str) -> Facts:
    """P3's facts from P4's recorded prompt and the sheet's song entry."""
    bpm = re.search(r"\b(\d{2,3}) BPM\b", prompt)
    key = re.search(r"harmonic key: ([^.]+)", prompt)
    moods = re.search(r"\bmood: ([^.]+)", prompt)
    length = _LENGTH.search(block)
    return Facts(bpm=int(bpm.group(1)) if bpm else None,
                 key=normal_key(key.group(1)) if key else None,
                 moods=tuple(m.strip() for m in moods.group(1).split(",")) if moods else (),
                 seconds=int(length.group(1)) if length else None,
                 instrumental="Instrumental: yes" in block)


def waiting_songs(inboxes: Sequence[Path], tracks: Iterable[str]) -> list[Song]:
    """The instrumental songs of ``tracks`` still to be made whose record was never
    written by the director (a saved song is never rewritten)."""
    songs: list[Song] = []
    for track in tracks:
        for inbox in inboxes:
            sheet, record_path = (inbox / f"{_safe(track)}.prompts.txt",
                                  inbox / f"{_safe(track)}.songs.json")
            record = _load(record_path)
            if record is None or record.get("written_by") or not sheet.is_file():
                continue
            entries = {str(code): e for code, e in record["songs"].items()
                       if isinstance(e, dict) and isinstance(e.get("stem"), str)
                       and isinstance(e.get("lines"), list)}
            for block in sheet.read_text(encoding="utf-8").split("--- Song ")[1:]:
                saved = _SAVE_AS.search(block)
                if saved is None or "Instrumental: yes" not in block:
                    continue
                stem = saved.group(1)
                if (inbox / f"{stem}.wav").is_file():
                    continue
                code = next((c for c, e in entries.items()
                             if stem == e["stem"] or stem.startswith(f"{e['stem']}-r")), None)
                if code is None:
                    continue
                lines = tuple(str(x) for x in entries[code]["lines"])
                found = _field(lines)
                if found is None:
                    continue
                songs.append(Song(stem=stem, code=code, sheet=sheet, record=record_path,
                                  field=found[0], text=found[1], exclude=found[2],
                                  lines=lines,
                                  facts=facts_of(str(entries[code].get("prompt") or ""),
                                                 block)))
    return songs


def recorded_prompts(inboxes: Sequence[Path], tracks: Iterable[str],
                     limit: int = 8) -> list[str]:
    """The texts an earlier video's songs were made from (its records, in the inbox or
    moved out with a finished plan)."""
    texts: list[str] = []
    for track in tracks:
        name = f"{_safe(track)}.songs.json"
        for inbox in inboxes:
            for path in [inbox / name, *sorted(inbox.glob(f"*/*/{name}"))]:
                record = _load(path)
                for entry in (record or {}).get("songs", {}).values():
                    found = _field([str(x) for x in (entry or {}).get("lines") or []])
                    if found and found[1] not in texts:
                        texts.append(found[1])
                if len(texts) >= limit:
                    return texts[:limit]
    return texts


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.part")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _new_lines(song: Song, text: str, exclude: str | None) -> list[str]:
    head = _HEAD.match(song.lines[0])
    assert head is not None  # a Song is only made from a line that matches
    out = [f"{song.field} ({len(text)} characters;{head.group(3)}):", text]
    for line in song.lines[2:]:
        if line.startswith(_EXCLUDE):
            out.append(f"{_EXCLUDE}{exclude or line[len(_EXCLUDE):]}")
        elif not line.startswith("Warning: "):  # P4's cut-off warning: the text fits
            out.append(line)
    return out


def _store(songs: Sequence[Song], written: Mapping[str, list[str]],
           refused: Mapping[str, str], director: dict[str, Any]) -> None:
    """Write each record once (``written``: stem -> new lines) and re-render its sheet."""
    for record_path in dict.fromkeys(s.record for s in songs):
        mine = [s for s in songs if s.record == record_path]
        record = _load(record_path)
        if record is None:
            continue
        for song in mine:
            entry = record["songs"][song.code]
            if song.stem in written:
                entry.update(template_prompt=song.text, template_lines=list(song.lines),
                             lines=written[song.stem], written_by="claude")
            else:
                entry.update(written_by="template",
                             director_refused=refused.get(song.stem, director.get("reason")))
        record["written_by"] = "claude" if any(s.stem in written for s in mine) else "template"
        record["director"] = director
        _write_atomic(record_path, json.dumps(record, indent=2, sort_keys=True) + "\n")
        sheet = mine[0].sheet
        text = sheet.read_text(encoding="utf-8")
        blocks = text.split("--- Song ")
        for n, block in enumerate(blocks):
            for song in mine:
                if song.stem in written and f"Save as: {song.stem}.wav\n" in block:
                    blocks[n] = block.replace("\n".join(song.lines),
                                              "\n".join(written[song.stem]), 1)
        if "--- Song ".join(blocks) != text:
            _write_atomic(sheet, "--- Song ".join(blocks))


# -- the requests ----------------------------------------------------------------------------

SONGS_SYSTEM: Final = (
    "You are the music director of a YouTube channel of instrumental deep-focus "
    "electronic music. For each song of one video you write the text the owner pastes "
    "into an AI music tool (Suno's Styles box, or Flow Music's Prompt box), and you review "
    "variety. Rules for every text: keep every fixed fact exactly (tempo in BPM, key, "
    "length, instrumental); include the words \"instrumental\" and \"no vocals\"; never "
    "mention singing, lyrics, voices, choirs or rap except to say there are none; moods "
    "only from: focused, calm, dreamy, hopeful, uplifting; no artist names, no \"in the "
    "style of\" or \"like <artist>\", no URLs; at most 1000 characters on one line of "
    "comma-separated style phrases. Make the songs of this video clearly distinct from "
    "each other (lead instrument, groove, texture, arrangement) while staying inside the "
    "video's sub-style, and clearly different from the recent videos' prompts. The tool "
    "weighs the first words most: open every text with what makes that song different "
    "(its lead, texture, groove or scene), never with the genre, mood, tempo or key, and "
    "do not start two songs with the same words; put genre, mood, BPM, key and "
    "\"instrumental, no vocals\" later in the text. Name a mood only as one of the "
    "allowed words followed by \"mood\" (never \"study mood\"). Answer only with the "
    "requested JSON.")

STYLE_SYSTEM: Final = (
    "You advise the owner of a YouTube channel of instrumental deep-focus electronic "
    "music on the sub-style of the next video. Pick exactly one sub-style from the list "
    "given, so the channel does not sound alike from one video to the next and leans "
    "towards what viewers responded to. Give a one-line reason (at most 160 characters). "
    "Answer only with the requested JSON.")

SONGS_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "songs": {"type": "array", "items": {
            "type": "object",
            "properties": {"stem": {"type": "string"}, "styles": {"type": "string"},
                           "exclude": {"type": "string"}},
            "required": ["stem", "styles"]}},
        "review": {"type": "string"}},
    "required": ["songs", "review"]}


def songs_prompt(brief: Brief) -> str:
    songs = [{"stem": s.stem, "box": s.field,
              "fixed": {"bpm": s.facts.bpm, "key": s.facts.key, "moods": list(s.facts.moods),
                        "length_seconds": s.facts.seconds, "instrumental": s.facts.instrumental},
              "template_text": s.text,
              **({"template_exclude": s.exclude} if s.exclude else {})} for s in brief.songs]
    return (
        "Write the text for every song of this video.\n\n"
        f"Video: {json.dumps(brief.direction, ensure_ascii=False)}\n\n"
        "Songs (\"template_text\" is what the code wrote; write your own; \"box\" is where "
        "it is pasted; Flow Music has no Exclude box, so put nothing there for a Prompt):\n"
        f"{json.dumps(songs, ensure_ascii=False, indent=1)}\n\n"
        "The last videos' prompts, newest first (sound different from them):\n"
        f"{json.dumps(brief.recent, ensure_ascii=False, indent=1)}\n\n"
        "Return songs: one entry per stem with styles (the full text for its box) and, for "
        "a Styles box only, exclude (what to keep out); and review: one to three sentences "
        "on how the songs differ from each other and from the last videos.")


def style_schema(available: Sequence[str]) -> dict[str, Any]:
    return {"type": "object",
            "properties": {"subgenre": {"type": "string", "enum": list(available)},
                           "reason": {"type": "string"}},
            "required": ["subgenre", "reason"]}


def _in_thread(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="plm-director", daemon=True).start()


@dataclass
class Director:
    """Runs the requests in the background (``spawn``), once per video, and keeps the
    log. ``runner=None``: turned off."""

    runner: Runner | None
    model: str = DEFAULT_MODEL
    log_path: Path | None = None
    clock: Clock = field(default_factory=SystemClock)
    spawn: Callable[[Callable[[], None]], None] = _in_thread
    timeout: float = TIMEOUT_SECONDS
    _lock: Any = field(default_factory=threading.Lock)
    _started: set[str] = field(default_factory=set)
    _running: set[str] = field(default_factory=set)
    _suggestion: tuple[str, dict[str, Any], float] | None = None

    @property
    def enabled(self) -> bool:
        return self.runner is not None

    def writing(self, key: str) -> bool:
        return key in self._running

    def _log(self, **entry: Any) -> None:
        if self.log_path is None:
            return
        line = json.dumps({"at": self.clock.now().isoformat(), **entry}, ensure_ascii=False)
        with self._lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(line + "\n")

    def _ask(self, system: str, prompt: str, schema: Mapping[str, Any]) -> Reply:
        if self.runner is None:
            raise DirectorUnavailable("off")
        return self.runner(system, prompt, schema, self.timeout)

    # -- song prompts ----------------------------------------------------------------------

    def write_prompts(self, key: str, prepare: Callable[[], Brief | None]) -> bool:
        """Start the one request for the video ``key`` (unless already started in this
        process); ``prepare`` runs in the background too. Returns whether it started."""
        if self.runner is None:
            return False
        with self._lock:
            if key in self._started:
                return False
            self._started.add(key)
            self._running.add(key)
        self.spawn(lambda: self._write_job(key, prepare))
        return True

    def _write_job(self, key: str, prepare: Callable[[], Brief | None]) -> None:
        began = time.monotonic()
        brief: Brief | None = None
        try:
            brief = prepare()
            if brief is None or not brief.songs:
                return
            reply = self._ask(SONGS_SYSTEM, songs_prompt(brief), SONGS_SCHEMA)
            answered = reply.answer.get("songs")
            if not isinstance(answered, list):
                raise DirectorUnavailable("invalid_answer", "no list of songs")
            by_stem = {str(a.get("stem")): a for a in answered if isinstance(a, dict)}
            written: dict[str, list[str]] = {}
            refused: dict[str, str] = {}
            for song in brief.songs:
                got = by_stem.get(song.stem)
                text = " ".join(str((got or {}).get("styles") or "").split())
                exclude = " ".join(str((got or {}).get("exclude") or "").split()) or None
                if song.field != "Styles" or song.exclude is None:
                    exclude = None  # only Suno's sheet has an Exclude box
                problem = ("no text from Claude" if not got else styles_problem(text, song.facts)
                           or (exclude_problem(exclude) if exclude else None))
                if problem is None:
                    written[song.stem] = _new_lines(song, text, exclude)
                else:
                    refused[song.stem] = problem
            review = " ".join(str(reply.answer.get("review") or "").split())[:600]
            _store(brief.songs, written, refused,
                   {"model": reply.model or self.model, "at": self.clock.now().isoformat(),
                    "review": review, "reason": None})
            self._log(what="song_prompts", key=key, ok=True, fallback=None,
                      model=reply.model or self.model, songs=len(brief.songs),
                      accepted=len(written), refused=refused,
                      seconds=round(time.monotonic() - began, 1))
        except Exception as exc:  # noqa: BLE001 - every failure keeps the template texts
            reason = (f"{exc.reason}: {exc.detail}" if isinstance(exc, DirectorUnavailable)
                      else f"{type(exc).__name__}: {exc}")[:300]
            if brief is not None and brief.songs:
                try:
                    _store(brief.songs, {}, {}, {"model": self.model,
                                                 "at": self.clock.now().isoformat(),
                                                 "review": None, "reason": reason})
                except OSError:
                    pass  # the sheet stays as P4 wrote it; the log says why
            self._log(what="song_prompts", key=key, ok=False, fallback=reason,
                      model=self.model, accepted=0,
                      seconds=round(time.monotonic() - began, 1))
        finally:
            with self._lock:
                self._running.discard(key)

    # -- the Start menu's suggestion -------------------------------------------------------

    def suggestion(self, available: Sequence[str], fingerprint: str,
                   prepare: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
        """Claude's suggested sub-style for the next video: the cached answer for
        ``fingerprint`` (the available styles and the last videos), else ``thinking``
        while it is worked out in the background. ``None``: turned off."""
        if self.runner is None or not available:
            return None
        with self._lock:
            cached = self._suggestion
            if cached and cached[0] == fingerprint and (
                    cached[1]["state"] != "unavailable"
                    or time.monotonic() - cached[2] < RETRY_SUGGESTION_SECONDS):
                return dict(cached[1])
            self._suggestion = (fingerprint, {"state": "thinking"}, time.monotonic())
        self.spawn(lambda: self._suggest_job(list(available), fingerprint, prepare))
        with self._lock:
            return dict(self._suggestion[1]) if self._suggestion else {"state": "thinking"}

    def _suggest_job(self, available: list[str], fingerprint: str,
                     prepare: Callable[[], dict[str, Any]]) -> None:
        began = time.monotonic()
        try:
            context = prepare()
            prompt = ("Choose the sub-style of the next video.\n\n"
                      f"Available sub-styles (choose one): "
                      f"{json.dumps(context.get('available'), ensure_ascii=False)}\n\n"
                      "The last videos, newest first (style, title, YouTube counters when "
                      f"published, a few of their song prompts):\n"
                      f"{json.dumps(context.get('videos'), ensure_ascii=False, indent=1)}")
            reply = self._ask(STYLE_SYSTEM, prompt, style_schema(available))
            pick = reply.answer.get("subgenre")
            if pick not in available:
                raise DirectorUnavailable("not_available", f"{pick!r} is not in P2's list")
            result: dict[str, Any] = {
                "state": "ready", "subgenre": pick,
                "reason": " ".join(str(reply.answer.get("reason") or "").split())[:200],
                "model": reply.model or self.model, "at": self.clock.now().isoformat()}
            self._log(what="style_suggestion", ok=True, fallback=None, subgenre=pick,
                      model=result["model"], seconds=round(time.monotonic() - began, 1))
        except Exception as exc:  # noqa: BLE001 - the menu still works without it
            reason = (f"{exc.reason}: {exc.detail}" if isinstance(exc, DirectorUnavailable)
                      else f"{type(exc).__name__}: {exc}")[:300]
            result = {"state": "unavailable", "reason": reason}
            self._log(what="style_suggestion", ok=False, fallback=reason, model=self.model,
                      seconds=round(time.monotonic() - began, 1))
        with self._lock:
            if self._suggestion and self._suggestion[0] == fingerprint:
                self._suggestion = (fingerprint, result, time.monotonic())


def director_from_env(env: Mapping[str, str] | None = None, *,
                      dotenv_path: str | Path = ".env",
                      clock: Clock | None = None) -> Director:
    """The director as configured (process env, then ``.env``): on unless
    ``PLM_STUDIO_DIRECTOR=off``; model ``PLM_STUDIO_DIRECTOR_MODEL`` (default sonnet)."""
    process = os.environ if env is None else env
    dotenv = load_env_file(dotenv_path)

    def value(name: str) -> str:
        return (process.get(name) or dotenv.get(name) or "").strip()

    model = value(MODEL_VAR) or DEFAULT_MODEL
    off = value(SWITCH_VAR).lower() in ("off", "0", "false", "no")
    return Director(runner=None if off else cli_runner(model), model=model,
                    log_path=Path(value(LOG_VAR) or DEFAULT_LOG),
                    clock=clock or SystemClock())
