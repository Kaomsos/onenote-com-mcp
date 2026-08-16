"""Session diagnostics and explicit OneNote GUI launch MCP tools."""

from __future__ import annotations

from typing import Any

from .responses import invoke


async def health_check() -> dict[str, Any]:
    """At session start, check the existing visible OneNote GUI and runtime capabilities; never launch it. GUI readiness is required before every authorized effect."""
    return invoke("health_check")


async def launch_onenote_gui() -> dict[str, Any]:
    """When health_check is not ready, use UI Control to launch trusted OneNote once, then call health_check again before retrying an authorized effect."""

    return invoke("launch_onenote_gui")


TOOLS = [health_check, launch_onenote_gui]
