"""Human-gated same-parent SectionGroup reorder and restore scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import MCPStdioClient, REORDER_SECTION_GROUP_POLICY
from ..runtime import RuntimeOptions
from .base import Scenario
from .common.config import REORDER_SECTION_GROUP_TOOLS
from .common.container_reorder import execute_container_reorder
from .common.registry import SCENARIO_REGISTRY


@SCENARIO_REGISTRY.register
class ReorderSectionGroupScenario(Scenario):
    name = "reorder-section-group"
    help_text = (
        "CAPABILITY-LIMITED / VALIDATION FAILED: retained SectionGroup reorder "
        "diagnostic; backend keeps fixed ascending name order."
    )
    registered_for_all = False
    capability_assessment = {
        "capability_status": "limited",
        "validation_status": "failed",
        "reason": (
            "The backend keeps SectionGroups in fixed ascending name order and "
            "did not apply the requested sibling order after UpdateHierarchy returned success."
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
        return await execute_container_reorder(
            args=args,
            options=options,
            manifest=manifest,
            scenario_name=self.name,
            resource_type="section_group",
            tool_name="reorder_section_group",
            id_parameter="section_group_id",
            after_parameter="after_section_group_id",
            plans=(
                ("root_group_c", "root_group_a"),
                ("nested_group_c", "nested_group_a"),
            ),
            policy=REORDER_SECTION_GROUP_POLICY,
            allowed_tools=REORDER_SECTION_GROUP_TOOLS,
            client=client,
        )
