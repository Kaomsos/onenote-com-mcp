"""Human-gated cross-Notebook Move of a complete imported OneNote Page.

Revision-marker evidence is retained as a sensitive local diagnostic.  It is
not a post-Move acceptance gate: the production Copy/Move fidelity checks own
the decision to delete the source Page.
"""

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
from .common.interactive_bootstrap import (
    MAX_INTERACTIVE_TIMEOUT,
    InteractiveBootstrapScenarioMixin,
    _bounded_input,
)
from .common.move_page_evidence import (
    lossless_move_diagnostic,
    move_target_id,
    partial_move_details,
)
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.interactive_move_page import RECIPE


def _revision_projection(snapshot: Mapping[str, Any], page_id: str) -> dict[str, Any]:
    projections = snapshot.get("page_revision_marker_projections")
    projection = projections.get(page_id) if isinstance(projections, Mapping) else None
    markers = projection.get("markers") if isinstance(projection, Mapping) else None
    if not (
        isinstance(projection, Mapping)
        and int(projection.get("marker_count", 0)) > 0
        and isinstance(markers, list)
        and len(markers) == int(projection.get("marker_count", -1))
        and all(
            isinstance(marker, Mapping)
            and isinstance(marker.get("value"), str)
            and isinstance(marker.get("value_sha256"), str)
            for marker in markers
        )
        and projection.get("marker_values_exposed") is True
        and projection.get("author_metadata_exposed") is True
        and projection.get("sensitive_evidence") is True
        and projection.get("content_exposed") is False
    ):
        raise InvariantFailure(
            "Whole-Page Move is missing detailed body revision-marker author evidence."
        )
    return dict(projection)


def _revision_identity(projection: Mapping[str, Any]) -> dict[str, Any]:
    markers = [
        {
            "ordinal": marker.get("ordinal"),
            "revision_node_ordinal": marker.get("revision_node_ordinal"),
            "node_kind": marker.get("node_kind"),
            "attribute": marker.get("attribute"),
            "value": marker.get("value"),
            "value_sha256": marker.get("value_sha256"),
        }
        for marker in projection.get("markers", ())
        if isinstance(marker, Mapping)
    ]
    return {
        "schema_version": 2,
        "marker_count": projection.get("marker_count"),
        "attribute_counts": dict(projection.get("attribute_counts", {})),
        "node_counts": dict(projection.get("node_counts", {})),
        "sha256": projection.get("sha256"),
        "markers": markers,
        "marker_values_exposed": True,
        "author_metadata_exposed": True,
        "sensitive_evidence": True,
        "content_exposed": False,
    }


def _marker_comparisons(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
) -> list[dict[str, Any]]:
    source_markers = list(source.get("markers", ()))
    target_markers = list(target.get("markers", ()))
    comparisons: list[dict[str, Any]] = []
    fields = (
        "ordinal",
        "revision_node_ordinal",
        "node_kind",
        "attribute",
        "value",
        "value_sha256",
    )
    for ordinal in range(max(len(source_markers), len(target_markers))):
        source_marker = source_markers[ordinal] if ordinal < len(source_markers) else None
        target_marker = target_markers[ordinal] if ordinal < len(target_markers) else None
        checks = {
            "source_present": source_marker is not None,
            "target_present": target_marker is not None,
            **{
                field: (
                    isinstance(source_marker, Mapping)
                    and isinstance(target_marker, Mapping)
                    and source_marker.get(field) == target_marker.get(field)
                )
                for field in fields
            },
        }
        comparisons.append(
            {
                "ordinal": ordinal,
                "source": dict(source_marker) if isinstance(source_marker, Mapping) else None,
                "target": dict(target_marker) if isinstance(target_marker, Mapping) else None,
                "checks": checks,
                "matched": all(checks.values()),
            }
        )
    return comparisons


def _revision_comparison(
    source_projection: Mapping[str, Any],
    target_projection: Mapping[str, Any] | None,
    *,
    source_page_id: str,
    target_page_id: str | None,
    operation_outcome: str,
    target_revision_error: str | None = None,
) -> dict[str, Any]:
    source = _revision_identity(source_projection)
    target = _revision_identity(target_projection) if target_projection is not None else None
    marker_comparisons = _marker_comparisons(source, target or {"markers": []})
    preserved = target is not None and source == target
    return {
        "schema_version": 2,
        "acceptance": "diagnostic_only",
        "operation_outcome": operation_outcome,
        "source_phase": "before_move_copy",
        "target_phase": "after_move_copy",
        "source_page_id": source_page_id,
        "target_page_id": target_page_id,
        "source": source,
        "target": target,
        "target_revision_error": target_revision_error,
        "comparison_complete": target is not None,
        "marker_comparisons": marker_comparisons,
        "matched_marker_count": sum(
            1 for comparison in marker_comparisons if comparison["matched"]
        ),
        "preserved": preserved,
        "marker_values_exposed": True,
        "author_metadata_exposed": True,
        "sensitive_evidence": True,
        "content_exposed": False,
    }


