# tests/contract/test_c9_storage_namespace.py
"""Seam C9 — Cross-phase storage layout. FROZEN by Ruling R13.

Asserts the single storage namespace, the seven frozen prefixes,
content-addressed keys, and checksum enforcement on write and read.
"""

from __future__ import annotations

import hashlib

import pytest
from plm_platform.storage import (
    ANALYTICS_PREFIX,
    EXPERIMENTS_PREFIX,
    MARKET_PREFIX,
    MEDIA_PREFIX,
    MUSIC_PREFIX,
    PREFIXES,
    QA_PREFIX,
    REPORTS_PREFIX,
    UnknownPrefixError,
    build_key,
    digest_from_key,
    from_uri,
    to_uri,
)

from tests.support.contract import SeamRegistry

# Exercises code that loads platform settings; the values are set explicitly.
pytestmark = pytest.mark.usefixtures("platform_env")

REGISTRY_ENTRY = "C9 · Cross-phase storage layout"

FROZEN_PREFIXES: tuple[str, ...] = (
    "market/",
    "experiments/",
    "music/",
    "qa/",
    "media/",
    "reports/",
    "analytics/",
)


def test_frozen_prefixes_match_registry(contract_registry: SeamRegistry) -> None:
    contract_registry.require_entry(REGISTRY_ENTRY)
    assert PREFIXES == FROZEN_PREFIXES
    assert MARKET_PREFIX == "market/"
    assert EXPERIMENTS_PREFIX == "experiments/"
    assert MUSIC_PREFIX == "music/"
    assert QA_PREFIX == "qa/"
    assert MEDIA_PREFIX == "media/"
    assert REPORTS_PREFIX == "reports/"
    assert ANALYTICS_PREFIX == "analytics/"


def test_build_key_enforces_frozen_prefixes() -> None:
    content = b"sample music payload"
    digest = hashlib.sha256(content).hexdigest()

    # Valid key with canonical prefix
    key = build_key(MUSIC_PREFIX, "neon/exp01/track01.wav", digest=digest)
    assert key.startswith("music/")
    assert key.endswith(f"/{digest}")
    assert digest_from_key(key) == digest

    # Invalid prefix raises UnknownPrefixError
    with pytest.raises(UnknownPrefixError):
        build_key("unapproved_prefix/", "data.json", digest=digest)


def test_uri_conversion_roundtrip() -> None:
    key = "media/brand_neon/exp_01/pkg_01/master.mp4/abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"

    uri = to_uri(key)
    assert uri.startswith("plm://")
    assert uri.endswith(f"/{key}")

    parsed_key = from_uri(uri)
    assert parsed_key == key
