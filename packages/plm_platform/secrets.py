"""A string wrapper whose value never leaks through repr/str/formatting/logging (W0.4).

The config loader wraps every credential in :class:`Secret`. Rendered output is always
the redaction marker; the real value is available only through the explicit
:meth:`Secret.get_secret_value`. Accidental string interpolation therefore prints
``**********`` instead of the value, and string concatenation raises instead of building
a leaked string.
"""

from __future__ import annotations

import hmac
from typing import Final, final

__all__ = ["Secret"]

_REDACTED: Final[str] = "**********"


@final
class Secret:
    """Opaque holder for a secret string; redacted whenever it is rendered.

    ``__repr__``, ``__str__`` and ``__format__`` all produce the fixed marker
    ``**********``, so f-strings, ``%``-formatting and logging handlers are born safe.
    :meth:`get_secret_value` is the single, greppable access point for the real value.
    Equality compares values in constant time and only against other ``Secret``s;
    hashing is deliberately constant and never derived from the value.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError(f"Secret expects a str, got {type(value).__name__}")
        self._value: str = value

    def get_secret_value(self) -> str:
        """Explicitly unwrap the secret; every call site is a deliberate risk point."""
        return self._value

    def __repr__(self) -> str:
        return f"Secret('{_REDACTED}')"

    def __str__(self) -> str:
        return _REDACTED

    def __format__(self, format_spec: str) -> str:
        return format(_REDACTED, format_spec)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    def __hash__(self) -> int:
        # Constant on purpose: equal secrets must hash equally, and a value-derived hash
        # would let a hash dump reveal information about the secret.
        return hash(_REDACTED)
