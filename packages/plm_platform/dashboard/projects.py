"""Projects: every video the Studio has made, numbered, with its whole story kept.

Owner 2026-11-09/10: a video project needs a number to find it again; every step
(prompts, songs, pictures, video) stays visible after it is done; the run's progress is a
timestamped log in plain words; and any stop says what happened and what to do, loudly.

A project is a pipeline run (``ops.pipeline_runs``); test-harness runs
(``pipeline.gate-*``) are not projects. Its number is its place among projects by start
time, so it never changes: runs are never deleted (their transitions are append-only).
The log is the run's own append-only transition record (``ops.pipeline_transitions``),
translated line by line; nothing is kept twice.

This module is pure (rows in, dicts out) apart from :class:`ProjectStore`, which reads
the rows from the shared database read-only.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

__all__ = [
    "HOLD_HELP",
    "ProjectStore",
    "explain_hold",
    "number_runs",
    "plain_log",
    "state_label",
]

TEST_RUN_PREFIX: Final[str] = "pipeline.gate-"

STATE_LABELS: Final[dict[str, str]] = {
    "IDEA": "Planning the video", "RESEARCHED": "Planning the video",
    "POLICY_CHECKED": "Planning the video", "GENERATING": "Preparing the song prompts",
    "GENERATED": "Checking the songs", "QA": "Checking the songs",
    "PASSED": "Checking the songs",
    "ASSEMBLING": "Building the playlist and the video",
    "PACKAGED": "Video built", "UPLOADING": "Uploading to YouTube",
    "PROCESSING": "YouTube is processing the upload",
    "PUBLISHED": "Published", "ANALYZING": "Published (collecting results)",
    "LEARNING": "Published (learning from results)", "FAILED": "Failed",
    "CANCELLED": "Cancelled", "REJECTED": "Rejected", "HELD": "Stopped",
}
FINISHED: Final = frozenset({"PUBLISHED", "ANALYZING", "LEARNING", "FAILED", "CANCELLED",
                             "REJECTED"})

# What each kind of stop means, in the owner's words, and what the Studio offers for it.
# ``action``: "songs" / "art" / "review" are the owner's own steps (answered on the
# project page); "retry" resumes the hold with the reason recorded as the evidence (the
# owner read the reason and chose to try again); None: nothing the Studio can do.
HOLD_HELP: Final[dict[str, dict[str, Any]]] = {
    "MANUAL_AUDIO_AWAITED": {
        "title": "Waiting for you: make the songs",
        "what": "The song prompts are ready. Make each song, upload it, then press Continue.",
        "action": "songs"},
    "THUMBNAIL_ART_AWAITED": {
        "title": "Waiting for you: make the pictures",
        "what": "The playlist is built. Make pictures A, B and C from the prompts, upload "
                "them, then press Import. Picture A is also the video's picture.",
        "action": "art"},
    "PUBLISH_APPROVAL_AWAITED": {
        "title": "Waiting for you: review and approve",
        "what": "The video is ready. Watch it, check the title and pictures, then approve "
                "the upload or request changes.",
        "action": "review"},
    "PLAYLIST_SUPPLY_SHORT": {
        "title": "Stopped: not enough songs for the length",
        "what": "The songs that passed are shorter in total than the video's length. "
                "Try again asks for more song prompts.",
        "action": "retry"},
    "STEP_PLATFORM_FAULT": {
        "title": "Stopped: a step was refused",
        "what": "A step failed or was refused. The reason is below. Fix what it names "
                "(or ask Claude), then try again.",
        "action": "retry"},
    "BUDGET_RESERVATION_REFUSED": {
        "title": "Stopped: the spending guard refused a step",
        "what": "The guard that checks every paid or public action refused one. The "
                "reason is below.",
        "action": "retry"},
}


def state_label(state: str, hold_reason: str | None) -> str:
    if state == "HELD":
        known = HOLD_HELP.get(hold_reason or "")
        return known["title"] if known else f"Stopped: {hold_reason or 'unknown reason'}"
    return STATE_LABELS.get(state, state.title())


def number_runs(runs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The projects, oldest first, each with its ``number`` (1, 2, ...)."""
    real = [dict(r) for r in runs if not str(r["workflow_id"]).startswith(TEST_RUN_PREFIX)]
    real.sort(key=lambda r: (_iso(r["created_at"]), str(r["workflow_id"])))
    for number, run in enumerate(real, start=1):
        run["number"] = number
    return real


