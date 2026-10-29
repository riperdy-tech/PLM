"""Unit tests for Music Factory API endpoints (§4.5, §67, §68, R6, R15)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from plm_contracts.seams.c3 import TrackSpecPayload

from services.music_factory.api.app import create_app
from services.music_factory.domain.enums import AttemptStatus
from services.music_factory.domain.models import GenerationRequest, GenerationResponse
from services.music_factory.providers.adapters import MockAdapter
from services.music_factory.storage.artifacts import MinioArtifactStorage
from services.music_factory.storage.models import (
    creative_variants_table,
    generated_candidates_table,
    generation_attempts_table,
    generation_plans_table,
    rights_snapshots_table,
)
from tests.support.fake_minio import FakeMinio
from tests.support.music_db import create_music_engine, music_session
from tests.support.seams import lineage_fields, track_spec_fields

SAMPLE_C3_SPEC = TrackSpecPayload.build(
    **track_spec_fields(
        track_spec_id='trk_api_01',
        playlist_spec_id='ply_01',
        brand_profile_id='neon_seoul',
        hard_constraints={'genre': 'rnb', 'language': 'english', 'vocal_mode': 'vocal'},
        creative_latitude={},
        lyric_spec={},
        vocal_spec={'vocal_mode': 'vocal'},
        instrumentation_spec={},
        production_style='lofi_contemporary',
        requested_duration=90,
        candidate_target=1,
        priority=1,
        budget_limit=50.0,
        lineage=lineage_fields(experiment_id='exp_api_01', content_arm_id='arm_01'),
        bounded_preferences={'bpm': 80,
         'key_profile': None,
         'mood': ['melancholic'],
         'use_case': ['late_night_drive']},
        provider_policy={'allowed_providers': ['mock']},
    )
).to_wire()


from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings

from services.music_factory.config import load_music_factory_settings

ENV_WITH_TOKEN = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": "music-seam-token",
}


def _make_client(
    *, with_token: bool = True, engine: sa.Engine | None = None,
) -> tuple[TestClient, str]:
    env = dict(ENV_WITH_TOKEN)
    if not with_token:
        env.pop("PLM_SERVICE_TOKEN")
    platform = load_settings(env=env)
    settings = load_music_factory_settings(platform=platform)
    db_engine = engine if engine is not None else create_music_engine(static_pool=True)

    def session_factory():
        return music_session(db_engine)

    # The production MinIO store over an in-process stand-in for the MinIO client (R4).
    storage = MinioArtifactStorage(platform, client=FakeMinio())  # type: ignore[arg-type]
    app = create_app(session_factory=session_factory, settings=settings, storage=storage)
    client = TestClient(app)
    return client, "music-seam-token"


def test_health_check_public() -> None:
    client, _ = _make_client(with_token=False)
    res = client.get("/music/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


def test_auth_fail_closed_without_token() -> None:
    client, token = _make_client(with_token=True)

    # Missing header -> 401
    res = client.get("/music/providers")
    assert res.status_code == 401

    # Invalid token -> 401
    res = client.get("/music/providers", headers={SERVICE_TOKEN_HEADER: "wrong-token"})
    assert res.status_code == 401

    # Valid token -> 200
    res = client.get("/music/providers", headers={SERVICE_TOKEN_HEADER: token})
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_plan_and_execution_lifecycle_and_r6_endpoints() -> None:
    client, token = _make_client(with_token=True)
    headers = {SERVICE_TOKEN_HEADER: token}

    # 1. Create plan
    res_plan = client.post("/music/plans", json=SAMPLE_C3_SPEC, headers=headers)
    assert res_plan.status_code == 200
    plan_data = res_plan.json()
    plan_id = plan_data["plan_id"]
    assert plan_data["status"] == "planned"

    # 2. Execute plan
    res_exec = client.post(f"/music/plans/{plan_id}/execute", json=SAMPLE_C3_SPEC, headers=headers)
    assert res_exec.status_code == 200
    exec_data = res_exec.json()
    assert exec_data["status"] == "completed"
    assert len(exec_data["candidates"]) >= 1

    candidate_id = exec_data["candidates"][0]["candidate_id"]

    # 3. Ruling R6 read endpoints:
    # GET /music/candidates/{id}/attempts
    res_att = client.get(f"/music/candidates/{candidate_id}/attempts", headers=headers)
    assert res_att.status_code == 200
    assert len(res_att.json()) >= 1

    # GET /music/candidates/{id}/license
    res_lic = client.get(f"/music/candidates/{candidate_id}/license", headers=headers)
    assert res_lic.status_code == 200
    lic_data = res_lic.json()
    assert lic_data["commercial_use_status"] == "eligible"
    assert lic_data["plan_or_tier"] == "internal"  # declared by the mock provider (ADR-0027)
    assert lic_data["account_identity_ref"] == "plm-mock"
    assert lic_data["terms_version"] == "mock-v1"

    # GET /music/candidates/{id}/artifacts
    res_art = client.get(f"/music/candidates/{candidate_id}/artifacts", headers=headers)
    assert res_art.status_code == 200
    assert len(res_art.json()) >= 1

    # GET /music/candidates/{id}/provenance
    res_prov = client.get(f"/music/candidates/{candidate_id}/provenance", headers=headers)
    assert res_prov.status_code == 200
    prov_data = res_prov.json()
    assert prov_data["candidate_id"] == candidate_id
    assert prov_data["track_spec_id"] == "trk_api_01"

    # 4. Export to P5 emitting Seam C4 event
    res_export = client.post(f"/music/candidates/{candidate_id}/export-to-p5", headers=headers)
    assert res_export.status_code == 200
    export_data = res_export.json()
    assert export_data["candidate"]["status"] == "exported_to_p5"
    assert export_data["event"]["event_type"] == "music_candidate_ready_for_p5"
    assert export_data["event"]["schema_version"] == "3.0"  # ADR-0035
    assert export_data["event"]["lineage"]["content_arm_id"] == "arm_01"  # not a placeholder
    # The mock honours the requested duration (ADR-0032); it no longer clamps to 30 s.
    assert export_data["event"]["measured_audio"]["duration_seconds"] == 90.0
    # R4: the artifact is named by its opaque plm:// URI, whose key ends in the sha256.
    event = export_data["event"]
    assert event["artifact_uri"].startswith("plm://plm-media/music/")
    assert event["artifact_uri"].endswith("/" + event["artifact_sha256"])

    # Re-export is idempotent: the same event, rebuilt from the same stored facts.
    again = client.post(f"/music/candidates/{candidate_id}/export-to-p5", headers=headers)
    assert again.status_code == 200
    assert again.json()["event"] == event
    assert again.json()["candidate"]["status"] == "exported_to_p5"


def test_candidate_license_uses_own_attempt_and_refuses_missing_provenance() -> None:
    """A plan's first license must never be reused for its second candidate."""
    engine = create_music_engine(static_pool=True)
    client, token = _make_client(engine=engine)
    headers = {SERVICE_TOKEN_HEADER: token}
    payload = {**SAMPLE_C3_SPEC, "candidate_target": 2}

    created = client.post("/music/plans", json=payload, headers=headers)
    assert created.status_code == 200
    plan_id = created.json()["plan_id"]
    executed = client.post(
        f"/music/plans/{plan_id}/execute", json=payload, headers=headers,
    )
    assert executed.status_code == 200
    candidates = executed.json()["candidates"]
    assert len(candidates) == 2
    first, second = candidates
    assert first["source_attempt_id"] != second["source_attempt_id"]

    for candidate in candidates:
        license_response = client.get(
            f"/music/candidates/{candidate['candidate_id']}/license", headers=headers,
        )
        assert license_response.status_code == 200
        assert license_response.json()["attempt_id"] == candidate["source_attempt_id"]

    second_url = f"/music/candidates/{second['candidate_id']}/license"
    # A legacy missing link cannot borrow the first attempt's rights.
    with engine.begin() as connection:
        connection.execute(
            sa.update(generated_candidates_table)
            .where(generated_candidates_table.c.id == second["candidate_id"])
            .values(source_attempt_id=None)
        )
    missing_link = client.get(second_url, headers=headers)
    assert missing_link.status_code == 404

    # A dangling source ID also must not borrow a license from the same plan.
    with engine.begin() as connection:
        connection.execute(
            sa.update(generated_candidates_table)
            .where(generated_candidates_table.c.id == second["candidate_id"])
            .values(source_attempt_id="missing_attempt")
        )
    assert client.get(second_url, headers=headers).status_code == 404

    # A producing attempt without a rights record stays unverified.
    with engine.begin() as connection:
        connection.execute(
            sa.update(generated_candidates_table)
            .where(generated_candidates_table.c.id == second["candidate_id"])
            .values(source_attempt_id=second["source_attempt_id"])
        )
        connection.execute(
            sa.delete(rights_snapshots_table)
            .where(rights_snapshots_table.c.attempt_id == second["source_attempt_id"])
        )
    assert client.get(second_url, headers=headers).status_code == 404
    first_license = client.get(
        f"/music/candidates/{first['candidate_id']}/license", headers=headers,
    )
    assert first_license.status_code == 200
    assert first_license.json()["attempt_id"] == first["source_attempt_id"]


