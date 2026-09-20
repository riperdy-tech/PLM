# tests/unit/market/test_api_auth.py
"""P1.10: the service-token auth is fail-closed and never leaks the token."""

from plm_platform.config import load_settings

from services.market_intelligence.api.app import create_app
from services.market_intelligence.config import load_market_settings
from tests.support.c1_api import SyncASGIClient

BASE_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
}

TOKEN_HEADER = "X-PLM-Service-Token"


def _client(platform_settings) -> SyncASGIClient:
    app = create_app(session_factory=None,
                     settings=load_market_settings(platform=platform_settings))
    return SyncASGIClient(app, base_url="http://testserver", headers={})


def test_without_a_configured_token_every_read_is_blocked_not_open() -> None:
    platform = load_settings(env=BASE_ENV)
    with _client(platform) as client:
        response = client.get("/market/v1/opportunities")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "blocked_pending_credentials"


def test_a_wrong_or_missing_token_is_401_and_the_right_one_passes() -> None:
    platform = load_settings(env={**BASE_ENV, "PLM_SERVICE_TOKEN": "shared-secret"})
    with _client(platform) as client:
        assert client.get("/market/v1/opportunities").status_code == 401
        assert client.get("/market/v1/opportunities",
                          headers={TOKEN_HEADER: "wrong"}).status_code == 401
        # the right token reaches the session dependency, which has no factory here
        assert client.get("/market/v1/opportunities",
                          headers={TOKEN_HEADER: "shared-secret"}).status_code == 503


def test_the_token_never_appears_in_an_error_body_or_the_redacted_settings() -> None:
    platform = load_settings(env={**BASE_ENV, "PLM_SERVICE_TOKEN": "shared-secret-sentinel"})
    with _client(platform) as client:
        body = client.get("/market/v1/opportunities",
                          headers={TOKEN_HEADER: "wrong"}).text
    assert "shared-secret-sentinel" not in body
    assert "shared-secret-sentinel" not in repr(platform)
    assert "**********" in repr(platform)
