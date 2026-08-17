"""Human-gated Section reorder and restore scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import MCPStdioClient, REORDER_SECTION_POLICY
from ..runtime import RuntimeOptions
from .base import Scenario
from .common.config import REORDER_SECTION_TOOLS
from .common.container_reorder import execute_container_reorder
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.reorder_section import RECIPE


@SCENARIO_REGISTRY.register
class ReorderSectionScenario(Scenario):
    name = "reorder-section"
    fixture_recipe = RECIPE
    included_in_all = True
    worksite_dry_run_action = "preserve-reordered-sections"
    help_text = "GATED: reorder and sort direct Sections under Notebook and SectionGroup parents, then restore."

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        return await execute_container_reorder(
            args=args,
            options=options,
            manifest=manifest,
            scenario_name=self.name,
            resource_type="section",
            tool_name="reorder_section",
            id_parameter="section_id",
            after_parameter="after_section_id",
            sort_tool_name="sort_children",
            plans=(
                ("notebook-parent", "root_section_c", "root_section_a"),
                ("section-group-parent", "group_section_c", "group_section_a"),
            ),
            policy=REORDER_SECTION_POLICY,
            allowed_tools=REORDER_SECTION_TOOLS,
            client=client,
        )
