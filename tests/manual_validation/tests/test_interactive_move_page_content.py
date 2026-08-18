"""Pure contracts for the representative-content interactive Page Move scaffold."""

from __future__ import annotations

import argparse
import asyncio

import pytest

from tests.manual_validation.mcp_stdio_client import ClientFailure
from tests.manual_validation.runtime import InvariantFailure, RuntimeOptions
from tests.manual_validation.scenarios import interactive_move_page_content as move_content
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.fixture_recipes.recipe_base import (
    FixtureBundleObservation,
    FixtureRoleObservation,
)
from tests.manual_validation.scenarios.common.fixture_models import FixtureBuildResult
from tests.manual_validation.test_utils import read_json


SOURCE_ID = "source-page"
TARGET_ID = "target-page"


def _structures() -> tuple[dict, dict]:
    source = {
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
            "id": "source-section",
            "resource_type": "section",
            "parent_id": "source-notebook",
        },
        "source_canvas_page": {
            "id": SOURCE_ID,
            "resource_type": "page",
            "section_id": "source-section",
            "title": "01-Representative-Page",
            "page_level": 1,
        },
    }
    destination = {
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
    return source, destination


def _source_snapshot(*, moved: bool = False, unknown: bool = False) -> dict:
    source, _destination = _structures()
    items = list(source.values())
    if moved:
        items = [item for item in items if item["id"] != SOURCE_ID]
    capabilities = ["Outline", "RichText", "Table"]
    if unknown:
        capabilities.append("UnknownWidget")
    return {
        "notebook_id": "source-notebook",
        "items": items,
        "page_hashes": {
            "instructions-page": "instructions-hash",
            **({} if moved else {SOURCE_ID: "source-hash"}),
        },
        "page_body_hashes": {
            "instructions-page": "instructions-body-hash",
            **({} if moved else {SOURCE_ID: "source-body-hash"}),
        },
        "page_objects": {
            "instructions-page": [{"kind": "Outline"}],
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
            **(
                {}
                if moved
                else {
                    SOURCE_ID: {
                        "capabilities": capabilities,
                        "unknown_nodes": (
                            ["UnknownWidget"] if unknown else []
                        ),
                        "unsupported_page_roots": [],
                        "complete": not unknown,
                    }
                }
            ),
        },
    }


def _destination_snapshot(*, with_target: bool = False) -> dict:
    _source, destination = _structures()
    items = list(destination.values())
    if with_target:
        items.append(
            {
                "id": TARGET_ID,
                "resource_type": "page",
                "section_id": "destination-section",
                "title": "01-Representative-Moved",
                "page_level": 1,
            }
        )
    return {
        "notebook_id": "destination-notebook",
        "items": items,
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
        "page_capability_projections": {},
    }


def _observation(*, unknown: bool = False) -> FixtureBundleObservation:
    source, destination = _structures()
    return FixtureBundleObservation(
        roles={
            "source": FixtureRoleObservation(
                role="source",
                args=argparse.Namespace(),
                notebook={"id": "source-notebook"},
                notebook_path="C:/working/source",
                snapshot=_source_snapshot(unknown=unknown),
                build=FixtureBuildResult(source, {}),
            ),
            "destination": FixtureRoleObservation(
                role="destination",
                args=argparse.Namespace(),
                notebook={"id": "destination-notebook"},
                notebook_path="C:/working/destination",
                snapshot=_destination_snapshot(),
                build=FixtureBuildResult(destination, {}),
            ),
        }
    )


def _identity_observation(
    prefix: str,
    *,
    title: str = "Frozen representative title",
    body_hash: str = "representative-body-hash",
) -> FixtureBundleObservation:
    """Construct semantically equal bundles with intentionally different live IDs."""

    source, destination = _structures()

    def remap_structure(structure: dict) -> dict:
        ids = {
            str(value["id"]): f"{prefix}-{value['id']}"
            for value in structure.values()
        }
        remapped = {}
        for key, value in structure.items():
            remapped_value = dict(value)
            for field in ("id", "parent_id", "section_id", "parent_page_id"):
                if field in remapped_value and remapped_value[field] is not None:
                    remapped_value[field] = ids.get(
                        str(remapped_value[field]),
                        f"{prefix}-{remapped_value[field]}",
                    )
            remapped[key] = remapped_value
        return remapped

    source = remap_structure(source)
    destination = remap_structure(destination)
    source_page = source["source_canvas_page"]
    source_page["title"] = title
    source_page["path"] = f"{prefix}/01-Move-Source/{title}"
    source_page_id = str(source_page["id"])
    return FixtureBundleObservation(
        roles={
            "source": FixtureRoleObservation(
                role="source",
                args=argparse.Namespace(),
                notebook={"id": f"{prefix}-source-notebook"},
                notebook_path=f"C:/{prefix}/source",
                snapshot={
                    "notebook_id": f"{prefix}-source-notebook",
                    "items": list(source.values()),
                    "page_hashes": {source_page_id: f"{prefix}-volatile-page-hash"},
                    "page_body_hashes": {source_page_id: body_hash},
                    "page_objects": {
                        source_page_id: [
                            {"id": f"{prefix}-outline", "kind": "Outline"},
                            {"id": f"{prefix}-table", "kind": "Table"},
                        ]
                    },
                    "page_capability_projections": {
                        source_page_id: {
                            "capabilities": ["Outline", "RichText", "Table"],
                            "unknown_nodes": [],
                            "unsupported_page_roots": [],
                            "complete": True,
                        }
                    },
                },
                build=FixtureBuildResult(source, {}),
            ),
            "destination": FixtureRoleObservation(
                role="destination",
                args=argparse.Namespace(),
                notebook={"id": f"{prefix}-destination-notebook"},
                notebook_path=f"C:/{prefix}/destination",
                snapshot={"notebook_id": f"{prefix}-destination-notebook", "items": list(destination.values())},
                build=FixtureBuildResult(destination, {}),
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
        "page_results": [
            {
                "source_page_id": SOURCE_ID,
                "target_page_id": TARGET_ID,
                "lossless": passed,
                "normalizations": {},
                "equivalence": {
                    "verification_tier": "semantic_content_v1",
                    "acceptance_checks": [
                        "binary_sha256",
                        "semantic_content",
                        "semantic_projection_complete",
                    ],
                    "checks": {
                        "binary_sha256": True,
                        "semantic_content": passed,
                        "semantic_projection_complete": True,
                    },
                    "equivalent": passed,
                    "semantic_content_comparison": {
                        "source_complete": True,
                        "target_complete": True,
                        "checks": {"title": True, "content": passed},
                        "passed": passed,
                    },
                },
            }
        ],
    }


def _manifest(instance_id: str) -> dict:
    source, destination = _structures()
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
            "interactive_live_validation": {"passed": True},
        },
    }


def _args(instance_id: str) -> argparse.Namespace:
    return argparse.Namespace(
        template_instance_id=instance_id,
        interactive_timeout=60,
        notebook_name="Source",
        keep_worksite=False,
        run_identity=argparse.Namespace(safe_timestamp="20260818T120000Z"),
    )


def test_recipe_pair_shares_exact_two_role_cache_identity_and_freezes_ready() -> None:
    bootstrap = SCENARIO_REGISTRY.get(
        "bootstrap-move-page-content-fixture"
    ).fixture_recipe
    consumer = SCENARIO_REGISTRY.get("interactive-move-page-content").fixture_recipe
    assert bootstrap is not consumer
    assert bootstrap.cache_fingerprint == consumer.cache_fingerprint
    assert tuple(
        role.role for role in bootstrap.cache_identity.notebook_roles
    ) == ("destination", "source")
    report = bootstrap.authored_content_report(_observation())
    assert report["passed"] is True
    assert report["representative_capabilities"] == ["Table"]
    frozen = bootstrap.freeze_authored_instance(_observation())
    assert frozen.state == "ready"
    assert frozen.move_source_deletion_allowed is True

    evidence_only = bootstrap.freeze_authored_instance(_observation(unknown=True))
    assert evidence_only.state == "evidence_only"
    assert evidence_only.move_source_deletion_allowed is False
    assert "UnknownWidget" in evidence_only.unknown_capabilities


def test_representative_move_instance_identity_ignores_materialized_ids_and_paths() -> None:
    recipe = SCENARIO_REGISTRY.get(
        "bootstrap-move-page-content-fixture"
    ).fixture_recipe
    authored = recipe.freeze_authored_instance(_identity_observation("template"))
    materialized = recipe.freeze_authored_instance(_identity_observation("working"))
    renamed = recipe.freeze_authored_instance(
        _identity_observation("working", title="Different representative title")
    )
    changed_content = recipe.freeze_authored_instance(
        _identity_observation("working", body_hash="different-body-hash")
    )

    assert authored.template_instance_id == materialized.template_instance_id
    assert authored.projection_digest == materialized.projection_digest
    assert authored.template_instance_id != renamed.template_instance_id
    assert authored.template_instance_id != changed_content.template_instance_id


def test_representative_move_instance_identity_requires_stable_body_hash() -> None:
    recipe = SCENARIO_REGISTRY.get(
        "bootstrap-move-page-content-fixture"
    ).fixture_recipe
    observation = _identity_observation("template")
    observation.roles["source"].snapshot.pop("page_body_hashes")

    with pytest.raises(InvariantFailure, match="stable page body hash"):
        recipe.freeze_authored_instance(observation)


def test_interactive_move_lossless_failure_is_one_call_and_preserves_source(
    monkeypatch, tmp_path
) -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-move-page-content")
    instance_id = "authored-" + "a" * 24
    called = 0
    move_attempted = False

    class Client:
        async def call_tool(self, name: str, arguments: dict) -> dict:
            nonlocal called, move_attempted
            assert name == "move_page"
            called += 1
            move_attempted = True
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
                    "execution": {"attempts": 1, "replayed": False},
                },
            )

    async def fake_snapshot(_client, notebook_id: str) -> dict:
        if notebook_id == "source-notebook":
            return _source_snapshot()
        return _destination_snapshot(with_target=move_attempted)

    async def forbidden_input(_prompt: str, _timeout: int) -> str:
        raise AssertionError("lossless failure must not request an acceptance verdict")

    monkeypatch.setattr(move_content, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(move_content, "_bounded_input", forbidden_input)
    with pytest.raises(ClientFailure, match="lossless failed"):
        asyncio.run(
            scenario.execute(
                _args(instance_id),
                RuntimeOptions(tmp_path, 1_800, False, False),
                _manifest(instance_id),
                client=Client(),
                fixture_result={},
            )
        )

    assert called == 1
    diagnostic = read_json(
        tmp_path / "scenarios" / "interactive-move-page-content" / "lossless-diagnostic.json"
    )
    assert diagnostic["source_active_after_failure"] is True
    assert diagnostic["target_active_in_destination"] is True
    assert diagnostic["lossless"] is False
    assert diagnostic["copy_contract_satisfied"] is False
    assert diagnostic["page_results"][0]["verification_tier"] == (
        "semantic_content_v1"
    )
    assert diagnostic["source_to_transformed_projection"] == (
        "not_exposed_by_current_copy_report"
    )
    assert diagnostic["content_exposed"] is False


def test_interactive_move_accepts_only_after_machine_lossless_and_nonpermanent_delete(
    monkeypatch, tmp_path
) -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-move-page-content")
    instance_id = "authored-" + "b" * 24
    called = 0
    moved = False

    class Client:
        async def call_tool(self, name: str, arguments: dict) -> dict:
            nonlocal called, moved
            assert name == "move_page"
            assert arguments["include_subpages"] is False
            called += 1
            moved = True
            return {
                "outcome": "moved",
                "include_descendants": False,
                "source_deleted_nonpermanently": True,
                "copy_report": _copy_report(passed=True),
                "item": {"id": TARGET_ID},
            }

    async def fake_snapshot(_client, notebook_id: str) -> dict:
        if notebook_id == "source-notebook":
            return _source_snapshot(moved=moved)
        return _destination_snapshot(with_target=moved)

    async def accepted_input(_prompt: str, _timeout: int) -> str:
        return f"ACCEPT {tmp_path.name} MovePageContent"

    monkeypatch.setattr(move_content, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(move_content, "_bounded_input", accepted_input)
    result = asyncio.run(
        scenario.execute(
            _args(instance_id),
            RuntimeOptions(tmp_path, 1_800, False, False),
            _manifest(instance_id),
            client=Client(),
            fixture_result={},
        )
    )

    assert called == 1
    assert result["status"] == "passed"
    assert result["verified"] is True
    assert result["lossless"] is True
    assert result["copy_contract_satisfied"] is True
    assert result["source_deleted_nonpermanently"] is True
    acceptance = read_json(
        tmp_path / "scenarios" / "interactive-move-page-content" / "human-acceptance.json"
    )
    assert acceptance["machine_lossless_gate_passed"] is True
    assert acceptance["human_verdict"] == "accepted"
