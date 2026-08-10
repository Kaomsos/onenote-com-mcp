"""Human-gated same-Notebook Page reparent capability probe."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import MCPStdioClient
from ..runtime import RuntimeOptions
from .base import Scenario
from .common.config import REPARENT_PAGE_TOOLS
from .common.registry import SCENARIO_REGISTRY
from .common.reparent import execute_reparent_probe


@SCENARIO_REGISTRY.register
class ReparentPageScenario(Scenario):
    name = "reparent-page"
    help_text = (
        "EXPERIMENTAL: probe numbered same-Notebook Page reparent with a Description "
        "Page, then restore or preserve."
    )
    registered_for_all = False
    capability_assessment = {
        "capability_status": "experimental",
        "validation_status": "passed",
        "reason": (
            "A user-run probe confirmed same-Notebook Page reparent with the expected ID "
            "remap while preserving RichText, Table, List, Tag, and Image content."
        ),
    }

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        return await execute_reparent_probe(
            args=args,
            options=options,
            manifest=manifest,
            scenario_name=self.name,
            resource_type="page",
            target_key="reparent_page",
            source_parent_key="source_section",
            destination_parent_key="destination_section",
            allowed_tools=REPARENT_PAGE_TOOLS,
            client=client,
        )
