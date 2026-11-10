"""P3 plans supply to the declared playlist range, extends it, and replays by key (R5).

Owner decision 2026-10-15: a playlist video's length is a declared range and its target
is only the planning midpoint. ADR-0044 (2026-11-01, revised the same day) set the policy to 20–30 minutes
(the ``20_30m`` bucket) for this brand. These
tests run P3's own code (planner, genome engine, HTTP routes over SQLite); they prove P3's
behaviour, not that P4–P6 consume it (the integration and media tests cover that).
"""

from __future__ import annotations

import copy
import json
import math
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from plm_contracts.seams.c3 import DurationRangeSeconds
from plm_platform.config import load_settings
from sqlalchemy.orm import sessionmaker

from services.experiments.api.app import create_app
from services.experiments.config import load_experiments_settings
from services.experiments.domain.genome import DurationRange
from services.experiments.genome.engine import (
    TEMPLATES_PATH,
    TemplateLibrary,
    TemplateLibraryError,
    brief_playlist_duration,
    build_genome_from_directive,
)
from services.experiments.ingestion.p2_adapter import adapt_directive
from services.experiments.materialization.supply import (
    SupplyPolicy,
    default_supply_policy,
    plan_supply,
)
from tests.support.experiments_db import create_experiments_engine
from tests.unit.experiments.test_api_endpoints import ENV_WITH_TOKEN, SAMPLE_DIRECTIVE

HEADERS = {"X-PLM-Service-Token": "experiments-seam-token"}


def _client() -> TestClient:
    settings = load_experiments_settings(platform=load_settings(env=dict(ENV_WITH_TOKEN)))
    factory = sessionmaker(bind=create_experiments_engine(static_pool=True))
    return TestClient(create_app(session_factory=factory, settings=settings))


def _directive(**brief: Any) -> dict[str, Any]:
    directive = copy.deepcopy(SAMPLE_DIRECTIVE)
    directive["brief"].update(brief)
    return directive


