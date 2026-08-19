"""Pure contracts for complete-Page interactive cross-Notebook Move."""

from __future__ import annotations

import argparse
import asyncio
import hashlib

import pytest

from tests.manual_validation.mcp_stdio_client import ClientFailure
from tests.manual_validation.runtime import InvariantFailure, RuntimeOptions
from tests.manual_validation.scenarios import interactive_move_page as move_page
from tests.manual_validation.scenarios.common import fixture_runtime, interactive_bootstrap
from tests.manual_validation.scenarios.common.fixture_models import FixtureBuildResult
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.fixture_recipes.interactive_move_page import (
    PLACEHOLDER_TITLE,
)
from tests.manual_validation.scenarios.fixture_recipes.recipe_base import (
    FixtureBundleObservation,
    FixtureRoleObservation,
)
from tests.manual_validation.test_utils import read_json


SOURCE_ID = "imported-page"
TARGET_ID = "target-page"
INSTANCE_ID = "authored-" + "a" * 24


def _initial_source_structure() -> dict:
    return {
        "source_instructions_section": {
            "id": "instructions-section",
            "resource_type": "section",
            "parent_id": "source-notebook",
        },
        "source_instructions_page": {
            "id": "instructions-page",
            "resource_type": "page",
            "section_id": "instructions-section",
            "title": "00-Reserved-Marker-Do-Not-Edit",
            "page_level": 1,
        },
        "source_canvas_section": {
            "id": "intake-section",
            "resource_type": "section",
            "parent_id": "source-notebook",
            "name": "01-Whole-Page-Intake",
        },
        "source_canvas_page": {
            "id": "placeholder-page",
            "resource_type": "page",
            "section_id": "instructions-section",
            "title": PLACEHOLDER_TITLE,
            "page_level": 1,
        },
    }


def _imported_page() -> dict:
    return {
        "id": SOURCE_ID,
        "resource_type": "page",
        "section_id": "intake-section",
        "title": "Imported revision-marked Page",
        "modified": "2026-08-19T10:00:00+08:00",
        "page_level": 1,
        "parent_page_id": None,
        "path": "01-Whole-Page-Intake/Imported revision-marked Page",
    }


def _frozen_source_structure() -> dict:
    structure = _initial_source_structure()
    structure["source_canvas_page"] = _imported_page()
    return structure


def _destination_structure() -> dict:
    return {
        "destination_section": {
            "id": "destination-section",
            "resource_type": "section",
            "parent_id": "destination-notebook",
        },
        "destination_anchor": {
            "id": "destination-anchor",
            "resource_type": "page",
            "section_id": "destination-section",
            "title": "99-Destination-Anchor",
            "page_level": 1,
        },
    }


