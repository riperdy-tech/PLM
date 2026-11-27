"""The Claude director (owner 2026-11-27): Claude writes each song's Styles text and
suggests the next sub-style; the code checks every text before it is used.

A fake runner stands in for the ``claude`` CLI (no real Claude call in tests). The song
sheets and their ``songs.json`` are written by P4's own Suno adapter, so the director is
proven against the format P4 writes and re-renders.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from plm_platform.clock import FixedClock
from plm_platform.dashboard.director import (
    Director,
    DirectorUnavailable,
    Facts,
    Reply,
    claude_argv,
    director_from_env,
    exclude_problem,
    parse_cli_output,
    styles_problem,
    waiting_songs,
)

from tests.unit.test_dashboard_studio import _studio

NOW = datetime(2026, 11, 27, 9, tzinfo=UTC)
FACTS = Facts(bpm=84, key="D minor", moods=("focused", "calm"), seconds=180,
              instrumental=True)
GOOD = ("instrumental, no vocals, focused calm mood, 84 BPM, key of D minor, soft "
        "felt piano lead over a dusty half-time beat, warm tape hiss")


# -- the checks ------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    GOOD,
    "Instrumental only, no vocals, about 180 seconds long. deep pads, dreamy mood.",
    "instrumental, without vocals, glassy arpeggios, no vocals or lyrics, no choir",
    "instrumental, no vocals, 84bpm, rolling sub bass, hopeful and uplifting",
])
def test_a_text_that_keeps_the_facts_is_accepted(text: str) -> None:
    assert styles_problem(text, FACTS) is None


@pytest.mark.parametrize(("text", "why"), [
    ("soft piano, no vocals, 84 BPM", "instrumental"),
    ("instrumental, warm piano, 84 BPM", "no vocals"),
    ("instrumental, no vocals, airy female vocals in the chorus", "vocal"),
    ("instrumental, no vocals, a rap verse over pads", "vocal"),
    ("instrumental, no vocals, distant choir swells", "vocal"),
    ("instrumental, no vocals, sung hook", "vocal"),
    ("instrumental, no vocals, dark mood, heavy pads", "mood"),
    ("instrumental, no vocals, melancholic piano", "mood"),
    ("instrumental, no vocals, 90 BPM groove", "BPM"),
    ("instrumental, no vocals, key of E minor", "key"),
    ("instrumental, no vocals, about 240 seconds long", "length"),
    ("instrumental, no vocals, " + "soft pads, " * 100, "1000"),
    ("instrumental, no vocals, see https://example.com for the sound", "URL"),
    ("instrumental, no vocals, in the style of a famous producer", "artist"),
    ("instrumental, no vocals, sounds like Burial", "artist"),
    ("instrumental, no vocals, like Tycho with warm pads", "artist"),
])
def test_a_text_that_breaks_a_rule_is_refused(text: str, why: str) -> None:
    problem = styles_problem(text, FACTS)
    assert problem is not None and why.lower() in problem.lower(), problem


def test_a_named_key_is_refused_when_the_spec_names_no_key() -> None:
    open_key = Facts(bpm=84, key=None, moods=("calm",), seconds=180, instrumental=True)
    assert styles_problem("instrumental, no vocals, A minor pads", open_key) is not None
    assert styles_problem("instrumental, no vocals, minor pads", open_key) is None


def test_an_exclude_may_name_vocals_but_not_the_instrumental_itself() -> None:
    assert exclude_problem("vocals, harsh hi-hats, sudden drops") is None
    assert exclude_problem("instrumental, piano") is not None
    assert exclude_problem("x" * 1001) is not None
    assert exclude_problem("anything like Burial") is not None


# -- song sheets written by P4 -------------------------------------------------------------------


def _p4_sheet(inbox: Path, track: str = "trk_1", *, plan: str = "gen_plan_1") -> Any:
    """P4's Suno adapter writes the sheet and songs.json for two instrumental songs."""
    from music_factory.domain.models import GenerationRequest
    from music_factory.providers.suno_manual import SunoManualAdapter, SunoManualSettings

    adapter = SunoManualAdapter(SunoManualSettings(inbox=inbox, model_version="v4.5"))
    requests = [GenerationRequest(
        track_spec_id=track, experiment_id="exp_1", vocal_mode="instrumental",
        plan_id=plan, variant_code=f"v{n}", duration_seconds=180,
        duration_min_seconds=150, duration_max_seconds=210,
        negative_prompt="low quality, harsh frequencies",
        prompt=(f"ELECTRONIC track. mood: focused, calm. for deep work. 84 BPM. "
                f"instrumental, no vocals. harmonic key: D_minor. energy: low. "
                f"foreground lead: lead_{n}.")) for n in (1, 2)]
    adapter.awaiting_audio(requests)
    return adapter, requests


