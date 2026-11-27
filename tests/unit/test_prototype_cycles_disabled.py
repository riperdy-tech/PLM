"""The prototype operator cycles refuse to run before any provider or P2 call.

These scripts report the old hardcoded upload `lPYmaPHPQr4` as new work and would
post a fabricated reward into P2; none generates music or renders a video.
"""
from __future__ import annotations

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


def test_p9_closed_loop_runner_refuses(no_network: None) -> None:
    from scripts.run_phase_p9_closed_loop import main

    with pytest.raises(RuntimeError, match="prototype disabled"):
        main()


def test_direct_test_video_upload_refuses(no_network: None) -> None:
    from scripts.publish_test_video import main

    with pytest.raises(RuntimeError, match="prototype disabled"):
        main()

