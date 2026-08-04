"""Composition root for the local Microsoft OneNote MCP server."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .bridge import OneNoteBridge
from .policy import env_bool
from .services import ServiceContainer
from .settings import DEFAULT_TIMEOUT, MAX_TEXT_CHARS, MCP_NAME
from .tools import register_tools

mcp = FastMCP(MCP_NAME)
bridge = OneNoteBridge(timeout_seconds=DEFAULT_TIMEOUT)
services = ServiceContainer.build(bridge, max_text_chars=MAX_TEXT_CHARS)
register_tools(mcp, services, raw_xml_enabled=env_bool("LOCAL_ONENOTE_ENABLE_RAW_XML"))


def main() -> None:
    """Run the stdio MCP transport."""

    mcp.run()


if __name__ == "__main__":
    main()
