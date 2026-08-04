"""Configured service access for transport-level tool functions."""

from __future__ import annotations

from ..services import ServiceContainer


_services: ServiceContainer | None = None


def configure(services: ServiceContainer) -> None:
    global _services
    _services = services


def get_services() -> ServiceContainer:
    if _services is None:
        raise RuntimeError("MCP tool services have not been configured.")
    return _services
