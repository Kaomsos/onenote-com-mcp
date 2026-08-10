"""Human-gated same-Notebook typed SectionGroup reparent scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import MCPStdioClient
from ..runtime import RuntimeOptions
from .base import Scenario
from .common.config import REPARENT_SECTION_GROUP_TOOLS
from .common.registry import SCENARIO_REGISTRY
from .common.reparent import execute_typed_reparent


@SCENARIO_REGISTRY.register
class ReparentSectionGroupScenario(Scenario):
    name = "reparent-section-group"
    help_text = (
        "EXPERIMENTAL: validate typed Notebook→SectionGroup, SectionGroup→Notebook, and "
        "SectionGroup→SectionGroup ID-preserving reparent, then restore or preserve."
    )
    registered_for_all = False
    capability_assessment = {
        "capability_status": "experimental",
        "validation_status": "passed",
        "reason": (
            "A user-run migrated typed scenario confirmed all three same-Notebook "
            "SectionGroup transitions while preserving identity, topology, content, and restoration."
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
        return await execute_typed_reparent(
            args=args,
            options=options,
            manifest=manifest,
            scenario_name=self.name,
            resource_type="section_group",
            target_key=None,
            source_parent_key=None,
            destination_parent_key=None,
            allowed_tools=REPARENT_SECTION_GROUP_TOOLS,
            client=client,
            plans=(
                (
                    "notebook-to-section-group",
                    "notebook_to_group_target",
                    None,
                    "notebook_to_group_destination",
                ),
                (
                    "section-group-to-notebook",
                    "group_to_notebook_target",
                    "group_to_notebook_source",
                    None,
                ),
                (
                    "section-group-to-section-group",
                    "group_to_group_target",
                    "group_to_group_source",
                    "group_to_group_destination",
                ),
            ),
        )
