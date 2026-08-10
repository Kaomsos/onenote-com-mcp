"""Infrastructure base for the four independently registered Copy scenarios."""

from __future__ import annotations

import argparse
from dataclasses import replace
from typing import Any, Awaitable, Callable

from ..mcp_stdio_client import COPY_NO_DELETE_POLICY, MCPStdioClient
from ..runtime import RuntimeOptions
from .base import Scenario
from .common.config import COPY_CLEANUP_TOOLS
from .common.specs import ScenarioSpec


CopyExecutor = Callable[..., Awaitable[dict[str, Any]]]


class CopyScenario(Scenario):
    timeout_default = 1_800
    included_in_all = True
    worksite_dry_run_action = "preserve-active-copy-targets"
    execute_copy: CopyExecutor

    def runtime_spec(self, args: argparse.Namespace) -> ScenarioSpec:
        spec = self.spec
        if not getattr(args, "keep_worksite", False):
            return spec
        return replace(
            spec,
            policy=COPY_NO_DELETE_POLICY,
            tool_allowlist=frozenset(
                set(spec.tool_allowlist) - COPY_CLEANUP_TOOLS - {"close_notebook"}
            ),
        )

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
