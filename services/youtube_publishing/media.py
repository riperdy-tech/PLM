"""The stored bytes of a C6 package's video and thumbnail, for a real upload.

A real YouTube upload sends the package's own bytes, fetched by their pinned digests
(ff73bce); a mock client uses its placeholders and needs none.
"""

from __future__ import annotations

from plm_platform.storage import digest_from_key, from_uri, get_verified

__all__ = ["artifact_bytes"]


def artifact_bytes(uri: str, checksum: str) -> bytes:
    """The stored bytes of a C6 asset, refused unless they hash to its checksum."""
    key = from_uri(uri)
    if digest_from_key(key) != checksum:
        raise ValueError(f"{uri} does not pin checksum {checksum}")
    return get_verified(key)
