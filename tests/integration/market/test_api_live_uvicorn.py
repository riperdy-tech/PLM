# tests/integration/market/test_api_live_uvicorn.py
"""The app served by uvicorn over a real socket — not only through ASGITransport.

No service token and no database are configured: the accepted behaviour of a fresh
checkout is a 503 `blocked_pending_credentials` with a correlation id on the way back,
served over an actual TCP socket.
"""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
import uvicorn

from services.market_intelligence.api.app import create_app
from services.market_intelligence.config import load_market_settings

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_a_real_uvicorn_server_answers_with_the_blocked_reason() -> None:
    """No token configured ⇒ 503 blocked, over a real socket, with a correlation id back."""
    port = _free_port()
    app = create_app(session_factory=None, settings=load_market_settings())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    response = None
    try:
        while time.monotonic() < deadline and response is None:
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/market/v1/opportunities",
                                     timeout=1.0)
            except httpx.TransportError:
                time.sleep(0.1)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert response is not None and response.status_code == 503
    assert response.json()["detail"]["code"] == "blocked_pending_credentials"
    assert "x-correlation-id" in {key.lower() for key in response.headers}
