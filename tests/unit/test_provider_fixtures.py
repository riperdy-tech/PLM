"""Every R6 provider fixture states where its shape came from."""
from __future__ import annotations

import pytest

from tests.support.provider_fixtures import all_fixtures, load


def test_fixtures_exist() -> None:
    assert all_fixtures()


@pytest.mark.parametrize(("provider", "name"), all_fixtures())
def test_fixture_has_provenance(provider: str, name: str) -> None:
    load(provider, name)  # raises on a missing or unknown provenance
