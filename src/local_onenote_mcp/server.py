"""Composition root for the local Microsoft OneNote MCP server."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .bridge import OneNoteBridge
from .debug_trace import create_tracer_from_env
from .services import ServiceContainer
from .settings import DEFAULT_TIMEOUT, MAX_TEXT_CHARS, MCP_NAME
from .tools import register_tools

mcp = FastMCP(MCP_NAME)
bridge = OneNoteBridge(timeout_seconds=DEFAULT_TIMEOUT)
services = ServiceContainer.build(bridge, max_text_chars=MAX_TEXT_CHARS)
_debug_tracer, _trace_status = create_tracer_from_env()
register_tools(mcp, services, tracer=_debug_tracer, trace_status=_trace_status)


def main() -> None:
    """Run the stdio MCP transport."""

    try:
        mcp.run()
    finally:
        if _debug_tracer is not None:
            _debug_tracer.close()


if __name__ == "__main__":
    main()
