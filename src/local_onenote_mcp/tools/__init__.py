"""MCP transport adapters and registration for the OneNote application services."""

from __future__ import annotations

from typing import Any

from ..operation_catalog import build_operation_registry
from ..services import ServiceContainer
from ..services.operation_runtime import OperationRuntime
from ..tool_surface import USER_TOOL_NAMES
from .copying import TOOLS as COPY_TOOLS
from .context import configure
from .hierarchy import TOOLS as HIERARCHY_TOOLS
from .mutations import TOOLS as MUTATION_TOOLS
from .operations import TOOLS as OPERATION_TOOLS
from .pages import TOOLS as PAGE_TOOLS
from .system import TOOLS as SYSTEM_TOOLS


_TOOL_BY_NAME = {
    function.__name__: function
    for function in (
        *SYSTEM_TOOLS,
        *HIERARCHY_TOOLS,
        *PAGE_TOOLS,
        *MUTATION_TOOLS,
        *COPY_TOOLS,
        *OPERATION_TOOLS,
    )
}
if set(_TOOL_BY_NAME) != set(USER_TOOL_NAMES):
    raise RuntimeError(
        "Public tool adapters do not match the frozen user surface: "
        f"missing={sorted(set(USER_TOOL_NAMES) - set(_TOOL_BY_NAME))}, "
        f"unexpected={sorted(set(_TOOL_BY_NAME) - set(USER_TOOL_NAMES))}."
    )
DEFAULT_TOOLS = [_TOOL_BY_NAME[name] for name in USER_TOOL_NAMES]


def register_tools(mcp: Any, services: ServiceContainer) -> None:
    """Bind the service container and register the production MCP tool surface."""

    registry = build_operation_registry(services)
    registry.audit_public_tools(
        tuple(function.__name__ for function in DEFAULT_TOOLS), profile="default"
    )
    configure(OperationRuntime(registry, services.coordinator))
    for function in DEFAULT_TOOLS:
        mcp.tool()(function)


__all__ = ["DEFAULT_TOOLS", "register_tools"]
