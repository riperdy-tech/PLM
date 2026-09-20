# tests/contract/conftest.py
"""Seam-test fixtures: a fixture-backed P1 API with its token, no docker, no credentials."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.support.c1_api import SyncASGIClient, open_c1_client


@pytest.fixture
def c1_api_client() -> Iterator[SyncASGIClient]:
    with open_c1_client() as client:
        yield client
