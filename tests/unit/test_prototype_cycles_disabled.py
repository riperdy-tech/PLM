"""The prototype operator cycles refuse to run before any provider or P2 call.

Both scripts report the old hardcoded upload `lPYmaPHPQr4` as new work and would
post a fabricated reward into P2; neither generates music or renders a video.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("prototype cycle reached the network")

    monkeypatch.setattr(httpx.Client, "send", refuse)


def test_loop_worker_cycle_refuses(no_network: None) -> None:
    from scripts.autonomous_loop_worker import run_cycle

    with pytest.raises(RuntimeError, match="prototype disabled"):
        run_cycle(auto_approve=True)


def test_control_deck_cycle_refuses_before_any_step(no_network: None) -> None:
    pytest.importorskip("tkinter")
    pytest.importorskip("PIL.ImageTk")
    from scripts.plm_desktop_control import PlmControlDeckApp

    deck = MagicMock()
    with pytest.raises(RuntimeError, match="Control Deck cycle disabled"):
        PlmControlDeckApp._run_cycle_steps(deck)
    deck._update_progress.assert_not_called()
