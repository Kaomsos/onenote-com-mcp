"""Infrastructure base for the four independently registered Copy scenarios."""

from __future__ import annotations

import argparse
from typing import Any, Awaitable, Callable

from ..mcp_stdio_client import MCPStdioClient
from ..runtime import RuntimeOptions
from .base import Scenario


CopyExecutor = Callable[..., Awaitable[dict[str, Any]]]


class CopyScenario(Scenario):
    timeout_default = 1_800
    registered_for_all = True
    execute_copy: CopyExecutor

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.execute_copy(args, options, manifest, client=client)


__all__ = ["CopyScenario"]
