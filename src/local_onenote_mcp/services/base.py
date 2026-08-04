"""Shared infrastructure helpers for application services."""

from __future__ import annotations

from typing import Any

from ..bridge import OneNoteBridge, OneNoteBridgeError


class BaseService:
    def __init__(self, bridge: OneNoteBridge) -> None:
        self.bridge = bridge

    def call(self, operation: str, **params: Any) -> dict[str, Any]:
        try:
            return self.bridge.call(operation, **params)
        except OneNoteBridgeError as exc:
            raise RuntimeError(str(exc)) from exc

    @staticmethod
    def enum(name: str, value: str, options: dict[str, int]) -> int:
        key = value.casefold()
        if key not in options:
            allowed = ", ".join(sorted(options))
            raise ValueError(f"{name} must be one of: {allowed}")
        return options[key]