def explain_hold(case: Mapping[str, Any], *, owner_roles: Iterable[str] = ()) -> dict[str, Any]:
    """A stop in plain words: what it is, the system's own reason, what to do, and
    whether the owner may act on it (the hold's reviewer role)."""
    reason = str(case.get("reason_code") or "")
    known = HOLD_HELP.get(reason)
    details = case.get("details") or {}
    detail = (details.get("error") if isinstance(details, Mapping) else None) or ""
    role = case.get("reviewer_role")
    allowed = role in set(owner_roles)
    action = None if known is None else known["action"]
    if known is None:
        action = "retry" if case.get("hold_class") != "HARD_STOP" else None
    return {
        "case_id": case.get("case_id"), "reason_code": reason,
        "title": known["title"] if known else f"Stopped: {reason.replace('_', ' ').lower()}",
        "what": known["what"] if known else "The run stopped. The reason is below.",
        "detail": str(detail), "since": case.get("raised_at"),
        "action": action, "role": role, "owner_may_act": allowed,
        "blocked_because": None if allowed else (
            f"Only someone with the '{role}' role may continue this stop, and the owner "
            f"does not have it. Ask Claude to look at it."),
        "your_turn": action in ("songs", "art", "review"),
    }


# -- the log ---------------------------------------------------------------------------------

_STEP_DONE: Final[dict[str, str]] = {
    "research": "P2 chose what to make",
    "policy_check": "P3 designed the video",
    "generate": "Songs received",
    "qa": "Songs checked",
    "extend": "Asked P3 for more songs",
    "assemble": "Playlist and video built",
    "upload": "Uploaded to YouTube",
    "submit_distribution": "Sent to distribution",
}
_ENTER: Final[dict[str, str]] = {
    "GENERATING": "Preparing the song prompts",
    "QA": "Checking the songs",
    "ASSEMBLING": "Building the playlist and the video",
    "UPLOADING": "Uploading to YouTube",
}
_CONTINUES: Final[dict[str, str]] = {
    "manual_audio_inbox_ref": "You sent the songs",
    "thumbnail_art_inbox_ref": "You imported the pictures",
    "publish_approval_ref": "You approved the upload",
    "supply_review_ref": "You asked for more songs",
    "platform_fix_ref": "Fixed and continued",
    "budget_approval_ref": "Spending guard cleared and continued",
}
_WAITS: Final[dict[str, str]] = {
    "manual_audio_awaited": "Waiting for you: songs",
    "thumbnail_art_awaited": "Waiting for you: pictures",
    "publish_approval_awaited": "Waiting for you: review and approval",
}


