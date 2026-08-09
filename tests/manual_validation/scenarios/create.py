"""Fixture-only create scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import MCPStdioClient
from ..runtime import RuntimeOptions
from ..test_utils import scenario_dir, write_json
from .base import Scenario
from .common.registry import SCENARIO_REGISTRY


@SCENARIO_REGISTRY.register
class CreateScenario(Scenario):
    name = "create"
    help_text = "GATED: create the preset isolated Notebook fixture, report, then close or keep."
    registered_for_all = True

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        result = {
            "scenario": self.name,
            "status": "passed",
            "fixture": fixture_result,
            "worksite_preserved": bool(getattr(args, "keep_worksite", False)),
        }
        if getattr(args, "keep_worksite", False):
            notebook = manifest["notebook"]
            worksite = {
                "status": "created_fixture_preserved",
                "target_ids": [notebook["id"]],
                "notebook_id": notebook["id"],
                "notebook_name": notebook["name"],
                "manual_cleanup_required": True,
                "cleanup": "Close the disposable source Notebook after inspection.",
            }
            write_json(
                scenario_dir(options.run_dir, self.name) / "worksite.json",
                worksite,
            )
            result["remaining_state"] = worksite
        write_json(
            scenario_dir(options.run_dir, self.name) / "result.json",
            result,
        )
        return result


__all__ = ["CreateScenario"]
