"""Fixture-only create scenario."""

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
    snapshot_ids,
    validate_manifest_notebook,
    write_json,
)
from .base import Scenario
from .common.copy_runtime import call_with_result_evidence
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.create import RECIPE


@SCENARIO_REGISTRY.register
class CreateScenario(Scenario):
    name = "create"
    fixture_recipe = RECIPE
    help_text = (
        "GATED: create the preset isolated Notebook fixture, create two same-title "
        "Pages with fresh IDs, verify and clean them up, then close or keep."
    )
    included_in_all = True
    worksite_dry_run_action = "preserve-created-fixture-and-duplicate-title-pages"

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
            raise RunnerFailure("Create scenario requires its single active scenario MCP client.")
        notebook_id = validate_manifest_notebook(manifest, args.notebook_name)
        section = resolve_manifest_item(manifest, "duplicate_title_section")
        out = scenario_dir(options.run_dir, self.name)
        before = await capture_snapshot(client, notebook_id)
        write_json(out / "before.json", before)
        before_ids = snapshot_ids(before)
        title = "Duplicate-Title-Regression"
        created: list[dict[str, Any]] = []
        for ordinal in (1, 2):
            response = await call_with_result_evidence(
                client,
                "create_page",
                {
                    "section_id": section["id"],
                    "title": title,
                    "content": f"Duplicate identity regression marker {ordinal}",
                    "content_format": "plain",
                },
                out / f"create-{ordinal}-result.json",
            )
            page_id = str(response.get("page_id", ""))
            allocated_id = str(response.get("allocated_id", ""))
            created.append(
                {
                    "ordinal": ordinal,
                    "page_id": page_id,
                    "allocated_id": allocated_id,
                    "identity_remapped": bool(response.get("identity_remapped", False)),
                    "section_id": response.get("page", {}).get("section_id"),
                }
            )
            write_json(out / "create-results.json", {"created": created})
        after = await capture_snapshot(client, notebook_id)
        write_json(out / "after.json", after)
        page_ids = [item["page_id"] for item in created]
        allocated_ids = [item["allocated_id"] for item in created]
        if any(not value for value in [*page_ids, *allocated_ids]):
            raise InvariantFailure("Create response omitted an allocated or read-back Page ID.")
        if len(set(page_ids)) != 2 or set(page_ids) & before_ids:
            raise InvariantFailure("Same-title Create did not return two fresh, distinct Page IDs.")
        if page_ids != allocated_ids:
            raise InvariantFailure(
                "Same-title Create unexpectedly remapped a COM allocated Page ID."
            )
        pages = [find_snapshot_item(after, page_id) for page_id in page_ids]
        if any(
            page is None
            or page.get("resource_type") != "page"
            or page.get("section_id") != section["id"]
            or page.get("title") != title
            for page in pages
        ):
            raise InvariantFailure(
                "Same-title Create read-back is missing, mistyped, or outside the target Section."
            )
        hashes = after.get("page_hashes", {})
        if len({hashes.get(page_id) for page_id in page_ids}) != 2:
            raise InvariantFailure("Same-title Create bodies were not independently readable.")

        keep_worksite = bool(getattr(args, "keep_worksite", False))
        cleanup_results: list[dict[str, Any]] = []
        restored = False
        if not keep_worksite:
            cleanup_targets = list(
                enumerate(
                    [page for page in pages if page is not None],
                    start=1,
                )
            )
            for ordinal, page in reversed(cleanup_targets):
                cleanup_results.append(
                    await call_with_result_evidence(
                        client,
                        "delete_page",
                        {
                            "page_id": page["id"],
                            "expected_title": page["title"],
                            "expected_section_id": page["section_id"],
                            "expected_modified": page.get("modified"),
                            "permanently": False,
                        },
                        out / f"cleanup-created-page-{ordinal:02d}-result.json",
                    )
                )
            restored_snapshot = await capture_snapshot(client, notebook_id)
            write_json(out / "restored.json", restored_snapshot)
            assert_restored(before, restored_snapshot)
            restored = True

        result = {
            "scenario": self.name,
            "status": "passed",
            "fixture": fixture_result,
            "duplicate_title_regression": {
                "allocated_ids": allocated_ids,
                "read_back_ids": page_ids,
                "fresh_and_distinct": True,
                "target_section_id": section["id"],
                "bodies_independently_readable": True,
            },
            "cleanup_results": cleanup_results,
            "restored": restored,
            "worksite_preserved": keep_worksite,
        }
        if keep_worksite:
            notebook = manifest["notebook"]
            worksite = {
                "status": "duplicate_title_pages_preserved",
                "target_ids": page_ids,
                "notebook_id": notebook["id"],
                "notebook_name": notebook["name"],
                "manual_cleanup_required": True,
                "cleanup": (
                    "Inspect both same-title Pages, then delete the two exact target IDs "
                    "non-permanently and close the disposable source Notebook."
                ),
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
