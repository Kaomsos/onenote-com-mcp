"""HUMAN-GATED validation of production OneNote COM convergence semantics."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import MCPStdioClient
from ..runtime import InvariantFailure, RunnerFailure, RuntimeOptions
from ..test_utils import (
    assert_restored,
    capture_snapshot,
    find_snapshot_item,
    resolve_manifest_item,
    scenario_dir,
    validate_manifest_notebook,
    write_json,
)
from .base import Scenario
from .common.copy_runtime import call_with_result_evidence
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.onenote_convergence import RECIPE


def _require_convergence(result: dict[str, Any], operation: str) -> dict[str, Any]:
    timing = result.get("convergence")
    if not isinstance(timing, dict) or timing.get("converged") is not True:
        raise InvariantFailure(f"{operation} omitted converged production timing evidence.")
    if int(timing.get("stable_observations", 0)) < 2:
        raise InvariantFailure(f"{operation} did not prove two stable observations.")
    if int(timing.get("attempts", 0)) < 2:
        raise InvariantFailure(f"{operation} timing attempts are incomplete.")
    return {
        "attempts": timing["attempts"],
        "elapsed_seconds": timing.get("elapsed_seconds"),
        "stable_observations": timing["stable_observations"],
        "identity_remap": dict(timing.get("identity_remap", {})),
        "transient_errors": list(timing.get("transient_errors", [])),
    }


@SCENARIO_REGISTRY.register
class OneNoteConvergenceScenario(Scenario):
    name = "onenote-convergence"
    fixture_recipe = RECIPE
    included_in_all = False
    timeout_default = 300
    help_text = (
        "HUMAN-GATED: validate production Create, Page update, Reorder, Delete, "
        "and lifecycle Close convergence on one fresh disposable Notebook."
    )
    worksite_dry_run_action = "preserve-convergence-probe-page"

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        if client is None:
            raise RunnerFailure("OneNote convergence scenario requires its scenario MCP client.")
        if options.use_cache:
            raise RunnerFailure("OneNote convergence scenario is fresh-only and forbids --use-cache.")
        notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
        section = resolve_manifest_item(manifest, "convergence_section")
        first = resolve_manifest_item(manifest, "first_anchor_page")
        out = scenario_dir(options.run_dir, self.name)
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)

        created = await call_with_result_evidence(
            client,
            "create_page",
            {
                "section_id": section["id"],
                "title": "03-Convergence-Probe",
                "content": "Initial convergence probe body",
                "content_format": "plain",
            },
            out / "create-result.json",
        )
        created_id = str(created.get("page_id", ""))
        if not created_id or created_id != str(created.get("allocated_id", "")):
            raise InvariantFailure("Create did not preserve its exact fresh allocated Page ID.")
        evidence = {"create": _require_convergence(created, "create_page")}

        created_item = created.get("page")
        if not isinstance(created_item, dict):
            raise InvariantFailure("Create response omitted the typed Page read-back.")
        appended = await call_with_result_evidence(
            client,
            "append_to_page",
            {
                "page_id": created_id,
                "content": "Second convergence marker",
                "expected_title": created_item["title"],
                "expected_section_id": section["id"],
                "expected_modified": created_item.get("modified"),
                "content_format": "plain",
            },
            out / "append-result.json",
        )
        evidence["page_update"] = _require_convergence(appended, "append_to_page")

        appended_item = appended.get("item")
        if not isinstance(appended_item, dict):
            raise InvariantFailure("Append response omitted the stable Page identity.")
        reordered = await call_with_result_evidence(
            client,
            "reorder_page",
            {
                "page_id": created_id,
                "expected_title": appended_item["title"],
                "expected_section_id": section["id"],
                "after_page_id": first["id"],
                "page_level": 1,
                "expected_modified": appended_item.get("modified"),
            },
            out / "reorder-result.json",
        )
        evidence["reorder"] = _require_convergence(reordered, "reorder_page")
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        observed = find_snapshot_item(after, created_id)
        if observed is None or int(observed.get("order", -1)) != int(first["order"]) + 1:
            raise InvariantFailure("Reorder snapshot did not place the probe after the exact anchor ID.")

        keep_worksite = bool(getattr(args, "keep_worksite", False))
        if keep_worksite:
            worksite = {
                "status": "convergence_probe_preserved",
                "target_ids": [created_id],
                "notebook_id": notebook_id,
                "manual_cleanup_required": True,
                "cleanup": "Delete the exact probe Page non-permanently, then close the disposable Notebook.",
            }
            write_json(out / "worksite.json", worksite)
            result = {
                "scenario": self.name,
                "status": "passed",
                "fixture": fixture_result,
                "convergence": evidence,
                "restored": False,
                "worksite_preserved": True,
                "remaining_state": worksite,
            }
            write_json(out / "result.json", result)
            return result

        deleted = await call_with_result_evidence(
            client,
            "delete_page",
            {
                "page_id": created_id,
                "expected_title": observed["title"],
                "expected_section_id": section["id"],
                "expected_modified": observed.get("modified"),
                "permanently": False,
            },
            out / "delete-result.json",
        )
        evidence["delete"] = _require_convergence(deleted, "delete_page")
        restored = await capture_snapshot(client, notebook_id)
        write_json(out / "restored.json", restored)
        assert_restored(before, restored)
        result = {
            "scenario": self.name,
            "status": "passed",
            "fixture": fixture_result,
            "convergence": evidence,
            "close_evidence": "recorded by the shared lifecycle wrapper after scenario execution",
            "restored": True,
            "worksite_preserved": False,
        }
        write_json(out / "result.json", result)
        return result


__all__ = ["OneNoteConvergenceScenario"]
