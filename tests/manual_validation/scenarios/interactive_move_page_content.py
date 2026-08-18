"""Human-gated Move of one frozen representative real-content Page."""

from __future__ import annotations

import argparse
from typing import Any, Mapping

from ..mcp_stdio_client import ClientFailure, MCPStdioClient
from ..runtime import InvariantFailure, RuntimeOptions
from ..test_utils import (
    capture_snapshot,
    display_name,
    find_snapshot_item,
    scenario_dir,
    validate_manifest_notebook,
    write_json,
)
from .base import Scenario
from .common.copy_runtime import call_with_result_evidence
from .common.interactive_bootstrap import InteractiveBootstrapScenarioMixin, MAX_INTERACTIVE_TIMEOUT, _bounded_input
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.move_page_content import RECIPE


def _partial_details(envelope: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(envelope or {})
    error = payload.get("error")
    if isinstance(error, Mapping):
        details = error.get("details")
        if isinstance(details, Mapping):
            return {"code": error.get("code"), **dict(details)}
    return payload


def _copy_target_id(result: Mapping[str, Any], source_id: str) -> str:
    destination = result.get("destination") or result.get("item") or {}
    if isinstance(destination, Mapping) and destination.get("id"):
        return str(destination["id"])
    report = result.get("copy_report")
    id_map = report.get("id_map") if isinstance(report, Mapping) else None
    if isinstance(id_map, Mapping) and id_map.get(source_id):
        return str(id_map[source_id])
    created = result.get("created_ids")
    if isinstance(created, list) and len(created) == 1 and created[0]:
        return str(created[0])
    return ""


def _lossless_diagnostic(
    result: Mapping[str, Any],
    *,
    source_id: str,
    target_id: str,
    source_active: bool,
    target_active_in_destination: bool,
) -> dict[str, Any]:
    report = result.get("copy_report")
    report = report if isinstance(report, Mapping) else {}
    page_results: list[dict[str, Any]] = []
    for value in report.get("page_results", ()):
        if not isinstance(value, Mapping):
            continue
        equivalence = value.get("equivalence")
        equivalence = equivalence if isinstance(equivalence, Mapping) else {}
        semantic = equivalence.get("semantic_content_comparison")
        semantic_stages = value.get("semantic_content_stages")
        page_results.append(
            {
                "source_page_id": value.get("source_page_id"),
                "target_page_id": value.get("target_page_id"),
                "lossless": value.get("lossless"),
                "verification_tier": equivalence.get("verification_tier"),
                "acceptance_checks": list(equivalence.get("acceptance_checks", ())),
                "checks": dict(equivalence.get("checks", {})),
                "equivalent": equivalence.get("equivalent"),
                "semantic_projection": (
                    {
                        "source_complete": semantic.get("source_complete"),
                        "target_complete": semantic.get("target_complete"),
                        "checks": dict(semantic.get("checks", {})),
                        "passed": semantic.get("passed"),
                    }
                    if isinstance(semantic, Mapping)
                    else None
                ),
                "semantic_content_stages": (
                    dict(semantic_stages)
                    if isinstance(semantic_stages, Mapping)
                    else None
                ),
                "normalizations": dict(value.get("normalizations", {})),
            }
        )
    issues = [dict(value) for value in report.get("issues", ()) if isinstance(value, Mapping)]
    return {
        "schema_version": 1,
        "operation": "move_page",
        "outcome": result.get("outcome"),
        "failed_step": result.get("failed_step"),
        "source_page_id": source_id,
        "target_page_id": target_id or None,
        "source_active_after_failure": source_active,
        "target_active_in_destination": target_active_in_destination,
        "source_deleted": result.get("source_deleted"),
        "verified": report.get("verified"),
        "lossless": report.get("lossless"),
        "copy_contract_satisfied": report.get("copy_contract_satisfied"),
        "lossless_candidate": (
            report.get("planning", {}).get("lossless_candidate")
            if isinstance(report.get("planning"), Mapping)
            else None
        ),
        "content_capabilities": (
            list(report.get("planning", {}).get("content_capabilities", ()))
            if isinstance(report.get("planning"), Mapping)
            else []
        ),
        "issues": issues,
        "skipped_content": [
            dict(value)
            for value in report.get("skipped_content", ())
            if isinstance(value, Mapping)
        ],
        "page_results": page_results,
        "semantic_content_stages_available": any(
            value.get("semantic_content_stages") is not None for value in page_results
        ),
        "follow_up_todo": "039_interactive_real_page_move_lossless_validation.md",
        "content_exposed": False,
    }


@SCENARIO_REGISTRY.register
class InteractiveMovePageContentScenario(InteractiveBootstrapScenarioMixin, Scenario):
    name = "interactive-move-page-content"
    help_text = (
        "HUMAN-GATED: author or reuse one representative-content Page, then move it "
        "across disposable Notebooks and capture the lossless gate."
    )
    fixture_recipe = RECIPE
    included_in_all = False
    worksite_dry_run_action = "preserve-interactive-move-content-evidence"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        InteractiveBootstrapScenarioMixin.add_arguments(self, parser)
        parser.add_argument(
            "--template-instance-id",
            help=(
                "Exact authored-<24 hex> instance ID for --use-cache; "
                "omit on fresh runs or when exactly one ready template exists."
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
        del fixture_result
        if client is None:
            raise InvariantFailure(
                "Interactive representative-content Move requires one scenario MCP client."
            )
        if args.interactive_timeout < 1 or args.interactive_timeout > MAX_INTERACTIVE_TIMEOUT:
            raise InvariantFailure("--interactive-timeout must be between 1 and 1800 seconds.")
        validate_manifest_notebook(manifest, args.notebook_name)
        cache = manifest.get("fixture_cache", {})
        selected = str(cache.get("template_instance_id", "") or "")
        if not selected:
            raise InvariantFailure(
                "Live representative-content fixture has no resolved instance."
            )
        explicit = str(getattr(args, "template_instance_id", "") or "")
        if explicit and explicit != selected:
            raise InvariantFailure(
                "Live representative-content fixture differs from the explicit instance."
            )
        if not (
            cache.get("template_state") == "ready"
            and cache.get("mutation_eligible") is True
            and cache.get("move_source_deletion_allowed") is True
        ):
            raise InvariantFailure(
                "Representative-content template is not eligible for source deletion."
            )
        live_validation = cache.get("interactive_live_validation", {})
        if not isinstance(live_validation, Mapping) or live_validation.get("passed") is not True:
            raise InvariantFailure(
                "Cached representative-content fixture did not pass live validation."
            )

        notebooks = manifest.get("notebooks")
        if not isinstance(notebooks, Mapping) or set(notebooks) != {"source", "destination"}:
            raise InvariantFailure(
                "Interactive Move requires exact source and destination Notebook roles."
            )
        source_notebook_id = str(notebooks["source"]["id"])
        destination_notebook_id = str(notebooks["destination"]["id"])
        source_id = str(manifest["structure"]["source_canvas_page"]["id"])
        source_section_id = str(manifest["structure"]["source_canvas_section"]["id"])
        destination_section_id = str(manifest["structure"]["destination_section"]["id"])
        out = scenario_dir(options.run_dir, self.name)
        before_roles = {
            "source": await capture_snapshot(client, source_notebook_id),
            "destination": await capture_snapshot(client, destination_notebook_id),
        }
        write_json(out / "before.json", {"schema_version": 1, "roles": before_roles})
        source = find_snapshot_item(before_roles["source"], source_id)
        if (
            source is None
            or source.get("resource_type") != "page"
            or str(source.get("section_id", "")) != source_section_id
            or int(source.get("page_level", 1)) != 1
            or source.get("parent_page_id") is not None
        ):
            raise InvariantFailure(
                "Representative Move source is not the exact manifest-bound root leaf Page."
            )
        page_sequence = [
            item
            for item in before_roles["source"].get("items", ())
            if isinstance(item, Mapping)
            and item.get("resource_type") == "page"
            and str(item.get("section_id", "")) == source_section_id
        ]
        source_index = next(
            (index for index, item in enumerate(page_sequence) if str(item.get("id")) == source_id),
            -1,
        )
        if source_index < 0 or (
            source_index + 1 < len(page_sequence)
            and int(page_sequence[source_index + 1].get("page_level", 1)) > 1
        ):
            raise InvariantFailure("Representative Move source unexpectedly has subpages.")

        arguments = {
            "page_id": source_id,
            "destination_section_id": destination_section_id,
            "expected_title": display_name(source),
            "expected_section_id": source_section_id,
            "expected_modified": source.get("modified"),
            "include_subpages": False,
        }
        try:
            moved = await call_with_result_evidence(
                client,
                "move_page",
                arguments,
                out / "move-result.json",
            )
        except ClientFailure as exc:
            partial = _partial_details(exc.envelope)
            after_roles = {
                "source": await capture_snapshot(client, source_notebook_id),
                "destination": await capture_snapshot(client, destination_notebook_id),
            }
            write_json(
                out / "after-failure.json",
                {"schema_version": 1, "roles": after_roles},
            )
            target_id = _copy_target_id(partial, source_id)
            source_active = find_snapshot_item(after_roles["source"], source_id) is not None
            target = (
                find_snapshot_item(after_roles["destination"], target_id)
                if target_id
                else None
            )
            target_active = bool(
                target is not None
                and str(target.get("section_id", "")) == destination_section_id
            )
            diagnostic = _lossless_diagnostic(
                partial,
                source_id=source_id,
                target_id=target_id,
                source_active=source_active,
                target_active_in_destination=target_active,
            )
            write_json(out / "lossless-diagnostic.json", diagnostic)
            if partial.get("outcome") in {"copy_only", "copy_unverified"} and not source_active:
                raise InvariantFailure(
                    "Lossless failure did not preserve the exact source Page."
                ) from exc
            raise

        report = moved.get("copy_report")
        report = report if isinstance(report, Mapping) else {}
        if not (
            report.get("verified") is True
            and report.get("lossless") is True
            and report.get("copy_contract_satisfied") is True
            and moved.get("source_deleted_nonpermanently") is True
            and moved.get("include_descendants") is False
        ):
            raise InvariantFailure(
                "Interactive Move returned success without the complete lossless-before-delete gate."
            )
        target_id = _copy_target_id(moved, source_id)
        if not target_id:
            raise InvariantFailure("Interactive Move returned no exact destination Page ID.")
        after_roles = {
            "source": await capture_snapshot(client, source_notebook_id),
            "destination": await capture_snapshot(client, destination_notebook_id),
        }
        write_json(out / "after.json", {"schema_version": 1, "roles": after_roles})
        source_active = find_snapshot_item(after_roles["source"], source_id) is not None
        target = find_snapshot_item(after_roles["destination"], target_id)
        if (
            source_active
            or target is None
            or str(target.get("section_id", "")) != destination_section_id
        ):
            raise InvariantFailure(
                "Interactive Move final topology does not match its exact source/destination contract."
            )
        diagnostic = _lossless_diagnostic(
            moved,
            source_id=source_id,
            target_id=target_id,
            source_active=False,
            target_active_in_destination=True,
        )
        write_json(out / "lossless-diagnostic.json", diagnostic)

        confirmation = f"ACCEPT {options.run_dir.name} MovePageContent"
        response = (
            await _bounded_input(
                f"Inspect the exact moved target and type {confirmation!r}: ",
                args.interactive_timeout,
            )
        ).strip()
        if response != confirmation:
            raise InvariantFailure(
                "Interactive Move human verdict was not the exact positive run-bound phrase."
            )
        acceptance = {
            "schema_version": 1,
            "scenario": self.name,
            "template_instance_id": selected,
            "target_page_id": target_id,
            "human_verdict": "accepted",
            "confirmation_bound_to_run": True,
            "machine_lossless_gate_passed": True,
            "content_exposed": False,
        }
        write_json(out / "human-acceptance.json", acceptance)
        remaining = {
            "status": "moved_target_preserved_for_inspection",
            "source_page_id": source_id,
            "target_page_id": target_id,
            "source_deleted_nonpermanently": True,
            "manual_cleanup_required": True,
        }
        write_json(out / "worksite.json", remaining)
        result = {
            "scenario": self.name,
            "status": "passed",
            "template_instance_id": selected,
            "target_page_id": target_id,
            "verified": True,
            "lossless": True,
            "copy_contract_satisfied": True,
            "source_deleted_nonpermanently": True,
            "human_verdict": "accepted",
            "restored": False,
            "remaining_state": remaining,
        }
        write_json(out / "result.json", result)
        return result


__all__ = [
    "InteractiveMovePageContentScenario",
    "_copy_target_id",
    "_lossless_diagnostic",
    "_partial_details",
]
