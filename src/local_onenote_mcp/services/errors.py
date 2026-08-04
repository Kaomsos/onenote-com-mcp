"""Application service errors with structured transport details."""

from __future__ import annotations

from typing import Any


class PartialFailure(RuntimeError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details
