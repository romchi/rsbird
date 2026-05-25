"""Exception hierarchy for rsbird."""
from __future__ import annotations


class RsBirdError(Exception):
    """Base class for all rsbird errors."""


class BirdError(RsBirdError):
    """BIRD returned an error reply (4-digit code 8xxx or 9xxx)."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"BIRD error {code}: {message}")
        self.code = code
        self.message = message


class BirdTimeout(RsBirdError):
    """A control-socket operation exceeded its timeout."""


class ParseError(RsBirdError):
    """The parser could not make sense of a BIRD reply."""

    def __init__(self, message: str, *, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw
