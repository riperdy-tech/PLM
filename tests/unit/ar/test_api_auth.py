# tests/unit/ar/test_api_auth.py
"""P2.13: the AR service token is fail-closed and never echoed (P1.10's semantics)."""

from fastapi.testclient import TestClient
from plm_platform.config import load_settings
from sqlalchemy.orm import sessionmaker

from services.autonomous_ar.api.app import create_app
from services.autonomous_ar.config import load_ar_settings
from tests.support.ar_db import create_ar_engine

ENV_WITH_TOKEN = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": "ar-seam-token",
}


def _app(*, with_token: bool) -> object:
    engine = create_ar_engine()
    factory = sessionmaker(bind=engine)
    env = dict(ENV_WITH_TOKEN)
    if not with_token:
        env.pop("PLM_SERVICE_TOKEN")
    settings = load_ar_settings(platform=load_settings(env=env))
    return create_app(session_factory=factory, settings=settings)


def test_unconfigured_token_closes_the_api_and_a_wrong_token_is_rejected() -> None:
    closed = TestClient(_app(with_token=False))
    blocked = closed.get("/ar/directives", params={"brand_id": "neon_seoul"})
    assert blocked.status_code == 503
    assert blocked.json()["detail"]["code"] == "blocked_pending_credentials"
    assert blocked.json()["detail"]["field_paths"] == ["PLM_SERVICE_TOKEN"]

    configured = TestClient(_app(with_token=True))
    wrong = configured.get("/ar/directives", params={"brand_id": "neon_seoul"},
                           headers={"X-PLM-Service-Token": "nope"})
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "invalid_service_token"
    # fail closed: no arm, cycle or directive data may appear in the refusal
    assert "ar-seam-token" not in wrong.text


def test_the_configured_token_is_never_rendered_by_the_settings_repr() -> None:
    settings = load_ar_settings(platform=load_settings(env=ENV_WITH_TOKEN))
    assert "ar-seam-token" not in repr(settings)
    assert "**********" in repr(settings.platform)