def _revision(
    revision_sha256: str = "revision-hash",
    *,
    last_modified_by: str = "Bob",
) -> dict:
    values = (
        (0, "Outline", "author", "Alice"),
        (0, "Outline", "authorInitials", "AA"),
        (1, "OE", "lastModifiedBy", last_modified_by),
    )
    return {
        "schema_version": 2,
        "marker_count": 3,
        "attribute_counts": {
            "author": 1,
            "authorInitials": 1,
            "lastModifiedBy": 1,
        },
        "node_counts": {"OE": 1, "Outline": 1},
        "sha256": revision_sha256,
        "markers": [
            {
                "ordinal": ordinal,
                "revision_node_ordinal": node_ordinal,
                "node_kind": node_kind,
                "attribute": attribute,
                "value": value,
                "value_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            }
            for ordinal, (node_ordinal, node_kind, attribute, value) in enumerate(values)
        ],
        "marker_values_exposed": True,
        "author_metadata_exposed": True,
        "sensitive_evidence": True,
        "content_exposed": False,
    }


def _source_snapshot(
    *,
    moved: bool = False,
    revision_sha256: str = "revision-hash",
) -> dict:
    structure = _initial_source_structure()
    imported = _imported_page()
    items = list(structure.values()) + ([] if moved else [imported])
    return {
        "notebook_id": "source-notebook",
        "items": items,
        "page_hashes": {
            "instructions-page": "instructions-hash",
            "placeholder-page": "placeholder-hash",
            **({} if moved else {SOURCE_ID: "imported-hash"}),
        },
        "page_body_hashes": {
            "instructions-page": "instructions-body-hash",
            "placeholder-page": "placeholder-body-hash",
            **({} if moved else {SOURCE_ID: "imported-body-hash"}),
        },
        "page_semantic_content_identities": {
            **(
                {}
                if moved
                else {
                    SOURCE_ID: {
                        "schema_version": 2,
                        "complete": True,
                        "sha256": "semantic-hash",
                        "persistence_sha256": "persistence-hash",
                        "materialization_sha256": "materialization-hash",
                    }
                }
            )
        },
        "page_objects": {
            "instructions-page": [{"kind": "Outline"}],
            "placeholder-page": [{"kind": "Outline"}],
            **(
                {}
                if moved
                else {SOURCE_ID: [{"kind": "Outline"}, {"kind": "Table"}]}
            ),
        },
        "page_capability_projections": {
            "instructions-page": {
                "capabilities": ["Outline", "RichText"],
                "unknown_nodes": [],
                "unsupported_page_roots": [],
                "complete": True,
            },
            "placeholder-page": {
                "capabilities": ["Outline", "RichText"],
                "unknown_nodes": [],
                "unsupported_page_roots": [],
                "complete": True,
            },
            **(
                {}
                if moved
                else {
                    SOURCE_ID: {
                        "capabilities": ["Outline", "RichText", "Table"],
                        "unknown_nodes": [],
                        "unsupported_page_roots": [],
                        "complete": True,
                    }
                }
            ),
        },
        "page_revision_marker_projections": {
            "instructions-page": {"marker_count": 0, "content_exposed": False},
            "placeholder-page": {"marker_count": 0, "content_exposed": False},
            **({} if moved else {SOURCE_ID: _revision(revision_sha256)}),
        },
    }


def _destination_snapshot(
    *,
    with_target: bool = False,
    revision_sha256: str = "revision-hash",
    last_modified_by: str = "Bob",
) -> dict:
    structure = _destination_structure()
    target = {
        "id": TARGET_ID,
        "resource_type": "page",
        "section_id": "destination-section",
        "title": "Imported revision-marked Page",
        "page_level": 1,
        "parent_page_id": None,
    }
    return {
        "notebook_id": "destination-notebook",
        "items": [*structure.values(), *([target] if with_target else [])],
        "page_hashes": {
            "destination-anchor": "anchor-hash",
            **({TARGET_ID: "target-hash"} if with_target else {}),
        },
        "page_objects": {
            "destination-anchor": [{"kind": "Outline"}],
            **(
                {TARGET_ID: [{"kind": "Outline"}, {"kind": "Table"}]}
                if with_target
                else {}
            ),
        },
        "page_capability_projections": {
            "destination-anchor": {
                "capabilities": ["Outline", "RichText"],
                "unknown_nodes": [],
                "unsupported_page_roots": [],
                "complete": True,
            }
        },
        "page_revision_marker_projections": {
            **(
                {
                    TARGET_ID: _revision(
                        revision_sha256,
                        last_modified_by=last_modified_by,
                    )
                }
                if with_target
                else {}
            )
        },
    }


def _observation(
    revision_sha256: str = "revision-hash",
    *,
    scaffold_build: bool = False,
) -> FixtureBundleObservation:
    return FixtureBundleObservation(
        roles={
            "source": FixtureRoleObservation(
                role="source",
                args=argparse.Namespace(),
                notebook={"id": "source-notebook"},
                notebook_path="C:/working/source",
                snapshot=_source_snapshot(revision_sha256=revision_sha256),
                build=FixtureBuildResult(
                    (
                        _initial_source_structure()
                        if scaffold_build
                        else _frozen_source_structure()
                    ),
                    {},
                ),
            ),
            "destination": FixtureRoleObservation(
                role="destination",
                args=argparse.Namespace(),
                notebook={"id": "destination-notebook"},
                notebook_path="C:/working/destination",
                snapshot=_destination_snapshot(),
                build=FixtureBuildResult(_destination_structure(), {}),
            ),
        }
    )


def _copy_report(*, passed: bool) -> dict:
    return {
        "planning": {
            "lossless_candidate": True,
            "content_capabilities": ["Outline", "RichText", "Table"],
        },
        "id_map": {SOURCE_ID: TARGET_ID},
        "verified": passed,
        "lossless": passed,
        "copy_contract_satisfied": passed,
        "issues": [],
        "skipped_content": [],
        "page_results": [],
    }


def _manifest(instance_id: str = INSTANCE_ID) -> dict:
    source = _frozen_source_structure()
    destination = _destination_structure()
    return {
        "notebook": {"id": "source-notebook", "name": "Source"},
        "notebooks": {
            "source": {"id": "source-notebook", "name": "Source"},
            "destination": {
                "id": "destination-notebook",
                "name": "Destination",
            },
        },
        "structure": {**source, **destination},
        "fixture_cache": {
            "template_instance_id": instance_id,
            "template_state": "ready",
            "mutation_eligible": True,
            "move_source_deletion_allowed": True,
            "opened_template": False,
            "interactive_live_validation": {
                "passed": True,
                "revision_markers": _revision(),
            },
        },
    }


def _args(instance_id: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        template_instance_id=instance_id,
        interactive_timeout=60,
        notebook_name="Source",
        keep_worksite=False,
        run_identity=argparse.Namespace(safe_timestamp="20260819T120000Z"),
    )


def test_bootstrap_rebinds_whole_page_and_freezes_both_notebook_roles(
    monkeypatch, tmp_path
) -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-move-page")
    run_dir = tmp_path / "run-whole-page"
    source_structure = _initial_source_structure()
    destination_structure = _destination_structure()
    answers = iter(
        (
            f"CONFIRM {run_dir.name} MovePage",
            f"ACCEPT {run_dir.name} MovePage",
        )
    )

    async def fake_input(_prompt: str, _timeout: int) -> str:
        return next(answers)

    async def fake_snapshot(_client, notebook_id: str, **kwargs) -> dict:
        assert kwargs == {"expose_revision_marker_values": True}
        if notebook_id == "source-notebook":
            return _source_snapshot()
        return _destination_snapshot()

    monkeypatch.setattr(interactive_bootstrap, "_bounded_input", fake_input)
    monkeypatch.setattr(interactive_bootstrap, "capture_snapshot", fake_snapshot)
    manifest = {
        "notebook": {"id": "source-notebook", "name": "Source"},
        "notebooks": {
            "source": {"id": "source-notebook", "name": "Source"},
            "destination": {
                "id": "destination-notebook",
                "name": "Destination",
            },
        },
        "notebook_paths": {
            "source": str(run_dir / "source"),
            "destination": str(run_dir / "destination"),
        },
        "structure": {**source_structure, **destination_structure},
        "role_structures": {
            "source": source_structure,
            "destination": destination_structure,
        },
        "fixture_validation": {
            "status": "passed",
            "role_checks": {"source": [], "destination": []},
            "bundle_checks": [],
        },
        "disposable_targets": {
            "source_notebook_path": str(run_dir / "source"),
            "destination_notebook_path": str(run_dir / "destination"),
        },
    }

    result = asyncio.run(
        interactive_bootstrap.run_interactive_bootstrap_phase(
            scenario,
            argparse.Namespace(interactive_timeout=60),
            RuntimeOptions(run_dir, 1_800, False, False),
            manifest,
            client=object(),
            fixture_result={"validation": {"role_checks": {}}},
        )
    )

    assert result["template_state"] == "ready"
    assert result["template_instance"]["mutation_eligible"] is True
    assert result["template_instance"]["move_source_deletion_allowed"] is True
    assert manifest["structure"]["source_canvas_page"]["id"] == SOURCE_ID
    assert set(manifest["role_structures"]) == {"source", "destination"}
    checkpoint = read_json(run_dir / "checkpoint.json")
    assert checkpoint["checkpoint_manifest_key"] == "source_canvas_section"
    assert checkpoint["checkpoint_target_id"] == "intake-section"
    detection = read_json(run_dir / "interactive-detection.json")
    assert detection["revision_markers"]["marker_count"] == 3
    assert [
        marker["value"] for marker in detection["revision_markers"]["markers"]
    ] == ["Alice", "AA", "Bob"]
    assert detection["author_metadata_exposed"] is True
    assert detection["sensitive_evidence"] is True
    assert detection["content_exposed"] is False


def test_revision_marker_digest_participates_in_authored_instance_identity() -> None:
    recipe = SCENARIO_REGISTRY.get("interactive-move-page").fixture_recipe

    assert recipe.freeze_authored_instance(_observation("first")).template_instance_id != (
        recipe.freeze_authored_instance(_observation("second")).template_instance_id
    )


def test_intake_fails_closed_on_multiple_pages_or_missing_revision_markers() -> None:
    recipe = SCENARIO_REGISTRY.get("interactive-move-page").fixture_recipe

    multiple = _observation(scaffold_build=True)
    multiple.roles["source"].snapshot["items"].append(
        {
            "id": "second-imported-page",
            "resource_type": "page",
            "section_id": "intake-section",
            "title": "Ambiguous",
            "page_level": 1,
        }
    )
    with pytest.raises(InvariantFailure, match="exactly one complete Page"):
        recipe.freeze_authored_structures(multiple)

    missing_revision = _observation(scaffold_build=True)
    missing_revision.roles["source"].snapshot[
        "page_revision_marker_projections"
    ][SOURCE_ID]["marker_count"] = 0
    with pytest.raises(InvariantFailure, match="Revision-marker"):
        recipe.freeze_authored_structures(missing_revision)


def test_static_contract_grants_one_strict_cross_notebook_move() -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-move-page")

    assert scenario.included_in_all is False
    assert "move_page" in scenario.spec.tool_allowlist
    assert scenario.spec.policy.deletes_enabled is True
    assert scenario.spec.execution_contract["single_public_move_call"] is True
    assert scenario.spec.execution_contract["verified_copy_before_delete"] is True
    assert scenario.spec.execution_contract["move_source_deletion_allowed"] is True
    assert scenario.spec.execution_contract["revision_marker_values_exposed"] is True
    assert scenario.spec.execution_contract["author_metadata_exposed"] is True
    assert scenario.spec.execution_contract["sensitive_evidence"] is True
    assert tuple(role.role for role in scenario.fixture_recipe.cache_identity.notebook_roles) == (
        "destination",
        "source",
    )
    assert scenario.fixture_recipe.authored_identity_rebind_keys == {
        "source_canvas_page"
    }


def test_fixture_runtime_opts_only_whole_page_recipe_into_raw_marker_values(
    monkeypatch,
) -> None:
    recipe = SCENARIO_REGISTRY.get("interactive-move-page").fixture_recipe
    calls: list[dict] = []

    async def fake_snapshot(_client, notebook_id: str, **kwargs) -> dict:
        calls.append({"notebook_id": notebook_id, **kwargs})
        return {"notebook_id": notebook_id}

    monkeypatch.setattr(fixture_runtime, "capture_snapshot", fake_snapshot)
    result = asyncio.run(
        fixture_runtime._capture_snapshot_with_observer(
            object(),
            "source-notebook",
            None,
            recipe=recipe,
        )
    )

    assert result == {"notebook_id": "source-notebook"}
    assert calls == [
        {
            "notebook_id": "source-notebook",
            "expose_revision_marker_values": True,
        }
    ]


def test_scenario_calls_move_page_once_and_records_revision_markers(
    monkeypatch, tmp_path
) -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-move-page")
    called = 0
    moved = False

    class Client:
        async def call_tool(self, name: str, arguments: dict) -> dict:
            nonlocal called, moved
            assert name == "move_page"
            assert arguments["page_id"] == SOURCE_ID
            assert arguments["destination_section_id"] == "destination-section"
            assert arguments["include_subpages"] is False
            assert "destination_title" not in arguments
            called += 1
            moved = True
            return {
                "outcome": "moved",
                "include_descendants": False,
                "source_deleted_nonpermanently": True,
                "copy_report": _copy_report(passed=True),
                "item": {"id": TARGET_ID},
            }

    async def fake_snapshot(_client, notebook_id: str, **kwargs) -> dict:
        assert kwargs == {"expose_revision_marker_values": True}
        if notebook_id == "source-notebook":
            return _source_snapshot(moved=moved)
        return _destination_snapshot(with_target=moved)

    async def accepted_input(_prompt: str, _timeout: int) -> str:
        return f"ACCEPT {tmp_path.name} MovePage MOVE"

    monkeypatch.setattr(move_page, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(move_page, "_bounded_input", accepted_input)
    result = asyncio.run(
        scenario.execute(
            _args(),
            RuntimeOptions(tmp_path, 1_800, False, False),
            _manifest(),
            client=Client(),
            fixture_result={},
        )
    )

    assert called == 1
    assert result["status"] == "passed"
    assert result["verified"] is True
    assert result["source_deleted_nonpermanently"] is True
    assert result["revision_markers_preserved"] is True
    comparison = read_json(
        tmp_path
        / "scenarios"
        / "interactive-move-page"
        / "revision-marker-comparison.json"
    )
    assert comparison["preserved"] is True
    assert comparison["source_phase"] == "before_move_copy"
    assert comparison["target_phase"] == "after_move_copy"
    assert comparison["operation_outcome"] == "moved"
    assert comparison["matched_marker_count"] == 3
    assert [item["source"]["value"] for item in comparison["marker_comparisons"]] == [
        "Alice",
        "AA",
        "Bob",
    ]
    assert [item["target"]["value"] for item in comparison["marker_comparisons"]] == [
        "Alice",
        "AA",
        "Bob",
    ]
    assert comparison["author_metadata_exposed"] is True
    assert comparison["sensitive_evidence"] is True
    assert comparison["content_exposed"] is False


def test_scenario_records_nonblocking_revision_marker_change(monkeypatch, tmp_path) -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-move-page")
    moved = False

    class Client:
        async def call_tool(self, name: str, arguments: dict) -> dict:
            nonlocal moved
            assert name == "move_page"
            moved = True
            return {
                "outcome": "moved",
                "include_descendants": False,
                "source_deleted_nonpermanently": True,
                "copy_report": _copy_report(passed=True),
                "item": {"id": TARGET_ID},
            }

    async def fake_snapshot(_client, notebook_id: str, **kwargs) -> dict:
        assert kwargs == {"expose_revision_marker_values": True}
        if notebook_id == "source-notebook":
            return _source_snapshot(moved=moved)
        return _destination_snapshot(
            with_target=moved,
            revision_sha256="changed-revision-hash",
            last_modified_by="Carol",
        )

    async def accepted_input(_prompt: str, _timeout: int) -> str:
        return f"ACCEPT {tmp_path.name} MovePage MOVE"

    monkeypatch.setattr(move_page, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(move_page, "_bounded_input", accepted_input)
    result = asyncio.run(
        scenario.execute(
            _args(),
            RuntimeOptions(tmp_path, 1_800, False, False),
            _manifest(),
            client=Client(),
            fixture_result={},
        )
    )

    assert result["status"] == "passed"
    assert result["revision_marker_comparison"] == "diagnostic_only"
    assert result["revision_markers_preserved"] is False
    comparison = read_json(
        tmp_path
        / "scenarios"
        / "interactive-move-page"
        / "revision-marker-comparison.json"
    )
    assert comparison["preserved"] is False
    assert comparison["acceptance"] == "diagnostic_only"
    changed = comparison["marker_comparisons"][2]
    assert changed["source"]["value"] == "Bob"
    assert changed["target"]["value"] == "Carol"
    assert changed["checks"]["value"] is False
    assert changed["matched"] is False


def test_lossless_failure_preserves_source_and_skips_human_verdict(
    monkeypatch, tmp_path
) -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-move-page")
    attempted = False

    class Client:
        async def call_tool(self, name: str, arguments: dict) -> dict:
            nonlocal attempted
            assert name == "move_page"
            attempted = True
            details = {
                "partial": True,
                "complete": False,
                "failed_step": "verify_copy",
                "outcome": "copy_only",
                "source_deleted": False,
                "created_ids": [TARGET_ID],
                "copy_report": _copy_report(passed=False),
                "destination": {"id": TARGET_ID},
            }
            raise ClientFailure(
                "lossless failed",
                envelope={
                    "ok": False,
                    "error": {
                        "code": "partial_failure",
                        "message": "lossless failed",
                        "details": details,
                    },
                },
            )

    async def fake_snapshot(_client, notebook_id: str, **kwargs) -> dict:
        assert kwargs == {"expose_revision_marker_values": True}
        if notebook_id == "source-notebook":
            return _source_snapshot()
        return _destination_snapshot(with_target=attempted)

    async def forbidden_input(_prompt: str, _timeout: int) -> str:
        raise AssertionError("lossless failure must not request a human verdict")

    monkeypatch.setattr(move_page, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(move_page, "_bounded_input", forbidden_input)
    with pytest.raises(ClientFailure, match="lossless failed"):
        asyncio.run(
            scenario.execute(
                _args(),
                RuntimeOptions(tmp_path, 1_800, False, False),
                _manifest(),
                client=Client(),
                fixture_result={},
            )
        )

    diagnostic = read_json(
        tmp_path
        / "scenarios"
        / "interactive-move-page"
        / "lossless-diagnostic.json"
    )
    assert diagnostic["source_active_after_failure"] is True
    assert diagnostic["target_active_in_destination"] is True
    assert diagnostic["lossless"] is False
    comparison = read_json(
        tmp_path
        / "scenarios"
        / "interactive-move-page"
        / "revision-marker-comparison.json"
    )
    assert comparison["operation_outcome"] == "copy_only"
    assert comparison["comparison_complete"] is True
    assert comparison["preserved"] is True
    assert [item["source"]["value"] for item in comparison["marker_comparisons"]] == [
        "Alice",
        "AA",
        "Bob",
    ]