def _raw_library() -> dict[str, Any]:
    with TEMPLATES_PATH.open(encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def _validated_package(client: TestClient, *, key: str | None = None) -> dict[str, Any]:
    created = client.post("/experiments/v1/experiments", headers=HEADERS,
                          json={"directive": SAMPLE_DIRECTIVE, "seed": 7})
    assert created.status_code == 200, created.text
    experiment = created.json()["experiment"]
    exp_id = experiment["experiment_id"]
    assert client.post(f"/experiments/v1/experiments/{exp_id}/validate",
                       headers=HEADERS).status_code == 200
    headers = HEADERS if key is None else {**HEADERS, "Idempotency-Key": key}
    materialized = client.post(
        f"/experiments/v1/experiments/{exp_id}/materialize", headers=headers,
        json={"treatment_id": experiment["treatments"][0]["treatment_id"]})
    assert materialized.status_code == 200, materialized.text
    package: dict[str, Any] = materialized.json()["package"]
    return package


# -- planning ----------------------------------------------------------------------------


def test_sixty_minute_target_with_three_minute_tracks_plans_enough_specs_plus_spares() -> None:
    """Proves the planning rule: a 45–75 min range (60 min midpoint) with 180 s tracks
    plans ceil(2700/180) = 15 base specs, which reach the range minimum on their own, plus
    ceil(15 * 0.25) = 4 declared spares; the plan still reaches the minimum after P5
    rejects every spare's worth of tracks."""
    policy = default_supply_policy()
    assert policy.spare_ratio == Decimal("0.25")
    plan = plan_supply(DurationRange(min_seconds=2700, max_seconds=4500), 180, policy)

    assert plan.target_duration_seconds == 3600
    assert plan.base_track_count == 15
    assert plan.spare_track_count == 4
    assert plan.track_count == 19
    assert plan.base_track_count * 180 >= plan.duration_range_seconds.min_seconds
    assert (plan.track_count - plan.spare_track_count) * 180 >= 2700
    assert plan.max_extension_track_count == math.ceil(19 * Decimal("0.5"))
    assert plan.supply_policy_version == policy.supply_policy_version


def test_spare_ratio_is_versioned_data_not_a_constant() -> None:
    """Proves the spare count follows the declared policy, not a fixed default."""
    none = SupplyPolicy(supply_policy_version="t0", spare_ratio=Decimal(0),
                        max_extension_ratio=Decimal(0))
    half = SupplyPolicy(supply_policy_version="t50", spare_ratio=Decimal("0.5"),
                        max_extension_ratio=Decimal(0))
    playlist = DurationRange(min_seconds=2700, max_seconds=4500)
    assert plan_supply(playlist, 180, none).track_count == 15
    assert plan_supply(playlist, 180, half).track_count == 15 + 8


# -- the range comes from the genome or the directive bucket -----------------------------


def test_directive_bucket_sets_the_playlist_range() -> None:
    """Proves ``duration_bucket: 20_30m`` makes the genome's playlist range 1200–1800 s
    (the policy range, ADR-0044), with the midpoint as target."""
    directive = adapt_directive(_directive(duration_bucket="20_30m"))
    genome = build_genome_from_directive(directive)
    assert (genome.playlist_duration.min_seconds, genome.playlist_duration.max_seconds,
            genome.playlist_duration.target_seconds) == (1200, 1800, 1500)
    assert brief_playlist_duration(directive)[1] is None


def test_without_a_bucket_the_policy_range_applies() -> None:
    """Proves an open bucket gets the owner's policy range (20-30 min, ADR-0036 as
    amended by ADR-0044), not
    the genre template's. R5: the lofi, jazz and ambient templates declared 60-120 and
    60-180 min, so a directive naming no bucket (P1's CI arms name none) planned a
    playlist outside the owner's decision."""
    for genre in ("rnb", "lofi", "jazz", "ambient", "focus_electronic"):
        directive = _directive(duration_bucket=None, genre=genre)
        genome = build_genome_from_directive(adapt_directive(directive))
        assert (genome.playlist_duration.min_seconds, genome.playlist_duration.max_seconds,
                genome.playlist_duration.target_seconds) == (1200, 1800, 1500), genre


def test_a_bucket_outside_the_policy_is_made_at_the_policy_length() -> None:
    """Owner decision 2026-10-31 (amends ADR-0036): an arm whose bucket lies outside the
    policy's allowed lengths (20-120 min since 2026-11-10) is produced at the default
    range, and the experiment records it."""
    directive = adapt_directive(_directive(duration_bucket="10_45m"))
    genome = build_genome_from_directive(directive)
    assert (genome.playlist_duration.min_seconds, genome.playlist_duration.max_seconds) == (
        1200, 1800)
    assert brief_playlist_duration(directive)[1] == "duration_bucket_mapped:10_45m->policy"

    response = _client().post("/experiments/v1/experiments", headers=HEADERS,
                              json={"directive": _directive(duration_bucket="10_45m")})
    assert response.status_code == 200
    assert "duration_bucket_mapped:10_45m->policy" in response.json()["experiment"][
        "design_notes"]


@pytest.mark.parametrize(("bucket", "expected"), [
    ("20_30m", (1200, 1800)), ("45_75m", (2700, 4500)), ("90_120m", (5400, 7200))])
def test_the_owners_length_choice_sets_the_playlist_range(
        bucket: str, expected: tuple[int, int]) -> None:
    """Owner 2026-11-10: the length chosen at Start (P2 puts only that in the brief) is
    the playlist range, unmapped; no choice gets the default 20-30 min."""
    directive = adapt_directive(_directive(duration_bucket=bucket))
    genome = build_genome_from_directive(directive)
    assert (genome.playlist_duration.min_seconds,
            genome.playlist_duration.max_seconds) == expected
    assert brief_playlist_duration(directive)[1] is None
    open_brief = build_genome_from_directive(adapt_directive(_directive(duration_bucket=None)))
    assert (open_brief.playlist_duration.min_seconds,
            open_brief.playlist_duration.max_seconds) == (1200, 1800)


def test_a_template_range_outside_the_policy_is_refused_at_load() -> None:
    """Proves a genre template may not declare a playlist range outside the policy; the
    library refuses to load (fail closed) instead of clamping it."""
    raw = _raw_library()
    raw["templates"]["lofi_chill"]["playlist_duration"] = {
        "min_seconds": 5400, "max_seconds": 9000, "target_seconds": 7200}
    with pytest.raises(TemplateLibraryError, match="playlist_duration_outside_policy"):
        TemplateLibrary(raw)


def test_the_policy_must_be_declared() -> None:
    """Proves a library without ``playlist_duration_policy`` (or naming an undeclared
    bucket) is refused at load; nothing defaults the playlist length."""
    raw = _raw_library()
    del raw["playlist_duration_policy"]
    with pytest.raises(TemplateLibraryError, match="playlist_duration_policy"):
        TemplateLibrary(raw)
    raw = _raw_library()
    raw["playlist_duration_policy"]["default_bucket"] = "gt_75m"
    with pytest.raises(TemplateLibraryError, match="undeclared bucket"):
        TemplateLibrary(raw)


def test_undeclared_bucket_is_made_at_the_policy_length() -> None:
    """``gt_75m`` declares no range; the arm is produced at the policy range (20-30 min,
    the ``20_30m`` bucket) with the mapping recorded, through the engine and over HTTP."""
    genome = build_genome_from_directive(adapt_directive(_directive(duration_bucket="gt_75m")))
    assert (genome.playlist_duration.min_seconds, genome.playlist_duration.max_seconds) == (
        1200, 1800)
    response = _client().post("/experiments/v1/experiments", headers=HEADERS,
                              json={"directive": _directive(duration_bucket="gt_75m")})
    assert response.status_code == 200
    assert "duration_bucket_mapped:gt_75m->policy" in response.json()["experiment"][
        "design_notes"]


def test_a_template_without_a_playlist_range_is_refused_at_load() -> None:
    """Proves a missing playlist range is refused when the template data loads; it is
    never defaulted."""
    raw = _raw_library()
    del raw["templates"]["jazz_cafe"]["playlist_duration"]
    with pytest.raises(TemplateLibraryError, match="playlist_duration"):
        TemplateLibrary(raw)


def test_a_bucket_whose_target_is_not_its_midpoint_is_refused_at_load() -> None:
    """Proves the bucket table may not declare a target other than the planning midpoint."""
    raw = _raw_library()
    raw["playlist_duration_buckets"]["45_75m"]["target_seconds"] = 3000
    with pytest.raises(TemplateLibraryError, match="midpoint"):
        TemplateLibrary(raw)


def test_package_api_exposes_the_declared_range_and_plan() -> None:
    """Proves GET /experiments/v1/packages/{id} carries the range and the supply plan."""
    client = _client()
    package = _validated_package(client)
    fetched = client.get(f"/experiments/v1/packages/{package['package_id']}", headers=HEADERS)
    assert fetched.status_code == 200
    playlist = fetched.json()["playlist_spec"]
    assert playlist["duration_range_seconds"] == {"min_seconds": 1200, "max_seconds": 1800}
    assert playlist["target_duration_seconds"] == 1500
    assert playlist["planned_track_duration_seconds"] == 190
    assert (playlist["base_track_count"], playlist["spare_track_count"]) == (7, 2)
    assert playlist["track_count_target"] == len(fetched.json()["track_specs"]) == 9
    assert playlist["spare_ratio"] == "0.25"
    assert "total_duration_minutes" not in playlist
    DurationRangeSeconds.model_validate(playlist["duration_range_seconds"])


# -- extension ---------------------------------------------------------------------------


def test_extend_is_idempotent_by_key_and_bounded() -> None:
    """Proves POST .../extend adds N distinct specs, replays the same specs for the same
    key, refuses the key with another count, and refuses past the declared bound."""
    client = _client()
    package = _validated_package(client)
    pkg_id = package["package_id"]
    url = f"/experiments/v1/packages/{pkg_id}/extend"
    bound = package["playlist_spec"]["max_extension_track_count"]
    assert bound == 5  # ceil(9 * 0.5)

    first = client.post(url, headers=HEADERS, json={"count": 3, "idempotency_key": "top-up-1"})
    assert first.status_code == 200, first.text
    added = first.json()
    ids = [spec["track_spec_id"] for spec in added["track_specs"]]
    planned = {spec["track_spec_id"] for spec in package["track_specs"]}
    assert len(set(ids)) == 3 and not set(ids) & planned
    assert all(spec["track_role"] == "secondary" for spec in added["track_specs"])
    assert all(spec["lineage"] == package["track_specs"][0]["lineage"]
               for spec in added["track_specs"])
    assert (added["extension_track_count"], added["remaining_extension_track_count"]) == (3, 2)

    replay = client.post(url, headers=HEADERS, json={"count": 3, "idempotency_key": "top-up-1"})
    assert replay.status_code == 200
    assert [s["track_spec_id"] for s in replay.json()["track_specs"]] == ids
    stored = client.get(f"/experiments/v1/packages/{pkg_id}", headers=HEADERS).json()
    assert len(stored["track_specs"]) == 9 + 3
    assert [s["track_spec_id"] for s in stored["track_specs"]][-3:] == ids
    assert [e["idempotency_key"] for e in stored["extensions"]] == ["top-up-1"]

    reused = client.post(url, headers=HEADERS, json={"count": 2, "idempotency_key": "top-up-1"})
    assert reused.status_code == 409
    assert reused.json()["detail"]["code"] == "idempotency_key_reused"

    # Asking for more than is left adds what is left (2 of 3); the replay under the same
    # key returns those two again, for the larger or the granted count.
    over = client.post(url, headers=HEADERS, json={"count": 3, "idempotency_key": "top-up-2"})
    assert over.status_code == 200
    assert len(over.json()["track_specs"]) == 2
    assert over.json()["remaining_extension_track_count"] == 0
    rest = client.post(url, headers=HEADERS, json={"count": 2, "idempotency_key": "top-up-2"})
    assert rest.status_code == 200
    assert rest.json()["track_specs"] == over.json()["track_specs"]
    assert client.post(url, headers=HEADERS, json={"count": 1, "idempotency_key": "top-up-3"}
                       ).json()["detail"]["code"] == "package_extension_limit_exceeded"


def test_extension_specs_are_deterministic_across_databases() -> None:
    """Proves the same package and key produce the same spec ids in a fresh database."""
    runs = []
    for _ in range(2):
        client = _client()
        package = _validated_package(client)
        response = client.post(f"/experiments/v1/packages/{package['package_id']}/extend",
                               headers=HEADERS, json={"count": 2, "idempotency_key": "k"})
        runs.append([s["track_spec_id"] for s in response.json()["track_specs"]])
    assert runs[0] == runs[1]


# -- Idempotency-Key header --------------------------------------------------------------


def test_idempotency_key_replays_the_stored_response() -> None:
    """Proves a retried design/validate/materialize with the same key and body returns the
    first answer instead of failing on its own duplicate insert (the R5 gap)."""
    client = _client()
    body = {"directive": SAMPLE_DIRECTIVE, "seed": 11}
    keyed = {**HEADERS, "Idempotency-Key": "design-1"}
    first = client.post("/experiments/v1/experiments", headers=keyed, json=body)
    again = client.post("/experiments/v1/experiments", headers=keyed, json=body)
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    exp = first.json()["experiment"]

    validate_keyed = {**HEADERS, "Idempotency-Key": "validate-1"}
    url = f"/experiments/v1/experiments/{exp['experiment_id']}/validate"
    assert client.post(url, headers=validate_keyed).json() == \
        client.post(url, headers=validate_keyed).json()

    mat_url = f"/experiments/v1/experiments/{exp['experiment_id']}/materialize"
    mat_body = {"treatment_id": exp["treatments"][0]["treatment_id"]}
    mat_keyed = {**HEADERS, "Idempotency-Key": "materialize-1"}
    mat_first = client.post(mat_url, headers=mat_keyed, json=mat_body)
    mat_again = client.post(mat_url, headers=mat_keyed, json=mat_body)
    assert mat_first.status_code == mat_again.status_code == 200
    assert mat_first.json() == mat_again.json()


def test_same_key_with_a_different_request_is_409() -> None:
    """Proves a key names exactly one request: another body or path under it is refused
    with ``idempotency_key_reused`` and changes nothing."""
    client = _client()
    keyed = {**HEADERS, "Idempotency-Key": "shared-key"}
    first = client.post("/experiments/v1/experiments", headers=keyed,
                        json={"directive": SAMPLE_DIRECTIVE, "seed": 1})
    assert first.status_code == 200
    other_body = client.post("/experiments/v1/experiments", headers=keyed,
                             json={"directive": SAMPLE_DIRECTIVE, "seed": 2})
    assert other_body.status_code == 409
    assert other_body.json()["detail"]["code"] == "idempotency_key_reused"
    exp_id = first.json()["experiment"]["experiment_id"]
    other_path = client.post(f"/experiments/v1/experiments/{exp_id}/validate", headers=keyed)
    assert other_path.status_code == 409
    assert client.get(f"/experiments/v1/experiments/{exp_id}",
                      headers=HEADERS).json()["status"] == "draft"
    listed = client.get("/experiments/v1/experiments", headers=HEADERS).json()
    assert listed["total"] == 1

    blank = client.post("/experiments/v1/experiments", headers={**HEADERS, "Idempotency-Key": " "},
                        json={"directive": SAMPLE_DIRECTIVE, "seed": 3})
    assert blank.status_code == 422
    assert blank.json()["detail"]["code"] == "idempotency_key_invalid"


def test_consecutive_videos_of_a_genre_never_share_a_visual_world() -> None:
    """Growth spec 2026-11-03 (variation): P3 reads the genre's recent visual worlds from
    its own store, so two videos of the same sub-style in a row get different worlds."""
    client = _client()
    worlds = []
    for n in range(3):
        directive = _directive(use_case=["study_focus"], vocal_mode="instrumental",
                               subgenre="future_garage_focus", genre="focus_electronic")
        directive["directive_id"] = f"dir_visual_{n}"
        directive["brief"].pop("visual_world", None)  # P3 chooses the world
        created = client.post("/experiments/v1/experiments", headers=HEADERS,
                              json={"directive": directive, "seed": 7})
        assert created.status_code == 200, created.text
        experiment = created.json()["experiment"]
        exp_id = experiment["experiment_id"]
        assert client.post(f"/experiments/v1/experiments/{exp_id}/validate",
                           headers=HEADERS).status_code == 200
        package = client.post(
            f"/experiments/v1/experiments/{exp_id}/materialize", headers=HEADERS,
            json={"treatment_id": experiment["treatments"][0]["treatment_id"]}).json()["package"]
        worlds.append(package["visual_spec"]["visual_world"])
    assert worlds[0] == "observatory_workspace"
    assert len(set(worlds)) == 3, worlds
