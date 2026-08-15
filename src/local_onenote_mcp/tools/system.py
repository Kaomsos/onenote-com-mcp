"""System diagnostics and identifier-resolution MCP tools."""

from __future__ import annotations

from typing import Any

from .responses import invoke


async def health_check() -> dict[str, Any]:
    """Verify local OneNote COM access and return a small hierarchy summary."""
    return invoke("health_check")


async def resolve_identifier(identifier: str, item_type: str = "") -> dict[str, Any]:
    """Resolve a OneNote identifier to one live typed object for read-only interaction."""

    return invoke("resolve_identifier", identifier=identifier, item_type=item_type)


async def get_special_locations() -> dict[str, Any]:
    """Return OneNote's local special folders."""

    return invoke("get_special_locations")


TOOLS = [health_check, resolve_identifier, get_special_locations]
