"""Application service errors with structured transport details."""

from __future__ import annotations

from typing import Any


class PartialFailure(RuntimeError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class MutationFailure(RuntimeError):
    """Content-free controlled mutation failure with a stable response code."""

    def __init__(self, message: str, *, code: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


class MutationPreflightFailure(ValueError):
    """Validation-compatible failure before a mutation execute was attempted."""

    code = "validation_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details
