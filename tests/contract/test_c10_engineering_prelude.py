# tests/contract/test_c10_engineering_prelude.py
"""Seam C10 — Missing engineering prelude (PHASE-P0). FROZEN by Ruling R16.

Asserts the Wave 0 platform surfaces that all nine domain phases assume
import and self-describe cleanly (P0 §5 C10, Ruling R16).
"""

from __future__ import annotations

from plm_contracts import ErrorShape, Tri
from plm_platform.cli import app as cli_app
from plm_platform.storage import PREFIXES

from tests.support.contract import SeamRegistry

REGISTRY_ENTRY = "C10 · Missing engineering prelude (PHASE-P0)"


def test_engineering_prelude_matches_registry(contract_registry: SeamRegistry) -> None:
    contract_registry.require_entry(REGISTRY_ENTRY)


def test_shared_contracts_surface_is_sound() -> None:
    # Error shape: code, message, field_paths (Review Focus 1, P0 W0.3)
    err = ErrorShape(code="invalid_request", message="test failure", field_paths=["body.name"])
    assert err.code == "invalid_request"
    assert err.message == "test failure"
    assert err.field_paths == ["body.name"]

    # Review Focus 1: Tri-state distinguishes known, zero, unknown, and not_ready
    known_val = Tri.known(42.0)
    zero_val = Tri.known(0.0)
    unknown_val = Tri.unknown()
    not_ready_val = Tri.not_ready()

    assert known_val.is_known and known_val.unwrap() == 42.0
    assert zero_val.is_known and zero_val.unwrap() == 0.0
    assert not unknown_val.is_known
    assert not not_ready_val.is_known
    assert unknown_val != not_ready_val
    assert zero_val != unknown_val


def test_platform_surface_is_sound() -> None:
    # Storage namespace has 7 prefixes (R13)
    assert len(PREFIXES) == 7

    # CLI Typer app has registered command groups and root commands
    cmd_names = {
        cmd.name or (cmd.callback.__name__ if cmd.callback else "")
        for cmd in cli_app.registered_commands
    }
    group_names = {group.name for group in cli_app.registered_groups}
    all_commands = cmd_names | group_names

    for phase_group in (
        "market",
        "ar",
        "experiments",
        "music",
        "qa",
        "media",
        "youtube",
        "distribution",
        "analytics",
        "doctor",
        "migrate",
    ):
        assert phase_group in all_commands, f"missing CLI group/command for {phase_group}"
