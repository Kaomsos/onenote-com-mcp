"""MCP transport adapters and registration for the OneNote application services."""

from __future__ import annotations

from typing import Any

from ..services import ServiceContainer
from .advanced import TOOLS as ADVANCED_TOOLS
from .context import configure
from .hierarchy import TOOLS as HIERARCHY_TOOLS
from .mutations import TOOLS as MUTATION_TOOLS
from .operations import TOOLS as OPERATION_TOOLS
from .pages import TOOLS as PAGE_TOOLS
from .system import TOOLS as SYSTEM_TOOLS


DEFAULT_TOOLS = [
    *SYSTEM_TOOLS,
    *HIERARCHY_TOOLS,
    *PAGE_TOOLS,
    *MUTATION_TOOLS,
    *OPERATION_TOOLS,
]


def register_tools(mcp: Any, services: ServiceContainer, *, raw_xml_enabled: bool = False) -> None:
    """Bind the service container and register the selected MCP tool profile."""

    configure(services)
    functions = [*DEFAULT_TOOLS, *(ADVANCED_TOOLS if raw_xml_enabled else [])]
    for function in functions:
        mcp.tool()(function)


__all__ = ["ADVANCED_TOOLS", "DEFAULT_TOOLS", "register_tools"]
