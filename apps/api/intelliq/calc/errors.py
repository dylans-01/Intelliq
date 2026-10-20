from __future__ import annotations

from .ast import Span


class CalcError(Exception):
    """A user-facing calculation error with an optional source position."""

    def __init__(self, message: str, span: Span | None = None, code: str = "invalid_formula"):
        super().__init__(message)
        self.message = message
        self.span = span
        self.code = code

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "start": self.span.start if self.span else None,
            "end": self.span.end if self.span else None,
        }