class FakeRunner:
    def __init__(self, answer: Mapping[str, Any] | Exception) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    def __call__(self, system: str, prompt: str, schema: Mapping[str, Any],
                 timeout: float) -> Reply:
        self.calls.append({"system": system, "prompt": prompt, "schema": schema,
                           "timeout": timeout})
        if isinstance(self.answer, Exception):
            raise self.answer
        return Reply(dict(self.answer), "claude-test-model")


def _director(tmp_path: Path, runner: Any) -> Director:
    return Director(runner=runner, model="sonnet", log_path=tmp_path / "director" / "log.jsonl",
                    clock=FixedClock(NOW), spawn=lambda job: job())


def _answer(stems: list[str], texts: list[str] | None = None) -> dict[str, Any]:
    texts = texts or [GOOD, GOOD.replace("felt piano", "glass marimba")]
    return {"songs": [{"stem": s, "styles": t, "exclude": "vocals, harsh hi-hats"}
                      for s, t in zip(stems, texts, strict=True)],
            "review": "Song 1 leads with piano, song 2 with marimba; both away from the "
                      "last video's pads."}


class _Store:
    def __init__(self) -> None:
        self.runs_list: list[dict[str, Any]] = [
            {"workflow_id": "wf_old", "experiment_id": "exp_0", "state": "PUBLISHED",
             "style": "minimal_pulse", "title": "Old Focus",
             "created_at": "2026-11-20T10:00:00+00:00",
             "updated_at": "2026-11-21T10:00:00+00:00"},
            {"workflow_id": "wf_1", "experiment_id": "exp_1", "state": "HELD",
             "style": "downtempo_synth", "title": None, "length": [5400, 6000],
             "created_at": "2026-11-27T08:00:00+00:00",
             "updated_at": "2026-11-27T08:30:00+00:00"}]

    def runs(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.runs_list]

    def track_ids(self, experiment_id: str) -> list[str]:
        return {"exp_1": ["trk_1"], "exp_0": ["trk_0"]}.get(experiment_id, [])

    def transitions(self, workflow_id: str) -> list[dict[str, Any]]:
        return []

    def packages(self, experiment_id: str) -> list[dict[str, Any]]:
        return []


SONGS_HOLD = {"case_id": "case_songs", "reason_code": "MANUAL_AUDIO_AWAITED",
              "pipeline": {"run_state": "HELD", "workflow_id": "wf_1"}}


def _wired(tmp_path: Path, runner: Any) -> tuple[Any, Path]:
    studio, _ = _studio(tmp_path, [SONGS_HOLD])
    music = tmp_path / "music"
    (music / "trk_1.prompts.txt").unlink()  # the fixture's hand-written sheet
    _p4_sheet(music)
    (music / "imported" / "gen_plan_0").mkdir(parents=True)  # the last video, archived
    _p4_sheet(music / "imported" / "gen_plan_0", "trk_0", plan="gen_plan_0")
    studio.store = _Store()
    studio.director = _director(tmp_path, runner)
    return studio, music