def _outputs(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("outputs_json")
    if isinstance(raw, str):
        raw = json.loads(raw or "{}")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _count(outputs: Mapping[str, Any], key: str) -> int | None:
    found = outputs.get(key)
    return len(found) if isinstance(found, list) else None


def plain_log(transitions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The run's transitions as plain lines, oldest first: ``{at, text, kind, detail}``;
    ``kind`` is ``step`` (work started), ``done``, ``wait`` (the owner's turn),
    ``problem`` (a stop or a refused step) or ``you`` (the owner's own action)."""
    lines: list[dict[str, Any]] = []
    for row in transitions:
        at = _iso(row.get("occurred_at"))
        to, step = str(row.get("to_state") or ""), row.get("step")
        code, detail = row.get("error_code"), row.get("error_detail")
        out = _outputs(row)
        if to == "IDEA":
            lines.append({"at": at, "kind": "step", "text": "Project started"})
        elif row.get("from_state") == "HELD" and to != "HELD":
            verdicts = out.get("verdicts") or []
            if out.get("exclude_candidates"):
                text = (f"You replaced {len(out['exclude_candidates'])} song(s); "
                        "going back to make new ones")
            elif out.get("rewind_to"):
                text = "You sent it back to rebuild the video"
            elif any(not v.get("allowed") for v in verdicts if isinstance(v, Mapping)):
                text = "A continue was refused"
            else:
                keys = set(out.get("evidence_keys") or [])
                text = next((said for key, said in _CONTINUES.items() if key in keys),
                            "You continued" if out.get("actor") == "owner" else "Continued")
            lines.append({"at": at, "kind": "you", "text": text})
        elif to == "HELD":
            wait = _WAITS.get(str(code or ""))
            if wait:
                lines.append({"at": at, "kind": "wait", "text": wait})
            else:
                lines.append({"at": at, "kind": "problem",
                              "text": f"Stopped: {str(code or 'unknown').replace('_', ' ').lower()}",
                              "detail": detail})
        elif code:
            text = {"playlist_supply_short": "The songs were too short in total for the "
                                             "video's length",
                    "package_extension_limit_exceeded": "P3 refused to add more songs "
                                                        "(limit reached)"}.get(
                str(code), f"Step refused: {str(code).replace('_', ' ')}")
            lines.append({"at": at, "kind": "problem", "text": text, "detail": detail})
        elif step in _STEP_DONE and to not in ("HELD",):
            text = _STEP_DONE[str(step)]
            songs = _count(out, "candidate_ids")
            if step == "generate" and songs is not None:
                text = f"{songs} song(s) received"
            elif step == "qa":
                passed = sum(1 for e in out.get("evaluations") or []
                             if isinstance(e, Mapping)
                             and isinstance(e.get("disposition"), str)
                             and e["disposition"].startswith("PASSED"))
                total = _count(out, "evaluations")
                if total:
                    text = f"Songs checked: {passed} of {total} passed"
            elif step == "extend" and out.get("count"):
                text = f"Asked P3 for {out['count']} more song(s)"
            elif step == "upload":
                video = next((p.get("youtube_video_id") for p in out.get("publications") or []
                              if isinstance(p, Mapping)), None)
                text = f"Uploaded to YouTube ({video})" if video else text
            lines.append({"at": at, "kind": "done", "text": text})
        elif to in _ENTER and not step:
            lines.append({"at": at, "kind": "step", "text": _ENTER[to]})
        elif to in ("CANCELLED", "FAILED", "REJECTED", "PUBLISHED"):
            lines.append({"at": at, "kind": "problem" if to != "PUBLISHED" else "done",
                          "text": STATE_LABELS[to], "detail": detail})
    return lines


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return "" if value is None else str(value)


# -- the database --------------------------------------------------------------------------


@dataclass
class ProjectStore:
    """Reads projects from the shared database (read-only)."""

    engine: Any

    def _rows(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        import sqlalchemy as sa

        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(sa.text(sql), dict(params or {})).mappings()]

    def runs(self) -> list[dict[str, Any]]:
        """Every run with the facts its list row shows: experiment, style, length,
        newest package and title, YouTube video."""
        runs = self._rows(
            "select workflow_id, state, held_from, last_error_code, last_error_detail, "
            "created_at, updated_at, completed_at from ops.pipeline_runs "
            "where workflow_id not like :test order by created_at, workflow_id",
            {"test": f"{TEST_RUN_PREFIX}%"})
        experiments = {r["workflow_id"]: r for r in self._rows(
            "select t.workflow_id, t.outputs_json::json->>'experiment_id' as experiment_id, "
            "t.outputs_json::json->'package_ids'->>0 as p3_package_id "
            "from ops.pipeline_transitions t where t.step = 'policy_check' "
            "and t.to_state = 'POLICY_CHECKED'")}
        facts = {r["experiment_id"]: r for r in self._rows(
            "select e.experiment_id, g.subgenre, s.duration_min_seconds, "
            "s.duration_max_seconds, v.visual_world from experiments.experiments e "
            "left join experiments.content_genomes g on g.fingerprint = e.base_genome_fingerprint "
            "left join experiments.playlist_specs s on s.experiment_id = e.experiment_id "
            "left join experiments.visual_specs v on v.experiment_id = e.experiment_id")}
        packages: dict[str, dict[str, Any]] = {}
        for row in self._rows(
                "select experiment_id, media_package_id, status, video_duration_seconds, "
                "metadata_candidates_json, created_at from media.media_packages "
                "order by created_at"):
            packages[str(row["experiment_id"])] = row  # newest wins
        videos = {r["media_package_id"]: r for r in self._rows(
            "select media_package_id, youtube_video_id, video_url, privacy_status, "
            "published_at from youtube.publication_records")}
        # P7 writes the publication record only when the whole publish is verified; an
        # upload that went up but stopped before (thumbnail, processing) is still on
        # YouTube, and the Studio says so (2026-11-10: jCn1eokpryw).
        for r in self._rows(
                "select m.media_package_id, a.youtube_video_id, a.state, m.privacy_status, "
                "a.updated_at as published_at from youtube.publish_attempts a "
                "join youtube.publish_manifests m on m.manifest_id = a.manifest_id "
                "where a.youtube_video_id is not null order by a.updated_at"):
            if r["media_package_id"] not in videos or videos[r["media_package_id"]].get(
                    "unfinished"):
                done = r["state"] in ("verified", "published", "scheduled")
                videos[r["media_package_id"]] = {**r, "video_url": None,
                                                 "unfinished": None if done else r["state"]}
        for run in runs:
            exp = experiments.get(run["workflow_id"]) or {}
            run["experiment_id"] = exp.get("experiment_id")
            run["p3_package_id"] = exp.get("p3_package_id")
            fact = facts.get(exp.get("experiment_id")) or {}
            run["style"] = fact.get("subgenre")
            run["length"] = ([fact["duration_min_seconds"], fact["duration_max_seconds"]]
                             if fact.get("duration_min_seconds") else None)
            run["visual_world"] = fact.get("visual_world")
            pkg = packages.get(str(exp.get("experiment_id"))) or {}
            run["media_package_id"] = pkg.get("media_package_id")
            run["package_status"] = pkg.get("status")
            run["duration_seconds"] = pkg.get("video_duration_seconds")
            run["title"] = _title(pkg.get("metadata_candidates_json"))
            video = videos.get(pkg.get("media_package_id")) or {}
            run["youtube"] = ({"video_id": video["youtube_video_id"],
                               "url": video.get("video_url")
                               or f"https://youtu.be/{video['youtube_video_id']}",
                               "privacy": video.get("privacy_status"),
                               "published_at": _iso(video.get("published_at")),
                               "unfinished": video.get("unfinished")}
                              if video.get("youtube_video_id") else None)
        return runs

    def transitions(self, workflow_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "select sequence, occurred_at, from_state, to_state, held_from, step, attempt, "
            "error_code, error_detail, outputs_json::text as outputs_json, "
            "hold_case_ids_json::text as hold_case_ids_json from ops.pipeline_transitions "
            "where workflow_id = :w order by sequence", {"w": workflow_id})

    def track_ids(self, experiment_id: str) -> list[str]:
        """The P4 tracks (song sheets) of the experiment, in the order they were asked."""
        return [str(r["track_spec_id"]) for r in self._rows(
            "select track_spec_id, min(created_at) as at from music.generation_plans "
            "where experiment_id = :e group by track_spec_id order by at, track_spec_id",
            {"e": experiment_id})]

    def packages(self, experiment_id: str) -> list[dict[str, Any]]:
        """The experiment's P6 packages, newest first (a song swap makes a new one)."""
        return self._rows(
            "select media_package_id, status, created_at from media.media_packages "
            "where experiment_id = :e order by created_at desc", {"e": experiment_id})


def _title(raw: Any) -> str | None:
    try:
        found = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if isinstance(found, list) and found and isinstance(found[0], Mapping):
        title = found[0].get("title")
        return str(title) if title else None
    return None
