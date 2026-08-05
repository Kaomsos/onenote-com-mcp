"""Mutation scenario selection, dry-run planning, and failure handoff."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..mcp_stdio_client import (
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    DELETE_POLICY,
    MOVE_POLICY,
    WRITE_POLICY,
)
from ..runner import (
    EXIT_RESTORE,
    RunnerFailure,
    RuntimeOptions,
    dry_run_result,
    load_manifest,
    read_json,
    resolve_manifest_item,
    scenario_dir,
    timestamp,
    utc_now,
    validate_manifest_notebook,
    write_json,
)
from ._config import (
    COPY_NOTEBOOK_TOOLS,
    COPY_TOOLS,
    DELETE_TOOLS,
    MOVE_TOOLS,
    RECONSTRUCTIVE_MOVE_PAGE_TOOLS,
    RENAME_TOOLS,
    REORDER_TOOLS,
)
from .copy import run_copy
from .delete import run_delete
from .move import run_move
from .reconstructive_move_page import run_reconstructive_move_page
from .rename import run_rename
from .reorder import run_reorder
from .report import render_report
from ..mcp_stdio_client import RECONSTRUCTIVE_MOVE_PAGE_POLICY

MUTATION_SCENARIO_RUNNERS = {
    "rename": run_rename,
    "reorder": run_reorder,
    "move": run_move,
    "delete": run_delete,
    "copy-page": run_copy,
    "copy-section": run_copy,
    "copy-section-group": run_copy,
    "copy-notebook": run_copy,
    "reconstructive-move-page": run_reconstructive_move_page,
}

async def run_validate(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    manifest = load_manifest(options.run_dir)
    if args.scenario == "reorder" and args.page_level < 1:
        raise RunnerFailure("--page-level must be at least 1.")
    policy_tools = {
        "rename": (WRITE_POLICY, RENAME_TOOLS),
        "reorder": (WRITE_POLICY, REORDER_TOOLS),
        "move": (MOVE_POLICY, MOVE_TOOLS),
        "delete": (DELETE_POLICY, DELETE_TOOLS),
        "copy-page": (COPY_POLICY, COPY_TOOLS),
        "copy-section": (COPY_POLICY, COPY_TOOLS),
        "copy-section-group": (COPY_POLICY, COPY_TOOLS),
        "copy-notebook": (COPY_NO_DELETE_POLICY, COPY_NOTEBOOK_TOOLS),
        "reconstructive-move-page": (
            RECONSTRUCTIVE_MOVE_PAGE_POLICY,
            RECONSTRUCTIVE_MOVE_PAGE_TOOLS,
        ),
    }
    policy, tools = policy_tools[args.scenario]
    if options.dry_run:
        notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
        target_keys = {
            "rename": getattr(args, "target", "move_source"),
            "reorder": "sibling_page",
            "move": "move_source",
            "copy-page": "parent_page",
            "copy-section": "move_source",
            "copy-section-group": "group_a",
            "copy-notebook": None,
            "reconstructive-move-page": "disposable_page",
        }
        if args.scenario == "delete":
            allowed_ids = {
                resolve_manifest_item(manifest, key)["id"]
                for key in ("disposable_group", "disposable_section", "disposable_page")
            }
            if args.delete_target_id not in allowed_ids:
                raise RunnerFailure("Delete target is not one of the manifest-allowlisted disposable IDs.")
            target_id = args.delete_target_id
        else:
            target_key = target_keys[args.scenario]
            target_id = (
                manifest["notebook"]["id"]
                if target_key is None
                else resolve_manifest_item(manifest, target_key)["id"]
            )
        result = dry_run_result(args.scenario, policy, tools, notebook_id, options)
        result["target_id"] = target_id
        return result
    return await MUTATION_SCENARIO_RUNNERS[args.scenario](args, options, manifest)

def record_failure(args: argparse.Namespace, message: str, exit_code: int) -> None:
    """Persist a failure handoff without masking the original exception."""

    try:
        if getattr(args, "command", None) != "validate" or not getattr(args, "run_dir", None):
            return
        run_dir = Path(args.run_dir)
        out = scenario_dir(run_dir, args.scenario)
        completed_artifacts = [
            name
            for name in ("before.json", "plan.json", "copy-result.json", "after.json", "restored.json")
            if (out / name).exists()
        ]
        mutation_result = (
            read_json(out / "copy-result.json")
            if "copy-result.json" in completed_artifacts
            else {}
        )
        created_ids = mutation_result.get("created_ids", [])
        needs_manual_cleanup = bool(created_ids) or mutation_result.get("outcome") in {
            "copy_only",
            "copy_unverified",
            "source_partially_recycled",
            "source_recycle_unverified",
            "source_delete_failed",
        }
        manifest = load_manifest(run_dir)
        target_keys = {
            "rename": getattr(args, "target", "move_source"),
            "reorder": "sibling_page",
            "move": "move_source",
            "copy-page": "parent_page",
            "copy-section": "move_source",
            "copy-section-group": "group_a",
            "copy-notebook": None,
            "reconstructive-move-page": "disposable_page",
        }
        if args.scenario == "delete":
            target_id = getattr(args, "delete_target_id", "")
        else:
            target_key = target_keys[args.scenario]
            target_id = (
                manifest.get("notebook", {}).get("id", "")
                if target_key is None
                else manifest.get("structure", {}).get(target_key, {}).get("id", "")
            )
        notebook_id = manifest.get("notebook", {}).get("id", "")
        last_step = "preflight"
        if "before.json" in completed_artifacts:
            last_step = "capture_before"
        if "copy-result.json" in completed_artifacts:
            last_step = "execute_mutation"
        if "after.json" in completed_artifacts:
            last_step = "capture_after"
        if "restored.json" in completed_artifacts:
            last_step = "capture_restored"
        failure = {
            "scenario": args.scenario,
            "status": (
                "needs_manual_cleanup"
                if needs_manual_cleanup
                else "needs_manual_restore" if exit_code == EXIT_RESTORE else "failed"
            ),
            "exit_code": exit_code,
            "error": message,
            "target_id": target_id,
            "last_successful_step": last_step,
            "completed_artifacts": completed_artifacts,
            "outcome": mutation_result.get("outcome"),
            "created_ids": created_ids,
            "id_map": (
                mutation_result.get("copy_report", {}).get("id_map")
                or mutation_result.get("id_map", {})
            ),
            "restored": True if "restored.json" in completed_artifacts and exit_code != EXIT_RESTORE else "unknown",
            "failed_at": utc_now(),
            "suggested_next_step": (
                ".venv\\Scripts\\python.exe tests\\manual_isolated\\run.py read "
                f"--notebook-id {notebook_id!r} --output .local-validation\\recovery-{timestamp()}"
            ),
        }
        write_json(out / "failure.json", failure)
        render_report(Path(args.run_dir))
    except Exception:
        pass
