"""HUMAN-GATED validation of production OneNote COM convergence semantics."""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from ..lifecycle import NotebookLifecycleWrapper
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


def _require_attempt_contract(result: dict[str, Any], operation: str) -> dict[str, Any]:
    reconciliation = result.get("reconciliation")
    if not isinstance(reconciliation, dict):
        raise InvariantFailure(f"{operation} omitted mutation attempt evidence.")
    expected = {
        "state": "applied",
        "mutation_stage": "postcondition",
        "preflight_state": "logical_ready",
        "mutation_attempted": True,
        "mutation_attempts": 1,
        "mutation_replayed": False,
        "observed_outcome": "applied",
        "retry_safety": "not_needed",
        "recommended_action": "none",
    }
    mismatched = {
        key: {"expected": expected_value, "actual": reconciliation.get(key)}
        for key, expected_value in expected.items()
        if reconciliation.get(key) != expected_value
    }
    if mismatched:
        raise InvariantFailure(
            f"{operation} violated the mutation attempt contract: {mismatched}"
        )
    return {key: reconciliation[key] for key in expected}


@SCENARIO_REGISTRY.register
class OneNoteConvergenceScenario(Scenario):
    name = "onenote-convergence"
    fixture_recipe = RECIPE
    included_in_all = False
    timeout_default = 300
    requires_lifecycle_wrappers = True
    production_close_handoff = True
    help_text = (
        "HUMAN-GATED: validate production Create, Title, Append, content Delete, "
        "Reorder, hierarchy Delete, and Close on one fresh disposable Notebook."
    )
    worksite_dry_run_action = "preserve-convergence-probe-page"

    async def execute_with_lifecycle(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
        wrappers: Mapping[str, NotebookLifecycleWrapper],
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
        renamed_title = "03-Convergence-Probe-Renamed"
        titled = await call_with_result_evidence(
            client,
            "update_page_title",
            {
                "page_id": created_id,
                "title": renamed_title,
                "expected_title": created_item["title"],
                "expected_section_id": section["id"],
                "expected_modified": created_item.get("modified"),
            },
            out / "title-result.json",
        )
        evidence["title"] = {
            "convergence": _require_convergence(titled, "update_page_title"),
            "attempt": _require_attempt_contract(titled, "update_page_title"),
        }
        titled_item = titled.get("item")
        if not isinstance(titled_item, dict) or titled_item.get("title") != renamed_title:
            raise InvariantFailure("Title update omitted the exact renamed Page read-back.")
        objects_before_append = await client.call_tool(
            "get_page_objects", {"page_id": created_id}
        )
        before_object_ids = {
            str(item["id"])
            for item in objects_before_append.get("objects", [])
            if item.get("id")
        }
        appended = await call_with_result_evidence(
            client,
            "append_to_page",
            {
                "page_id": created_id,
                "content": "Second convergence marker",
                "expected_title": titled_item["title"],
                "expected_section_id": section["id"],
                "expected_modified": titled_item.get("modified"),
                "content_format": "plain",
            },
            out / "append-result.json",
        )
        evidence["page_update"] = {
            "convergence": _require_convergence(appended, "append_to_page"),
            "attempt": _require_attempt_contract(appended, "append_to_page"),
        }

        appended_item = appended.get("item")
        if not isinstance(appended_item, dict):
            raise InvariantFailure("Append response omitted the stable Page identity.")
        objects_after_append = await client.call_tool(
            "get_page_objects", {"page_id": created_id}
        )
        fresh_deletable = [
            item
            for item in objects_after_append.get("objects", [])
            if item.get("can_delete") is True
            and item.get("id")
            and item.get("delete_target_id") == item.get("id")
            and str(item["id"]) not in before_object_ids
        ]
        if len(fresh_deletable) != 1:
            raise InvariantFailure(
                "Append must create exactly one fresh deletable content object for the delete contract."
            )
        content_object_id = str(fresh_deletable[0]["delete_target_id"])
        content_deleted = await call_with_result_evidence(
            client,
            "delete_page_content",
            {
                "page_id": created_id,
                "object_id": content_object_id,
                "expected_title": appended_item["title"],
                "expected_section_id": section["id"],
                "expected_modified": appended_item.get("modified"),
            },
            out / "content-delete-result.json",
        )
        evidence["content_delete"] = {
            "convergence": _require_convergence(
                content_deleted, "delete_page_content"
            ),
            "attempt": _require_attempt_contract(
                content_deleted, "delete_page_content"
            ),
        }
        after_content_delete = await capture_snapshot(client, notebook_id)
        current_probe = find_snapshot_item(after_content_delete, created_id)
        if current_probe is None:
            raise InvariantFailure("Content delete lost the disposable Page identity.")
        reordered = await call_with_result_evidence(
            client,
            "reorder_page",
            {
                "page_id": created_id,
                "expected_title": current_probe["title"],
                "expected_section_id": section["id"],
                "after_page_id": first["id"],
                "page_level": 1,
                "expected_modified": current_probe.get("modified"),
            },
            out / "reorder-result.json",
        )
        evidence["reorder"] = {
            "convergence": _require_convergence(reordered, "reorder_page"),
            "attempt": _require_attempt_contract(reordered, "reorder_page"),
        }
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
        evidence["delete"] = {
            "convergence": _require_convergence(deleted, "delete_page"),
            "attempt": _require_attempt_contract(deleted, "delete_page"),
        }
        restored = await capture_snapshot(client, notebook_id)
        write_json(out / "restored.json", restored)
        assert_restored(before, restored)
        restored_notebook = find_snapshot_item(restored, notebook_id)
        if restored_notebook is None:
            raise InvariantFailure("Restored snapshot omitted the disposable Notebook.")
        closed = await call_with_result_evidence(
            client,
            "close_notebook",
            {
                "notebook_id": notebook_id,
                "expected_name": restored_notebook["name"],
                "expected_modified": restored_notebook.get("modified"),
            },
            out / "close-result.json",
        )
        evidence["close"] = {
            "convergence": _require_convergence(closed, "close_notebook"),
            "attempt": _require_attempt_contract(closed, "close_notebook"),
        }
        lifecycle_close = wrappers["source"].adopt_production_close(closed)
        result = {
            "scenario": self.name,
            "status": "passed",
            "fixture": fixture_result,
            "convergence": evidence,
            "close_evidence": evidence["close"],
            "lifecycle_close_handoff": {
                "closed": lifecycle_close["closed"],
                "source_notebook_id": lifecycle_close["source_notebook_id"],
                "close_origin": lifecycle_close["close_origin"],
            },
            "restored": True,
            "worksite_preserved": False,
        }
        write_json(out / "result.json", result)
        return result


__all__ = ["OneNoteConvergenceScenario"]
