"""W0.6: content addressing, key grammar and the opaque artifact-URI pair.

Hermetic like the other unit tests: settings are injected and the dotenv path is
an absent file. Nothing here talks to MinIO — that is the integration suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from plm_platform.config import Settings, load_settings
from plm_platform.storage import (
    ANALYTICS_PREFIX,
    EXPERIMENTS_PREFIX,
    MARKET_PREFIX,
    MEDIA_PREFIX,
    MUSIC_PREFIX,
    PREFIXES,
    QA_PREFIX,
    REPORTS_PREFIX,
    InvalidArtifactUriError,
    InvalidObjectKeyError,
    InvalidResourcePathError,
    UnknownPrefixError,
    build_key,
    digest_from_key,
    from_uri,
    sha256_digest,
    to_uri,
)

ABSENT_DOTENV = Path(__file__).resolve().parent / "no-such.env"

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
RESOURCE = "brands/neon_seoul/track-1/master.wav"
MEDIA_KEY = f"media/{RESOURCE}/{ABC_SHA256}"


def settings(**overrides: str) -> Settings:
    env = {
        "DATABASE_URL": "postgresql://plm:secret@db.internal:5432/plm",
        "POSTGRES_PASSWORD": "secret",
        "MINIO_ENDPOINT": "objects.internal:9000",
        "MINIO_ROOT_USER": "plm-root",
        "MINIO_ROOT_PASSWORD": "minio-secret",
        "MINIO_BUCKET": "plm-media",
        "TEMPORAL_HOST": "temporal.internal:7233",
    }
    env.update(overrides)
    return load_settings(env=env, dotenv_path=ABSENT_DOTENV)


class TestNamespace:
    def test_seven_fixed_top_level_prefixes(self) -> None:
        assert PREFIXES == (
            MARKET_PREFIX,
            EXPERIMENTS_PREFIX,
            MUSIC_PREFIX,
            QA_PREFIX,
            MEDIA_PREFIX,
            REPORTS_PREFIX,
            ANALYTICS_PREFIX,
        )
        assert PREFIXES == (
            "market/",
            "experiments/",
            "music/",
            "qa/",
            "media/",
            "reports/",
            "analytics/",
        )


class TestDigest:
    def test_sha256_vectors(self) -> None:
        assert sha256_digest(b"") == EMPTY_SHA256
        assert sha256_digest(b"abc") == ABC_SHA256


class TestBuildKey:
    def test_key_is_prefix_path_digest(self) -> None:
        assert build_key(MEDIA_PREFIX, RESOURCE, ABC_SHA256) == MEDIA_KEY

    @pytest.mark.parametrize("prefix", ["not-a-prefix/", "media", "MEDIA/", "", "public/"])
    def test_unknown_prefix_refused(self, prefix: str) -> None:
        with pytest.raises(UnknownPrefixError, match="unknown storage prefix"):
            build_key(prefix, RESOURCE, ABC_SHA256)

    @pytest.mark.parametrize(
        "resource",
        ["", "/absolute", "a//b", "a/./b", "a/../b", "trailing/", "back\\slash"],
    )
    def test_dirty_resource_paths_refused(self, resource: str) -> None:
        with pytest.raises(InvalidResourcePathError, match="clean relative resource path"):
            build_key(MEDIA_PREFIX, resource, ABC_SHA256)

    @pytest.mark.parametrize("digest", ["abc", ABC_SHA256.upper(), ABC_SHA256 + "0", ""])
    def test_malformed_digest_refused(self, digest: str) -> None:
        with pytest.raises(InvalidObjectKeyError, match="sha256"):
            build_key(MEDIA_PREFIX, RESOURCE, digest)


class TestDigestFromKey:
    def test_pinned_digest_round_trips(self) -> None:
        assert digest_from_key(MEDIA_KEY) == ABC_SHA256

    def test_key_outside_the_namespace_refused(self) -> None:
        with pytest.raises(UnknownPrefixError, match="outside the platform namespace"):
            digest_from_key(f"elsewhere/thing/{ABC_SHA256}")

    def test_key_without_a_digest_segment_refused(self) -> None:
        with pytest.raises(InvalidObjectKeyError, match="does not end in a sha256 digest"):
            digest_from_key("media/brands/neon_seoul/track-1/master.wav")


class TestArtifactUri:
    def test_to_uri_builds_scheme_bucket_key(self) -> None:
        assert to_uri(MEDIA_KEY, settings=settings()) == f"plm://plm-media/{MEDIA_KEY}"

    def test_uri_round_trips_through_from_uri(self) -> None:
        uri = to_uri(MEDIA_KEY, settings=settings())
        assert from_uri(uri, settings=settings()) == MEDIA_KEY

    def test_foreign_scheme_refused(self) -> None:
        with pytest.raises(InvalidArtifactUriError, match="not a plm artifact URI"):
            from_uri(f"s3://plm-media/{MEDIA_KEY}", settings=settings())

    def test_uri_for_another_bucket_refused(self) -> None:
        other = to_uri(MEDIA_KEY, settings=settings(MINIO_BUCKET="other-bucket"))
        with pytest.raises(InvalidArtifactUriError, match="not a plm artifact URI"):
            from_uri(other, settings=settings())

    def test_empty_key_refused(self) -> None:
        with pytest.raises(InvalidArtifactUriError, match="not a plm artifact URI"):
            from_uri("plm://plm-media/", settings=settings())

    def test_from_uri_does_not_interpret_the_key(self) -> None:
        """Opaque by contract: only scheme and bucket are checked, nothing else."""
        assert from_uri("plm://plm-media/free/form/path", settings=settings()) == "free/form/path"
