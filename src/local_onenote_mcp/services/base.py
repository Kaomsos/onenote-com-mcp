"""Shared infrastructure helpers for application services."""

from __future__ import annotations

from typing import Any

from ..bridge import OneNoteBridge


class BaseService:
    def __init__(self, bridge: OneNoteBridge) -> None:
        self.bridge = bridge

    def call(self, operation: str, **params: Any) -> dict[str, Any]:
        return self.bridge.call(operation, **params)

    @staticmethod
    def enum(name: str, value: str, options: dict[str, int]) -> int:
        key = value.casefold()
        if key not in options:
            allowed = ", ".join(sorted(options))
            raise ValueError(f"{name} must be one of: {allowed}")
        return options[key]
