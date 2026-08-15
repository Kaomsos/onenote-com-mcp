"""Session diagnostics and explicit OneNote GUI launch MCP tools."""

from __future__ import annotations

from typing import Any

from .responses import invoke


async def health_check() -> dict[str, Any]:
    """Check an existing visible OneNote GUI and report content-free runtime capabilities; never launch it."""
    return invoke("health_check")


async def launch_onenote_gui() -> dict[str, Any]:
    """With UI Control, launch trusted OneNote Desktop at most once when absent, then observe GUI readiness."""

    return invoke("launch_onenote_gui")


TOOLS = [health_check, launch_onenote_gui]