@SCENARIO_REGISTRY.register
class InteractiveMovePageScenario(InteractiveBootstrapScenarioMixin, Scenario):
    name = "interactive-move-page"
    help_text = (
        "HUMAN-GATED: import one complete revision-marked disposable Page, then call "
        "move_page once across two disposable Notebooks."
    )
    fixture_recipe = RECIPE
    included_in_all = False
    worksite_dry_run_action = "preserve-cross-notebook-whole-page-move-target"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        InteractiveBootstrapScenarioMixin.add_arguments(self, parser)
        parser.add_argument(
            "--template-instance-id",
            help=(
                "Exact authored-<24 hex> instance ID for --use-cache; omit on "
                "fresh runs or when exactly one ready template exists."
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
                "Whole-Page Move requires one scenario-scoped MCP client."
            )
        if args.interactive_timeout < 1 or args.interactive_timeout > MAX_INTERACTIVE_TIMEOUT:
            raise InvariantFailure("--interactive-timeout must be between 1 and 1800 seconds.")
        validate_manifest_notebook(manifest, args.notebook_name)
        cache = manifest.get("fixture_cache", {})
        selected = str(cache.get("template_instance_id", "") or "")
        if not selected:
            raise InvariantFailure("Whole-Page Move has no resolved authored instance.")
        explicit = str(getattr(args, "template_instance_id", "") or "")
        if explicit and explicit != selected:
            raise InvariantFailure(
                "Whole-Page Move manifest differs from the explicit template instance."
            )
        if not (
            cache.get("template_state") == "ready"
            and cache.get("mutation_eligible") is True
            and cache.get("move_source_deletion_allowed") is True
            and cache.get("opened_template") is False
        ):
            raise InvariantFailure(
                "Whole-Page template is not eligible for cross-Notebook source deletion."
            )
        live = cache.get("interactive_live_validation")
        if not isinstance(live, Mapping) or live.get("passed") is not True:
            raise InvariantFailure(
                "Whole-Page fixture did not pass materialized live validation."
            )
        live_revision = live.get("revision_markers")
        if not isinstance(live_revision, Mapping):
            raise InvariantFailure(
                "Whole-Page fixture lost its frozen revision-marker identity."
            )

        notebooks = manifest.get("notebooks")
        if not isinstance(notebooks, Mapping) or set(notebooks) != {
            "source",
            "destination",
        }:
            raise InvariantFailure(
                "Whole-Page Move requires exact source and destination Notebook roles."
            )
        source_notebook_id = str(notebooks["source"]["id"])
        destination_notebook_id = str(notebooks["destination"]["id"])
        structure = manifest.get("structure", {})
        source_id = str(structure["source_canvas_page"]["id"])
        source_section_id = str(structure["source_canvas_section"]["id"])
        destination_section_id = str(structure["destination_section"]["id"])
        destination_anchor_id = str(structure["destination_anchor"]["id"])
        out = scenario_dir(options.run_dir, self.name)
        before_roles = {
            "source": await capture_snapshot(
                client,
                source_notebook_id,
                expose_revision_marker_values=True,
            ),
            "destination": await capture_snapshot(
                client,
                destination_notebook_id,
                expose_revision_marker_values=True,
            ),
        }
        write_json(out / "before.json", {"schema_version": 1, "roles": before_roles})
        source = find_snapshot_item(before_roles["source"], source_id)
        destination_anchor = find_snapshot_item(
            before_roles["destination"], destination_anchor_id
        )
        if (
            source is None
            or source.get("resource_type") != "page"
            or str(source.get("section_id", "")) != source_section_id
            or int(source.get("page_level", 1)) != 1
            or source.get("parent_page_id") is not None
        ):
            raise InvariantFailure(
                "Whole-Page Move source is not the exact imported root leaf Page."
            )
        if not (
            destination_anchor is not None
            and destination_anchor.get("resource_type") == "page"
            and str(destination_anchor.get("section_id", ""))
            == destination_section_id
        ):
            raise InvariantFailure(
                "Whole-Page Move destination anchor escaped its exact Section."
            )
        page_sequence = [
            item
            for item in before_roles["source"].get("items", ())
            if isinstance(item, Mapping)
            and item.get("resource_type") == "page"
            and str(item.get("section_id", "")) == source_section_id
        ]
        source_index = next(
            (
                index
                for index, item in enumerate(page_sequence)
                if str(item.get("id")) == source_id
            ),
            -1,
        )
        if source_index < 0 or (
            source_index + 1 < len(page_sequence)
            and int(page_sequence[source_index + 1].get("page_level", 1)) > 1
        ):
            raise InvariantFailure("Whole-Page Move source unexpectedly has subpages.")
        source_revision = _revision_projection(before_roles["source"], source_id)
        if _revision_identity(source_revision) != _revision_identity(live_revision):
            raise InvariantFailure(
                "Whole-Page Move source revision markers differ from the frozen live fixture."
            )

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
            partial = partial_move_details(exc.envelope)
            after_roles = {
                "source": await capture_snapshot(
                    client,
                    source_notebook_id,
                    expose_revision_marker_values=True,
                ),
                "destination": await capture_snapshot(
                    client,
                    destination_notebook_id,
                    expose_revision_marker_values=True,
                ),
            }
            write_json(
                out / "after-failure.json",
                {"schema_version": 1, "roles": after_roles},
            )
            target_id = move_target_id(partial, source_id)
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
            target_revision = None
            target_revision_error = None
            if target_id and target_active:
                try:
                    target_revision = _revision_projection(
                        after_roles["destination"],
                        target_id,
                    )
                except InvariantFailure as revision_exc:
                    target_revision_error = str(revision_exc)
            write_json(
                out / "revision-marker-comparison.json",
                _revision_comparison(
                    source_revision,
                    target_revision,
                    source_page_id=source_id,
                    target_page_id=target_id,
                    operation_outcome=str(partial.get("outcome", "partial_failure")),
                    target_revision_error=target_revision_error,
                ),
            )
            write_json(
                out / "lossless-diagnostic.json",
                lossless_move_diagnostic(
                    partial,
                    source_id=source_id,
                    target_id=target_id,
                    source_active=source_active,
                    target_active_in_destination=target_active,
                    follow_up_todo=None,
                ),
            )
            if partial.get("outcome") in {"copy_only", "copy_unverified"} and not source_active:
                raise InvariantFailure(
                    "Whole-Page lossless failure did not preserve the exact source Page."
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
                "Whole-Page Move returned success without the complete "
                "lossless-before-delete gate."
            )
        target_id = move_target_id(moved, source_id)
        if not target_id:
            raise InvariantFailure("Whole-Page Move returned no exact destination Page ID.")
        after_roles = {
            "source": await capture_snapshot(
                client,
                source_notebook_id,
                expose_revision_marker_values=True,
            ),
            "destination": await capture_snapshot(
                client,
                destination_notebook_id,
                expose_revision_marker_values=True,
            ),
        }
        write_json(out / "after.json", {"schema_version": 1, "roles": after_roles})
        source_active = find_snapshot_item(after_roles["source"], source_id) is not None
        target = find_snapshot_item(after_roles["destination"], target_id)
        anchor_after = find_snapshot_item(
            after_roles["destination"], destination_anchor_id
        )
        if (
            source_active
            or target is None
            or str(target.get("section_id", "")) != destination_section_id
            or int(target.get("page_level", 1)) != 1
            or target.get("parent_page_id") is not None
            or anchor_after is None
        ):
            raise InvariantFailure(
                "Whole-Page Move final topology does not match its exact "
                "source/destination contract."
            )
        target_revision = None
        target_revision_error = None
        try:
            target_revision = _revision_projection(
                after_roles["destination"], target_id
            )
        except InvariantFailure as revision_exc:
            target_revision_error = str(revision_exc)
        revision_comparison = _revision_comparison(
            source_revision,
            target_revision,
            source_page_id=source_id,
            target_page_id=target_id,
            operation_outcome="moved",
            target_revision_error=target_revision_error,
        )
        write_json(out / "revision-marker-comparison.json", revision_comparison)
        write_json(
            out / "lossless-diagnostic.json",
            lossless_move_diagnostic(
                moved,
                source_id=source_id,
                target_id=target_id,
                source_active=False,
                target_active_in_destination=True,
                follow_up_todo=None,
            ),
        )

        confirmation = f"ACCEPT {options.run_dir.name} MovePage MOVE"
        response = (
            await _bounded_input(
                f"Inspect the exact moved target and type {confirmation!r}: ",
                args.interactive_timeout,
            )
        ).strip()
        if response != confirmation:
            raise InvariantFailure(
                "Whole-Page Move human verdict was not the exact positive run-bound phrase."
            )
        acceptance = {
            "schema_version": 1,
            "scenario": self.name,
            "template_instance_id": selected,
            "target_page_id": target_id,
            "human_verdict": "accepted",
            "confirmation_bound_to_run": True,
            "machine_lossless_gate_passed": True,
            "revision_marker_comparison": "diagnostic_only",
            "revision_markers_preserved": revision_comparison["preserved"],
            "author_metadata_exposed": True,
            "sensitive_evidence": True,
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
            "revision_marker_comparison": "diagnostic_only",
            "revision_markers_preserved": revision_comparison["preserved"],
            "author_metadata_exposed": True,
            "sensitive_evidence": True,
            "human_verdict": "accepted",
            "restored": False,
            "remaining_state": remaining,
        }
        write_json(out / "result.json", result)
        return result


__all__ = ["InteractiveMovePageScenario"]