def test_api_partial_candidate_supply_is_not_reported_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_generate = MockAdapter.generate
    calls = 0

    def one_success(self: MockAdapter, request: GenerationRequest) -> GenerationResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_generate(self, request)
        return GenerationResponse(
            provider_job_id="", status=AttemptStatus.FAILED,
            error_message="simulated_candidate_unavailable",
        )

    monkeypatch.setattr(MockAdapter, "generate", one_success)
    client, token = _make_client()
    headers = {SERVICE_TOKEN_HEADER: token}
    spec = {
        **SAMPLE_C3_SPEC,
        "candidate_target": 2,
        "requested_duration": 8,
        "duration_range": {"min_seconds": 7, "max_seconds": 12},
    }
    created = client.post("/music/plans", json=spec, headers=headers)
    assert created.status_code == 200
    plan_id = created.json()["plan_id"]
    run = client.post(f"/music/plans/{plan_id}/execute", json=spec, headers=headers)
    assert run.status_code == 200
    assert run.json()["status"] == "failed"
    assert len(run.json()["candidates"]) == 1
    assert "candidate_target_not_met: 1/2" in run.json()["errors"]
    assert client.get(f"/music/plans/{plan_id}", headers=headers).json()["plan"][
        "status"
    ] == "failed"
    again = client.post(f"/music/plans/{plan_id}/execute", json=spec, headers=headers)
    assert again.status_code == 200
    assert again.json()["status"] == "failed"
    assert again.json()["candidates"] == run.json()["candidates"]
    assert calls == 3


