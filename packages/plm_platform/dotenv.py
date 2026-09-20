"""Minimal ``.env`` reader for the W0.4 layered loader.

``python-dotenv`` is not a runtime dependency of this repo, and the accepted format is
deliberately the small, documented subset that ``.env.example`` uses:

* blank lines and full-line ``#`` comments;
* an optional ``export `` prefix;
* ``KEY=VALUE`` where ``KEY`` matches ``[A-Za-z_][A-Za-z0-9_]*``;
* single-quoted values (literal), double-quoted values (``\\n``, ``\\t``, ``\\r``,
  ``\\\\``, ``\\"`` and ``\\'`` escapes; unknown escapes keep their backslash), and bare
  values — a bare value ends at `` #`` (a ``#`` preceded by whitespace), so passwords
  containing ``#`` survive unquoted;
* later assignments win over earlier ones.

Malformed input raises :class:`EnvFileError`, naming the line number and — when it can
be recovered safely — the key, never the value: a ``.env`` file holds secrets.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["EnvFileError", "load_env_file", "parse_env_file"]

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ESCAPES: dict[str, str] = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "\\": "\\",
    '"': '"',
    "'": "'",
}


class EnvFileError(ValueError):
    """A ``.env`` file exists but cannot be parsed."""


def load_env_file(path: str | Path) -> dict[str, str]:
    """Parse ``path``; a missing file yields ``{}`` — the file is never required."""
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    return parse_env_file(text, origin=str(file_path))


def parse_env_file(text: str, origin: str = "<env file>") -> dict[str, str]:
    """Parse dotenv ``text`` into a mapping; ``origin`` only appears in error messages."""
    values: dict[str, str] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export") and len(line) > 6 and line[6].isspace():
            line = line[7:].lstrip()
        key, separator, rest = line.partition("=")
        if not separator:
            raise EnvFileError(f"{origin} line {lineno}: expected a KEY=VALUE assignment")
        key = key.strip()
        if not _KEY_RE.match(key):
            raise EnvFileError(f"{origin} line {lineno}: invalid variable name {key!r}")
        values[key] = _parse_value(rest.strip(), origin, lineno)
    return values


def _parse_value(raw: str, origin: str, lineno: int) -> str:
    if raw.startswith("#"):
        return ""
    if raw.startswith("'"):
        closing = raw.find("'", 1)
        if closing == -1:
            raise EnvFileError(f"{origin} line {lineno}: unterminated single-quoted value")
        return _require_quoted_tail(raw[1:closing], raw[closing + 1 :], origin, lineno)
    if raw.startswith('"'):
        chars: list[str] = []
        index = 1
        while index < len(raw):
            char = raw[index]
            if char == '"':
                return _require_quoted_tail(
                    "".join(chars), raw[index + 1 :], origin, lineno
                )
            if char == "\\" and index + 1 < len(raw):
                chars.append(_ESCAPES.get(raw[index + 1], "\\" + raw[index + 1]))
                index += 2
                continue
            chars.append(char)
            index += 1
        raise EnvFileError(f"{origin} line {lineno}: unterminated double-quoted value")
    for index, char in enumerate(raw):
        if char == "#" and index > 0 and raw[index - 1].isspace():
            return raw[:index].rstrip()
    return raw


def _require_quoted_tail(value: str, tail: str, origin: str, lineno: int) -> str:
    tail = tail.strip()
    if tail and not tail.startswith("#"):
        raise EnvFileError(
            f"{origin} line {lineno}: unexpected characters after a quoted value"
        )
    return value
