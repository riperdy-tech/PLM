# tests/unit/ar/test_smoke_blocked.py
"""P2.16: the smoke's blocked paths are explicit outcomes, never a silent pass.

The success path needs a live P1 and a service token (an operator command, §114);
what a hermetic suite can pin is that every missing precondition returns the named
`blocked: …` reason and that the CLI turns it into `BLOCKED_EXIT_CODE`.
"""

from plm_platform.cli import BLOCKED_EXIT_CODE, app
from typer.testing import CliRunner

from services.autonomous_ar.acceptance.production_smoke import run_production_smoke

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
}


def test_without_a_service_token_the_smoke_is_blocked_with_that_exact_reason(monkeypatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    reason = run_production_smoke()
    assert reason == "blocked: pending P1 service token (PLM_SERVICE_TOKEN)"


def test_with_a_token_but_a_dead_p1_the_reason_names_the_p1_failure(monkeypatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PLM_SERVICE_TOKEN", "t")
    reason = run_production_smoke(base_url="http://127.0.0.1:9")
    assert reason is not None and reason.startswith("blocked: P1 unreachable")


def test_cli_exits_three_with_the_reason_on_stderr(monkeypatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    result = CliRunner().invoke(app, ["ar", "smoke"])
    assert result.exit_code == BLOCKED_EXIT_CODE
    assert "blocked: pending P1 service token" in result.output