def test_plan_api_scales_candidate_variants_and_refuses_capacity_overflow() -> None:
    client, token = _make_client()
    headers = {SERVICE_TOKEN_HEADER: token}
    four = client.post(
        "/music/plans", json={**SAMPLE_C3_SPEC, "candidate_target": 4},
        headers=headers,
    )
    assert four.status_code == 200
    assert four.json()["candidate_target"] == 4
    assert len(four.json()["variants"]) == 4

    too_many = client.post(
        "/music/plans", json={**SAMPLE_C3_SPEC, "candidate_target": 11},
        headers=headers,
    )
    assert too_many.status_code == 422
    assert too_many.json()["detail"]["code"] == "generation_plan_capacity_exceeded"
    assert too_many.json()["detail"]["field_paths"] == ["candidate_target"]


def test_plan_post_without_key_reuses_only_matching_immutable_brief() -> None:
    """Same deterministic ID must never overwrite an existing C3 plan."""
    engine = create_music_engine(static_pool=True)
    client, token = _make_client(engine=engine)
    headers = {SERVICE_TOKEN_HEADER: token}

    first = client.post("/music/plans", json=SAMPLE_C3_SPEC, headers=headers)
    assert first.status_code == 200
    initial = first.json()
    repeated = client.post("/music/plans", json=SAMPLE_C3_SPEC, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json() == initial

    for replacement in (
        {**SAMPLE_C3_SPEC, "budget_limit": 0},
        {**SAMPLE_C3_SPEC, "candidate_target": 2},
        {
            **SAMPLE_C3_SPEC,
            "bounded_preferences": {
                **SAMPLE_C3_SPEC["bounded_preferences"], "bpm": 91,
            },
        },
    ):
        conflicting = client.post("/music/plans", json=replacement, headers=headers)
        assert conflicting.status_code == 409
        assert conflicting.json()["detail"]["code"] == "generation_plan_identity_conflict"

    plan_url = f"/music/plans/{initial['plan_id']}"
    assert client.get(plan_url, headers=headers).json()["plan"]["status"] == "planned"
    executed = client.post(
        f"/music/plans/{initial['plan_id']}/execute",
        json=SAMPLE_C3_SPEC, headers=headers,
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "completed"

    after_completion = client.post("/music/plans", json=SAMPLE_C3_SPEC, headers=headers)
    assert after_completion.status_code == 200
    assert after_completion.json()["status"] == "completed"
    assert after_completion.json()["variants"] == initial["variants"]
    assert client.get(plan_url, headers=headers).json()["plan"]["status"] == "completed"

    # A corrupted persisted treatment cannot be silently returned as though
    # it matched the C3 brief, even if the source payload is identical.
    with engine.begin() as connection:
        connection.execute(
            sa.update(creative_variants_table)
            .where(creative_variants_table.c.id == initial["variants"][0]["variant_id"])
            .values(prompt_delta_json='{"extra_instruction":"altered"}')
        )
    corrupted = client.post("/music/plans", json=SAMPLE_C3_SPEC, headers=headers)
    assert corrupted.status_code == 409
    assert corrupted.json()["detail"]["code"] == "generation_plan_identity_conflict"
    assert client.get(plan_url, headers=headers).json()["plan"]["status"] == "completed"


def test_execute_refuses_changed_c3_brief_before_generating() -> None:
    """An immutable plan cannot pay for audio generated under a replacement brief."""
    engine = create_music_engine(static_pool=True)
    client, token = _make_client(engine=engine)
    headers = {SERVICE_TOKEN_HEADER: token}
    created = client.post("/music/plans", json=SAMPLE_C3_SPEC, headers=headers)
    assert created.status_code == 200
    plan_id = created.json()["plan_id"]
    changed = {
        **SAMPLE_C3_SPEC,
        "bounded_preferences": {
            **SAMPLE_C3_SPEC["bounded_preferences"], "bpm": 88,
        },
    }
    refused = client.post(
        f"/music/plans/{plan_id}/execute", json=changed, headers=headers,
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "generation_plan_spec_mismatch"
    assert client.get(f"/music/plans/{plan_id}", headers=headers).json()["plan"]["status"] == (
        "planned"
    )

    # Only the original immutable C3 payload can execute and export.
    valid = client.post(
        f"/music/plans/{plan_id}/execute", json=SAMPLE_C3_SPEC, headers=headers,
    )
    assert valid.status_code == 200
    assert valid.json()["status"] == "completed"


def test_c4_export_refuses_cross_plan_and_forged_source_evidence() -> None:
    """Neither C4 nor license reads can trust a mismatched persisted join."""
    engine = create_music_engine(static_pool=True)
    client, token = _make_client(engine=engine)
    headers = {SERVICE_TOKEN_HEADER: token}
    created = client.post("/music/plans", json=SAMPLE_C3_SPEC, headers=headers)
    assert created.status_code == 200
    plan_id = created.json()["plan_id"]
    executed = client.post(
        f"/music/plans/{plan_id}/execute", json=SAMPLE_C3_SPEC, headers=headers,
    )
    assert executed.status_code == 200
    candidate = executed.json()["candidates"][0]
    candidate_id = candidate["candidate_id"]
    attempt_id = candidate["source_attempt_id"]
    export_url = f"/music/candidates/{candidate_id}/export-to-p5"
    license_url = f"/music/candidates/{candidate_id}/license"

    for not_ready_status in ("planned", "running", "failed", "cancelled"):
        with engine.begin() as connection:
            connection.execute(
                sa.update(generation_plans_table)
                .where(generation_plans_table.c.id == plan_id)
                .values(status=not_ready_status)
            )
        refused = client.post(export_url, headers=headers)
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "generation_plan_not_completed"
        assert refused.json()["detail"]["field_paths"] == [
            "generation_plan_id", "status",
        ]
        assert client.get(f"/music/candidates/{candidate_id}", headers=headers).json()[
            "candidate"
        ]["status"] == "generated"

    with engine.begin() as connection:
        connection.execute(
            sa.update(generation_plans_table)
            .where(generation_plans_table.c.id == plan_id)
            .values(status="completed")
        )

    # These are separate corruption scenarios. Restore each source record so the
    # final happy path proves a refusal did not mutate or export the candidate.
    scenarios = (
        (generation_attempts_table, "id", attempt_id,
         "generation_plan_id", "unrelated_plan", plan_id, "attempt_plan_mismatch"),
        (generation_attempts_table, "id", attempt_id,
         "status", "failed", "succeeded", "attempt_not_succeeded"),
        (generation_attempts_table, "id", attempt_id,
         "model_id", "different_model", candidate["model_id"],
         "candidate_attempt_provider_mismatch"),
        (rights_snapshots_table, "attempt_id", attempt_id,
         "provider_id", "unrelated_provider", candidate["provider_id"],
         "rights_provider_mismatch"),
        (generated_candidates_table, "id", candidate_id,
         "track_spec_id", "wrong_track", candidate["track_spec_id"],
         "candidate_plan_identity_mismatch"),
    )
    for table, key_field, key_value, field, incorrect, original, reason in scenarios:
        with engine.begin() as connection:
            connection.execute(
                sa.update(table).where(table.c[key_field] == key_value).values({field: incorrect})
            )
        refused = client.post(export_url, headers=headers)
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == reason
        assert client.get(license_url, headers=headers).status_code == 404
        with engine.begin() as connection:
            connection.execute(
                sa.update(table).where(table.c[key_field] == key_value).values({field: original})
            )

    exported = client.post(export_url, headers=headers)
    assert exported.status_code == 200
    assert exported.json()["event"]["rights"]["attempt_id"] == attempt_id
    assert client.get(license_url, headers=headers).status_code == 200