def test_claude_writes_each_songs_styles_once_and_p4_keeps_showing_them(
        tmp_path: Path) -> None:
    runner = FakeRunner({})
    studio, music = _wired(tmp_path, runner)
    stems = [s.stem for s in waiting_songs([music], ["trk_1"])]
    assert stems == ["trk_1-v1", "trk_1-v2"]
    runner.answer = _answer(stems)

    studio.projects()  # the list page's poll starts the director for the waiting run

    (call,) = runner.calls
    sent = call["prompt"]
    assert "84" in sent and "D minor" in sent and "Downtempo synth" in sent
    assert "lead 1" in sent  # the template's Styles text
    assert "Old Focus" in sent and "lead 2" in sent  # the last video's prompts
    record = json.loads((music / "trk_1.songs.json").read_text(encoding="utf-8"))
    assert record["written_by"] == "claude"
    assert record["director"]["model"] == "claude-test-model"
    assert record["director"]["at"] == NOW.isoformat()
    assert "marimba" in record["director"]["review"]
    first = record["songs"]["v1"]
    assert first["written_by"] == "claude" and "lead 1" in first["template_prompt"]
    assert first["lines"][1] == GOOD
    assert first["lines"][0].startswith(f"Styles ({len(GOOD)} characters;")
    assert "Exclude styles: vocals, harsh hi-hats" in first["lines"]
    sheet = (music / "trk_1.prompts.txt").read_text(encoding="utf-8")
    assert GOOD in sheet and "glass marimba" in sheet and "lead 1" not in sheet

    # The Studio shows the texts and who wrote them.
    page = studio.project(2)
    (shown,) = page["songs"]["sheets"]
    assert shown["director"]["written_by"] == "claude"
    assert "marimba" in shown["director"]["review"]
    assert shown["songs"][0]["fields"][1] == {"label": "Styles", "text": GOOD}

    # Stored once: another poll, or a restarted dashboard, asks Claude nothing.
    studio.projects()
    studio.director = _director(tmp_path, runner)
    studio.projects()
    assert len(runner.calls) == 1

    # P4 re-renders the sheet from songs.json: Claude's text stays.
    _p4_sheet(music)
    assert GOOD in (music / "trk_1.prompts.txt").read_text(encoding="utf-8")

    log = [json.loads(line) for line in
           (tmp_path / "director" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(e["what"], e["ok"], e["accepted"]) for e in log] == [("song_prompts", True, 2)]
    assert "seconds" in log[0]


def test_a_song_whose_text_fails_a_check_keeps_the_template(tmp_path: Path) -> None:
    runner = FakeRunner({})
    studio, music = _wired(tmp_path, runner)
    before = json.loads((music / "trk_1.songs.json").read_text(encoding="utf-8"))
    runner.answer = _answer(["trk_1-v1", "trk_1-v2"],
                            [GOOD, GOOD.replace("84 BPM", "96 BPM")])
    studio.projects()
    record = json.loads((music / "trk_1.songs.json").read_text(encoding="utf-8"))
    assert record["songs"]["v1"]["written_by"] == "claude"
    second = record["songs"]["v2"]
    assert second["written_by"] == "template" and "BPM" in second["director_refused"]
    assert second["lines"] == before["songs"]["v2"]["lines"]
    (entry,) = [json.loads(x) for x in (tmp_path / "director" / "log.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert entry["accepted"] == 1 and "BPM" in entry["refused"]["trk_1-v2"]


@pytest.mark.parametrize("failure", [
    DirectorUnavailable("timeout", "no answer in 180 s"),
    DirectorUnavailable("claude_error", "usage limit reached"),
    {"songs": "not a list"},
    {"review": "no songs at all"},
])
def test_when_claude_fails_every_template_text_is_kept(tmp_path: Path, failure: Any) -> None:
    runner = FakeRunner(failure)
    studio, music = _wired(tmp_path, runner)
    sheet_before = (music / "trk_1.prompts.txt").read_text(encoding="utf-8")
    lines_before = json.loads((music / "trk_1.songs.json").read_text(
        encoding="utf-8"))["songs"]["v1"]["lines"]
    studio.projects()
    assert (music / "trk_1.prompts.txt").read_text(encoding="utf-8") == sheet_before
    record = json.loads((music / "trk_1.songs.json").read_text(encoding="utf-8"))
    assert record["songs"]["v1"]["lines"] == lines_before
    assert record["written_by"] == "template" and record["director"]["reason"]
    (entry,) = [json.loads(x) for x in (tmp_path / "director" / "log.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert entry["ok"] is False and entry["fallback"]
    studio.projects()
    assert len(runner.calls) == 1  # not asked again: the sheet stays as the owner sees it


def test_a_saved_song_is_never_rewritten(tmp_path: Path) -> None:
    runner = FakeRunner({})
    _, music = _wired(tmp_path, runner)
    (music / "trk_1-v1.wav").write_bytes(b"RIFF....WAVE")
    assert [s.stem for s in waiting_songs([music], ["trk_1"])] == ["trk_1-v2"]


# -- the Start menu's suggestion ----------------------------------------------------------------


def _start_studio(tmp_path: Path, runner: Any) -> Any:
    studio, _ = _studio(tmp_path, [])
    studio.store = _Store()
    studio.styles = lambda: {"available": ["minimal_pulse", "organic_electronic"],
                             "recent": [{"subgenre": "minimal_pulse", "at": "2026-11-26"}]}
    studio.director = _director(tmp_path, runner)
    return studio


def test_start_shows_claudes_suggested_style_from_p2s_list(tmp_path: Path) -> None:
    runner = FakeRunner({"subgenre": "organic_electronic",
                         "reason": "The last video was minimal pulse; organic textures contrast."})
    studio = _start_studio(tmp_path, runner)
    suggestion = studio.start_options()["styles"]["suggestion"]
    assert suggestion["state"] == "ready" and suggestion["subgenre"] == "organic_electronic"
    assert "minimal pulse" in suggestion["reason"]
    assert runner.calls[0]["schema"]["properties"]["subgenre"]["enum"] == [
        "minimal_pulse", "organic_electronic"]
    assert "Old Focus" in runner.calls[0]["prompt"]
    studio.start_options()
    assert len(runner.calls) == 1  # cached until the list of last videos changes


def test_a_suggestion_outside_p2s_list_is_not_shown(tmp_path: Path) -> None:
    runner = FakeRunner({"subgenre": "future_garage_focus", "reason": "fresh"})
    studio = _start_studio(tmp_path, runner)
    suggestion = studio.start_options()["styles"]["suggestion"]
    assert suggestion["state"] == "unavailable" and "subgenre" not in suggestion


def test_the_start_page_never_waits_for_claude(tmp_path: Path) -> None:
    jobs: list[Any] = []
    studio = _start_studio(tmp_path, FakeRunner({"subgenre": "minimal_pulse", "reason": "r"}))
    studio.director.spawn = jobs.append  # the job is queued, not run
    assert studio.start_options()["styles"]["suggestion"] == {"state": "thinking"}
    jobs[0]()
    assert studio.start_options()["styles"]["suggestion"]["subgenre"] == "minimal_pulse"


# -- off switch and the CLI ---------------------------------------------------------------------


def test_the_director_can_be_turned_off(tmp_path: Path) -> None:
    off = director_from_env({"PLM_STUDIO_DIRECTOR": "off"}, dotenv_path=tmp_path / "none.env")
    assert not off.enabled
    on = director_from_env({}, dotenv_path=tmp_path / "none.env")
    assert on.enabled and on.model
    runner = FakeRunner(_answer(["trk_1-v1", "trk_1-v2"]))
    studio, music = _wired(tmp_path, runner)
    studio.director = off
    studio.projects()
    assert runner.calls == []
    assert "written_by" not in json.loads((music / "trk_1.songs.json").read_text(
        encoding="utf-8"))
    assert studio.start_options()["styles"].get("suggestion") is None


def test_the_cli_runs_headless_without_tools_or_project_context() -> None:
    argv = claude_argv(["claude.exe"], "sonnet", "be brief", {"type": "object"})
    assert argv[:2] == ["claude.exe", "-p"]
    for flag in ("--output-format", "--json-schema", "--model", "--tools",
                 "--no-session-persistence", "--safe-mode", "--system-prompt"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--output-format") + 1] == "json"


def test_the_cli_answer_is_read_from_its_structured_output() -> None:
    out = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "result": "", "structured_output": {"subgenre": "x", "reason": "y"},
                      "modelUsage": {"claude-sonnet-test": {}}})
    reply = parse_cli_output(out, 0, "")
    assert reply.answer == {"subgenre": "x", "reason": "y"}
    assert reply.model == "claude-sonnet-test"
    with pytest.raises(DirectorUnavailable, match="usage"):
        parse_cli_output(json.dumps({"type": "result", "is_error": True,
                                     "result": "Claude usage limit reached"}), 1, "")
    with pytest.raises(DirectorUnavailable, match="invalid_json"):
        parse_cli_output("not json", 0, "")


def test_the_cli_timeout_is_a_fallback(tmp_path: Path) -> None:
    from plm_platform.dashboard.director import cli_runner

    def slow(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="claude", timeout=kwargs["timeout"])

    runner = cli_runner("sonnet", command=["claude.exe"], run=slow)
    with pytest.raises(DirectorUnavailable, match="timeout"):
        runner("s", "p", {"type": "object"}, 0.1)
