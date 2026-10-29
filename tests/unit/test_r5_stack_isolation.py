"""Gate R5's destructive-test isolation check, without network or DB writes."""
from __future__ import annotations

import pytest
from plm_platform.temporal import Phase
from sqlalchemy.engine import make_url

from tests.integration.test_r5_full_chain import _r5_isolation_refusal


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        ("postgresql://fixture:fixture@127.0.0.1:5432/plm", "r5_isolated_postgres_required"),
        ("postgresql://fixture:fixture@remote.test:55432/plm_r5_probe",
         "r5_isolated_postgres_required"),
        ("sqlite:///:memory:", "r5_isolated_postgres_required"),
        # The shared disposable-target rule: an explicit non-default port is required.
        ("postgresql://fixture:fixture@localhost/plm_r5_probe", "r5_isolated_postgres_required"),
        ("postgresql://fixture:fixture@localhost:5432/plm_r5_probe",
         "r5_isolated_postgres_required"),
        ("postgresql://fixture:fixture@[::1]:55432/plm_r5_probe", "r5_isolated_postgres_required"),
        ("postgresql://fixture:fixture@localhost:55432/plm_r5_probe", None),
        ("postgresql://fixture:fixture@127.0.0.1:55432/plm_r5_ci", None),
    ],
)
def test_r5_guard_requires_isolated_loopback_postgres(
    dsn: str, expected: str | None,
) -> None:
    assert _r5_isolation_refusal(
        opted_in=True, db=make_url(dsn),
        endpoints={Phase.MARKET: "http://127.0.0.1:8001"},
    ) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://remote.test:8001",
        "https://example.com:443",
        "file:///tmp/a",
        "http://localhost.evil.test:80",
        "http://0.0.0.0:8001",
        "not-a-url",
    ],
)
def test_r5_guard_denies_nonloopback_and_malformed_service_endpoints(
    endpoint: str,
) -> None:
    assert _r5_isolation_refusal(
        opted_in=True, db=make_url("postgresql://fixture:fixture@localhost:55432/plm_r5_probe"),
        endpoints={Phase.MARKET: endpoint},
    ) == "r5_isolated_phase_endpoints_required"


def test_r5_guard_denies_empty_endpoint_set_and_unrequested_stack() -> None:
    db = make_url("postgresql://fixture:fixture@localhost:55432/plm_r5_probe")
    assert _r5_isolation_refusal(
        opted_in=True, db=db, endpoints={},
    ) == "r5_isolated_phase_endpoints_required"
    assert _r5_isolation_refusal(
        opted_in=False, db=db, endpoints={Phase.MARKET: "http://127.0.0.1:8001"},
    ) == "r5_isolated_stack_opt_in_required"
