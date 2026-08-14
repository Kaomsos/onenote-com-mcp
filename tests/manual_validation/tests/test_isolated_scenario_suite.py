"""Pure contracts for the human-gated, scenario-scoped validation runner."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re

import pytest

from tests.manual_validation import runtime, test_utils
from tests.manual_validation.mcp_stdio_client import ClientFailure
from tests.manual_validation.runner import build_parser, main
from tests.manual_validation.runtime import RuntimeOptions
from tests.manual_validation.scenarios.common import orchestrator as validation
from tests.manual_validation.scenarios.common import fixture_runtime
from tests.manual_validation.scenarios.common.fixture_models import FixtureBuildResult, FixtureValidationContext
from tests.manual_validation.scenarios.base import Scenario
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.common.specs import SCENARIO_SPECS
from tests.manual_validation.scenarios.fixture_recipes import delete as delete_fixture
from tests.manual_validation.scenarios.fixture_recipes.copy_page import DESCRIPTION as COPY_PAGE_DESCRIPTION
from tests.manual_validation.scenarios.fixture_recipes.reorder_page import DESCRIPTION as REORDER_PAGE_DESCRIPTION
from tests.manual_validation.scenarios.fixture_recipes.reorder_section import DESCRIPTION as REORDER_SECTION_DESCRIPTION
from tests.manual_validation.scenarios.fixture_recipes.reorder_section_group import DESCRIPTION as REORDER_SECTION_GROUP_DESCRIPTION
from tests.manual_validation.scenarios.fixture_recipes.reparent_section import DESCRIPTION as REPARENT_SECTION_DESCRIPTION


SCENARIOS = validation.PUBLIC_SCENARIOS


@pytest.fixture(autouse=True)
def _running_onenote_gui(monkeypatch):
    monkeypatch.setattr(validation, "require_onenote_desktop", lambda: None)


def _validate_fixture_snapshot(scenario, snapshot, structure, content_fixture):
    evidence = {}
    if content_fixture is not None:
        evidence[
            "reparent_page_fixture" if scenario == "reparent-page" else "copy_fixture"
        ] = content_fixture
    return list(
        SCENARIO_REGISTRY.get(scenario).fixture_recipe.validate(
            FixtureValidationContext(
                args=argparse.Namespace(scenario=scenario), snapshot=snapshot
            ),
            FixtureBuildResult(structure, evidence),
        )
    )


def test_public_scenarios_are_class_managed_and_spec_backed() -> None:
    assert SCENARIO_REGISTRY.public_names == SCENARIOS
    assert all(isinstance(scenario, Scenario) for scenario in SCENARIO_REGISTRY.values())
    assert [scenario.spec.name for scenario in SCENARIO_REGISTRY.values()] == list(SCENARIOS)


def test_every_copy_scenario_has_a_runtime_executor() -> None:
    for name in ("copy-page", "copy-section", "copy-section-group", "copy-notebook"):
        scenario = SCENARIO_REGISTRY.get(name)
        assert callable(getattr(scenario, "execute_copy", None)), name


def _args(run_dir: Path, scenario: str, *, keep: bool = False) -> argparse.Namespace:
    values = {
        "command": scenario,
        "scenario": scenario,
        "notebook_name": "__ISOLATED__",
        "run_dir": run_dir,
        "timeout": 1_800,
        "dry_run": False,
        "json_output": False,
        "keep_notebook": keep,
    }
    if scenario == "rename":
        values.update(target="content_section", new_name=None)
    if scenario == "reorder-page":
        values["page_level"] = 2
    return argparse.Namespace(**values)


def _manifest(run_dir: Path, name: str = "__ISOLATED__") -> dict:
    source = (run_dir / "notebooks" / name).resolve()
    return {
        "schema_version": 1,
        "notebook": {"id": "notebook-id", "name": name},
        "structure": {
            "group_a": {"id": "group-a"},
            "group_b": {"id": "group-b"},
            "content_section": {"id": "content-section"},
            "parent_page": {"id": "parent-page"},
            "sibling_page": {"id": "sibling-page"},
            "disposable_group": {"id": "disposable-group"},
            "disposable_page": {"id": "disposable-page"},
        },
        "disposable_targets": {"source_notebook_path": str(source)},
    }


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_each_dry_run_declares_one_process_and_scenario_fixture(
    scenario, capsys, tmp_path
) -> None:
    run_dir = tmp_path / scenario
    assert main([scenario, "--run-dir", str(run_dir), "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == scenario
    assert payload["server_started"] is False
    assert payload["agent_execution_prohibited"] is True
    consumer_cache_required = SCENARIO_REGISTRY.get(
        scenario
    ).fixture_recipe.consumer_scenario
    assert payload["expected_mcp_process_starts"] == (
        0 if consumer_cache_required else 1
    )
    assert payload["fixture_profile"]["name"] == SCENARIO_SPECS[scenario].fixture.name
    assert payload["scenario_spec"]["tool_allowlist"] == sorted(
        SCENARIO_SPECS[scenario].tool_allowlist
    )
    if consumer_cache_required:
        assert [step["step"] for step in payload["ordered_steps"]] == [
            "preflight-cache-required"
        ]
        assert payload["cache"]["decision"] == "rejected_missing_use_cache"
        assert payload["ordered_steps"][0]["allowed_operations"] == []
        assert not run_dir.exists()
        return
    multi_role = len(
        SCENARIO_REGISTRY.get(scenario).fixture_recipe.cache_identity.notebook_roles
    ) > 1
    expected_steps = [
        "create-notebook-bundle" if multi_role else "create-source-notebook",
        scenario,
    ]
    if SCENARIO_REGISTRY.get(
        scenario
    ).fixture_recipe.requires_persistence_checkpoint:
        expected_steps[1:2] = [
            "prepare-fixture",
            "fixture-persistence-checkpoint",
            scenario,
        ]
    if scenario.startswith("bootstrap-"):
        expected_steps.extend(
            [
                "interactive-checkpoint",
                (
                    "record-evidence-only-and-close"
                    if getattr(
                        SCENARIO_REGISTRY.get(scenario).fixture_recipe,
                        "representation_discovery_only",
                        False,
                    )
                    else "close-stage-publish-materialize-live-validate"
                ),
            ]
        )
    expected_steps.extend(
        ["report", "close-notebook-bundle" if multi_role else "close-source-notebook"]
    )
    assert [step["step"] for step in payload["ordered_steps"]] == expected_steps
    assert payload["ordered_steps"][0]["allowed_operations"] == [
        "create_fresh_notebook"
    ]
    assert payload["ordered_steps"][-1]["allowed_operations"] == [
        "get_exact_notebook",
        "close_exact_notebook",
    ]
    assert payload["filesystem_cleanup"]["enabled"] is False
    assert not run_dir.exists()


def test_fixture_profiles_are_scenario_specific() -> None:
    names = {name: spec.fixture.name for name, spec in SCENARIO_SPECS.items()}
    assert names["create"] == "minimal-create-target"
    assert names["rename"] == "rename-target"
    assert names["reparent-section"] == "section-reparent"
    assert names["reparent-page"] == "typed-page-reparent"
    assert names["reparent-section-group"] == "typed-section-group-reparent"
    assert names["copy-page"] == "rich-page-copy"
    duplicates = {
        profile: {scenario for scenario, value in names.items() if value == profile}
        for profile in set(names.values())
        if list(names.values()).count(profile) > 1
    }
    assert duplicates == {
        "user-authored-zone": {
            "bootstrap-user-authored-fixture",
            "user-authored-fixture-consumer",
        },
        "interactive-insertedfile": {
            "bootstrap-inserted-file-fixture",
            "interactive-copy-inserted-file",
        },
        "interactive-inkdrawing": {
            "bootstrap-ink-drawing-fixture",
            "interactive-copy-ink-drawing",
        },
        "interactive-mediafile": {
            "bootstrap-media-file-fixture",
            "interactive-copy-media-file",
        },
        "interactive-uishape": {
            "bootstrap-shape-fixture",
            "interactive-copy-ui-shape",
        },
        "interactive-inline-equation": {
            "bootstrap-inline-equation-fixture",
            "interactive-copy-inline-equation",
        },
    }
    assert "create_notebook" not in SCENARIO_SPECS["create"].tool_allowlist
    assert "reparent_section" not in SCENARIO_SPECS["rename"].tool_allowlist
    assert "delete_section_group" not in SCENARIO_SPECS["reparent-section"].tool_allowlist


def test_every_fixture_creation_tool_is_in_its_scenario_allowlist() -> None:
    for name, spec in SCENARIO_SPECS.items():
        missing = spec.fixture.creation_tools - spec.tool_allowlist
        recipe = SCENARIO_REGISTRY.get(name).fixture_recipe
        if recipe.consumer_scenario:
            if spec.execution_contract.get("interactive_copy_evidence"):
                assert spec.policy.writes_enabled is True
                assert spec.policy.experimental_copy_enabled is True
                assert spec.policy.deletes_enabled is False
                assert {"plan_copy", "copy_page"} <= spec.tool_allowlist
            else:
                assert spec.policy.writes_enabled is False
                assert spec.policy.experimental_copy_enabled is False
            runtime_creation_tools = (
                {"create_section"}
                if spec.execution_contract.get("same_and_cross_section")
                else set()
            )
            assert missing == spec.fixture.creation_tools - runtime_creation_tools
            continue
        assert not missing, f"{name} fixture tools missing from allowlist: {sorted(missing)}"


def test_call_metrics_count_only_run_scoped_audit_lines(tmp_path) -> None:
    scenario = tmp_path / "scenario-mcp"
    scenario.mkdir()
    (scenario / "bridge-calls.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    (scenario / "calls.jsonl").write_text("{}\n{}\n{}\n", encoding="utf-8")
    (tmp_path / "lifecycle-bridge-calls.jsonl").write_text("{}\n", encoding="utf-8")
    metrics: dict = {}

    validation._refresh_call_metrics(metrics, tmp_path)

    assert metrics["observed_bridge_calls"] == {
        "scenario_mcp": 2,
        "lifecycle_wrapper": 1,
        "total": 3,
    }
    assert metrics["observed_mcp_tool_calls"] == 3


def test_fixture_validator_proves_page_tree_topology() -> None:
    structure = {
        "description_section": {"id": "description-section"},
        "description_page": {"id": "description-page"},
        "reorder_section": {"id": "section"},
        "parent_page": {"id": "parent"},
        "child_page": {"id": "child"},
        "sibling_page": {"id": "sibling"},
    }
    items = [
        {"id": "description-section", "resource_type": "section"},
        {
            "id": "description-page",
            "resource_type": "page",
            "title": "00-Reorder-Description",
            "section_id": "description-section",
        },
        {"id": "section", "resource_type": "section"},
        {
            "id": "parent",
            "resource_type": "page",
            "title": "01-Parent",
            "section_id": "section",
            "page_level": 1,
            "parent_page_id": None,
        },
        {
            "id": "child",
            "resource_type": "page",
            "title": "02-Child",
            "section_id": "section",
            "page_level": 2,
            "parent_page_id": "parent",
        },
        {
            "id": "sibling",
            "resource_type": "page",
            "title": "03-Sibling",
            "section_id": "section",
            "page_level": 1,
            "parent_page_id": None,
        },
    ]

    checks = _validate_fixture_snapshot("reorder-page", {"items": items}, structure, None)
    assert "Page levels and derived parent relationships match the profile" in checks
    assert "all scenario Pages use stable 00/01/02/03 title prefixes" in checks

    items[4]["parent_page_id"] = "wrong"
    with pytest.raises(runtime.InvariantFailure, match="topology"):
        _validate_fixture_snapshot("reorder-page", {"items": items}, structure, None)
    items[4]["parent_page_id"] = "parent"
    items[5]["title"] = "Sibling"
    with pytest.raises(runtime.InvariantFailure, match="numbering"):
        _validate_fixture_snapshot("reorder-page", {"items": items}, structure, None)


def test_reorder_page_fixture_description_makes_order_visually_explicit() -> None:
    spec = SCENARIO_SPECS["reorder-page"]

    assert {"description_section", "description_page"} <= set(spec.fixture.manifest_keys)
    assert "get_page_text" in spec.tool_allowlist
    assert "00-Reorder-Description" in spec.fixture.expected_structure[0]
    assert "01-Parent" in spec.fixture.expected_structure[1]
    assert "02-Child" in spec.fixture.expected_structure[1]
    assert "03-Sibling" in spec.fixture.expected_structure[1]
    assert "操作前（顺序 01,02,03）" in REORDER_PAGE_DESCRIPTION
    assert "预期操作后（顺序 01,03,02）" in REORDER_PAGE_DESCRIPTION
    assert "默认恢复后（顺序 01,02,03）" in REORDER_PAGE_DESCRIPTION


def test_copy_page_fixture_description_covers_six_case_bundle_matrix() -> None:
    spec = SCENARIO_SPECS["copy-page"]

    assert {"description_section", "description_page"} <= set(
        spec.fixture.manifest_keys
    )
    assert "get_page_text" in spec.tool_allowlist
    assert "00-Copy-Page-Description" in spec.fixture.expected_structure[0]
    assert "01-Source-Parent" in spec.fixture.expected_structure[1]
    assert "02-Source-Child" in spec.fixture.expected_structure[2]
    assert "原始状态：" in COPY_PAGE_DESCRIPTION
    assert "同 Section、跨 Section、跨 Notebook" in COPY_PAGE_DESCRIPTION
    assert "行内公式、单行公式" in COPY_PAGE_DESCRIPTION
    assert "不带子树" in COPY_PAGE_DESCRIPTION
    assert "带子树" in COPY_PAGE_DESCRIPTION
    assert "默认运行会在自动 read-back 验证后清理六个目标" in COPY_PAGE_DESCRIPTION


def test_copy_page_fixture_validator_proves_description_and_numbered_source_tree() -> None:
    assert SCENARIO_REGISTRY.get("copy-page").fixture_recipe.recipe_version == 11
    assert tuple(
        role.role
        for role in SCENARIO_REGISTRY.get("copy-page").fixture_recipe.cache_identity.notebook_roles
    ) == ("destination", "source")
    structure = {
        "description_section": {"id": "description-section"},
        "description_page": {"id": "description-page"},
        "source_section": {"id": "source-section"},
        "parent_page": {"id": "parent-page"},
        "semantic_page": {"id": "child-page"},
        "disposable_section": {"id": "destination-section"},
        "cross_section_anchor": {"id": "cross-section-anchor"},
        "cross_section_position_anchor": {"id": "cross-section-position-anchor"},
    }
    items = [
        {
            "id": "description-section",
            "resource_type": "section",
            "name": "00-Description",
            "parent_id": "notebook",
        },
        {
            "id": "description-page",
            "resource_type": "page",
            "title": "00-Copy-Page-Description",
            "section_id": "description-section",
            "parent_id": "description-section",
            "page_level": 1,
            "parent_page_id": None,
        },
        {
            "id": "source-section",
            "resource_type": "section",
            "name": "Source",
            "parent_id": "notebook",
        },
        {
            "id": "destination-section",
            "resource_type": "section",
            "name": "Destination",
            "parent_id": "notebook",
        },
        {
            "id": "parent-page",
            "resource_type": "page",
            "title": "01-Source-Parent",
            "section_id": "source-section",
            "parent_id": "source-section",
            "page_level": 1,
            "parent_page_id": None,
        },
        {
            "id": "cross-section-anchor",
            "resource_type": "page",
            "title": "02-Source-Child",
            "section_id": "destination-section",
            "parent_id": "destination-section",
            "page_level": 1,
            "parent_page_id": None,
        },
        {
            "id": "cross-section-position-anchor",
            "resource_type": "page",
            "title": "99-Position-Anchor",
            "section_id": "destination-section",
            "parent_id": "destination-section",
            "page_level": 1,
            "parent_page_id": None,
        },
        {
            "id": "child-page",
            "resource_type": "page",
            "title": "02-Source-Child",
            "section_id": "source-section",
            "parent_id": "source-section",
            "page_level": 2,
            "parent_page_id": "parent-page",
        },
    ]
    copy_fixture = {
        "page_id": "parent-page",
        "automated_content": [
            "rich_text",
            "table",
            "image",
            "inline_equation",
            "display_equation",
            "list",
            "tag",
        ],
        "equations": {
            "mathml_roots": 2,
            "inline_equations": 1,
            "display_equations": 1,
            "namespace_declarations": 2,
            "inline_candidates_with_visible_context": 1,
            "display_candidate_text_nodes": 1,
            "display_candidates_with_visible_residual": 0,
            "display_candidates_with_known_leading_blank": 1,
        },
        "semantic_page": {
            "page_id": "child-page",
            "observed_capabilities": ["List", "Tag"],
            "observed_counts": {"List": 3, "Tag": 3, "TagDef": 1},
        },
    }

    snapshot = {
        "items": items,
        "page_hashes": {
            "child-page": "source-child-hash",
            "cross-section-anchor": "cross-section-anchor-hash",
            "cross-section-position-anchor": "cross-section-position-anchor-hash",
        },
        "page_capability_projections": {
            "parent-page": {
                "schema_version": 1,
                "capabilities": [
                    "DisplayEquation",
                    "Image",
                    "Outline",
                    "RichText",
                    "Table",
                ],
                "complete": True,
            },
            "child-page": {
                "schema_version": 1,
                "capabilities": ["List", "Outline", "RichText", "Tag"],
                "complete": True,
            },
        },
    }
    checks = _validate_fixture_snapshot("copy-page", snapshot, structure, copy_fixture)
    assert "Description Page belongs to the fixture Description Section" in checks
    assert "Description and source Pages use stable 00/01/02 prefixes" in checks
    assert (
        "semantic child live Page XML exposes List/Tag to Copy planning" in checks
    )
    assert (
        "rich parent contains one inline equation and one display equation with the known COM leading blank"
        in checks
    )
    assert "cross-Section destination contains a distinct same-title anchor" in checks
    assert (
        "cross-Section anchor body hash differs from the same-title source Child"
        in checks
    )

    stale_evidence_snapshot = {
        **snapshot,
        "page_capability_projections": {
            **snapshot["page_capability_projections"],
            "child-page": {
                "schema_version": 1,
                "capabilities": ["Outline"],
                "complete": True,
            },
        },
    }
    with pytest.raises(
        runtime.InvariantFailure,
        match="live Page XML is missing List/Tag capabilities",
    ):
        _validate_fixture_snapshot(
            "copy-page", stale_evidence_snapshot, structure, copy_fixture
        )

    items[-1]["title"] = "Source-Child"
    with pytest.raises(runtime.InvariantFailure, match="stable Description/Source numbering"):
        _validate_fixture_snapshot("copy-page", snapshot, structure, copy_fixture)


def test_reorder_section_fixture_description_covers_both_parent_types() -> None:
    spec = SCENARIO_SPECS["reorder-section"]

    assert {"description_section", "description_page"} <= set(
        spec.fixture.manifest_keys
    )
    assert "get_page_text" in spec.tool_allowlist
    assert "00-Reorder-Section-Description" in spec.fixture.expected_structure[0]
    assert "01-Root-Section-A" in spec.fixture.expected_structure[1]
    assert "01-Group-Section-A" in spec.fixture.expected_structure[2]
    assert "场景一：父级为 Notebook" in REORDER_SECTION_DESCRIPTION
    assert (
        "场景二：父级为 01-Section-Parent（SectionGroup）"
        in REORDER_SECTION_DESCRIPTION
    )
    assert (
        "操作后：00-Description, 01-Root-Section-A, 03-Root-Section-C, 02-Root-Section-B"
        in REORDER_SECTION_DESCRIPTION
    )
    assert (
        "操作后：01-Group-Section-A, 03-Group-Section-C, 02-Group-Section-B"
        in REORDER_SECTION_DESCRIPTION
    )


def test_fixture_validator_proves_numbered_section_sequences_for_both_parents() -> None:
    structure = {
        "description_section": {"id": "description-section"},
        "description_page": {"id": "description-page"},
        "section_parent_group": {"id": "section-parent"},
    }
    items = [
        {"id": "notebook", "resource_type": "notebook", "name": "Notebook"},
        {
            "id": "description-section",
            "resource_type": "section",
            "name": "00-Description",
            "parent_id": "notebook",
        },
        {
            "id": "description-page",
            "resource_type": "page",
            "title": "00-Reorder-Section-Description",
            "section_id": "description-section",
        },
        {
            "id": "section-parent",
            "resource_type": "section_group",
            "name": "01-Section-Parent",
            "parent_id": "notebook",
        },
    ]
    for prefix, parent_id, label in (
        ("root", "notebook", "Root"),
        ("group", "section-parent", "Group"),
    ):
        for index, letter in enumerate("abc", start=1):
            section_id = f"{prefix}-section-{letter}"
            page_id = f"{prefix}-page-{letter}"
            structure[f"{prefix}_section_{letter}"] = {"id": section_id}
            structure[f"{prefix}_page_{letter}"] = {"id": page_id}
            items.extend(
                [
                    {
                        "id": section_id,
                        "resource_type": "section",
                        "name": f"{index:02d}-{label}-Section-{letter.upper()}",
                        "parent_id": parent_id,
                    },
                    {
                        "id": page_id,
                        "resource_type": "page",
                        "title": f"{index:02d}-{label}-Page-{letter.upper()}",
                        "section_id": section_id,
                    },
                ]
            )

    checks = _validate_fixture_snapshot(
        "reorder-section", {"items": items}, structure, None
    )
    assert (
        "Section fixture covers both legal parent types: Notebook and SectionGroup"
        in checks
    )
    assert "both Section sibling sequences are exactly A/B/C" in checks

    next(
        item for item in items if item["id"] == "group-section-b"
    )["parent_id"] = "notebook"
    with pytest.raises(runtime.InvariantFailure, match="outside its declared parent"):
        _validate_fixture_snapshot("reorder-section", {"items": items}, structure, None)


def test_reorder_section_group_fixture_description_covers_both_parent_types() -> None:
    spec = SCENARIO_SPECS["reorder-section-group"]

    assert {"description_section", "description_page"} <= set(
        spec.fixture.manifest_keys
    )
    assert "get_page_text" in spec.tool_allowlist
    assert "00-Reorder-SectionGroup-Description" in spec.fixture.expected_structure[0]
    assert "01-Root-Group-A" in spec.fixture.expected_structure[1]
    assert "01-Nested-Group-A" in spec.fixture.expected_structure[2]
    assert (
        "场景一：父级为 Notebook"
        in REORDER_SECTION_GROUP_DESCRIPTION
    )
    assert (
        "场景二：父级为 00-Group-Parent（SectionGroup）"
        in REORDER_SECTION_GROUP_DESCRIPTION
    )
    assert (
        "操作后：00-Group-Parent, 01-Root-Group-A, 03-Root-Group-C, 02-Root-Group-B"
        in REORDER_SECTION_GROUP_DESCRIPTION
    )
    assert (
        "操作后：01-Nested-Group-A, 03-Nested-Group-C, 02-Nested-Group-B"
        in REORDER_SECTION_GROUP_DESCRIPTION
    )


def test_reparent_section_fixture_description_covers_all_parent_transitions() -> None:
    spec = SCENARIO_SPECS["reparent-section"]

    assert {"description_section", "description_page"} <= set(
        spec.fixture.manifest_keys
    )
    assert "get_page_text" in spec.tool_allowlist
    assert "create_page" in spec.tool_allowlist
    assert "00-Reparent-Section-Description" in spec.fixture.expected_structure[0]
    assert "Notebook-To-Group" in spec.fixture.expected_structure[1]
    assert "Group-To-Notebook" in spec.fixture.expected_structure[2]
    assert "Group-To-Group" in spec.fixture.expected_structure[3]
    assert (
        "场景一：Notebook 父级 → SectionGroup 父级"
        in REPARENT_SECTION_DESCRIPTION
    )
    assert (
        "场景二：SectionGroup 父级 → Notebook 父级"
        in REPARENT_SECTION_DESCRIPTION
    )
    assert (
        "场景三：SectionGroup 父级 → SectionGroup 父级"
        in REPARENT_SECTION_DESCRIPTION
    )


def test_reparent_section_fixture_validator_proves_three_parent_transitions() -> None:
    structure = {
        "description_section": {"id": "description-section"},
        "description_page": {"id": "description-page"},
        "notebook_to_group_destination": {"id": "destination-1"},
        "notebook_to_group_anchor_a": {"id": "anchor-1a"},
        "notebook_to_group_anchor_b": {"id": "anchor-1b"},
        "notebook_to_group_section": {"id": "section-1"},
        "notebook_to_group_page": {"id": "page-1"},
        "group_to_notebook_source": {"id": "source-2"},
        "group_to_notebook_section": {"id": "section-2"},
        "group_to_notebook_page": {"id": "page-2"},
        "group_to_notebook_anchor_a": {"id": "anchor-2a"},
        "group_to_notebook_anchor_b": {"id": "anchor-2b"},
        "group_to_group_source": {"id": "source-3"},
        "group_to_group_destination": {"id": "destination-3"},
        "group_to_group_anchor_a": {"id": "anchor-3a"},
        "group_to_group_anchor_b": {"id": "anchor-3b"},
        "group_to_group_section": {"id": "section-3"},
        "group_to_group_page": {"id": "page-3"},
    }
    items = [
        {"id": "notebook", "resource_type": "notebook", "name": "Notebook"},
        {
            "id": "description-section",
            "resource_type": "section",
            "name": "00-Description",
            "parent_id": "notebook",
        },
        {
            "id": "description-page",
            "resource_type": "page",
            "title": "00-Reparent-Section-Description",
            "section_id": "description-section",
        },
        {
            "id": "destination-1",
            "resource_type": "section_group",
            "name": "01-Destination-Group",
            "parent_id": "notebook",
        },
        {"id": "anchor-1a", "resource_type": "section", "name": "00-Section-Anchor-A", "parent_id": "destination-1"},
        {"id": "anchor-1b", "resource_type": "section", "name": "99-Section-Anchor-B", "parent_id": "destination-1"},
        {
            "id": "section-1",
            "resource_type": "section",
            "name": "01-Notebook-To-Group-Section",
            "parent_id": "notebook",
        },
        {
            "id": "page-1",
            "resource_type": "page",
            "title": "01-Notebook-To-Group-Page",
            "section_id": "section-1",
        },
        {
            "id": "source-2",
            "resource_type": "section_group",
            "name": "02-Source-Group",
            "parent_id": "notebook",
        },
        {"id": "anchor-2a", "resource_type": "section", "name": "00-Notebook-Section-Anchor-A", "parent_id": "notebook"},
        {"id": "anchor-2b", "resource_type": "section", "name": "99-Notebook-Section-Anchor-B", "parent_id": "notebook"},
        {
            "id": "section-2",
            "resource_type": "section",
            "name": "02-Group-To-Notebook-Section",
            "parent_id": "source-2",
        },
        {
            "id": "page-2",
            "resource_type": "page",
            "title": "02-Group-To-Notebook-Page",
            "section_id": "section-2",
        },
        {
            "id": "source-3",
            "resource_type": "section_group",
            "name": "03-Source-Group",
            "parent_id": "notebook",
        },
        {
            "id": "destination-3",
            "resource_type": "section_group",
            "name": "03-Destination-Group",
            "parent_id": "notebook",
        },
        {"id": "anchor-3a", "resource_type": "section", "name": "00-Section-Anchor-A", "parent_id": "destination-3"},
        {"id": "anchor-3b", "resource_type": "section", "name": "99-Section-Anchor-B", "parent_id": "destination-3"},
        {
            "id": "section-3",
            "resource_type": "section",
            "name": "03-Group-To-Group-Section",
            "parent_id": "source-3",
        },
        {
            "id": "page-3",
            "resource_type": "page",
            "title": "03-Group-To-Group-Page",
            "section_id": "section-3",
        },
    ]

    checks = _validate_fixture_snapshot(
        "reparent-section", {"items": items}, structure, None
    )
    assert "case 1 source is Notebook-root and destination is a root SectionGroup" in checks
    assert "case 2 source is under its root SectionGroup and destination is Notebook" in checks
    assert "case 3 source and destination are distinct root SectionGroups" in checks

    next(item for item in items if item["id"] == "section-2")["parent_id"] = "notebook"
    with pytest.raises(runtime.InvariantFailure, match="SectionGroup-to-Notebook"):
        _validate_fixture_snapshot("reparent-section", {"items": items}, structure, None)


def test_fixture_validator_proves_numbered_section_groups_for_both_parents() -> None:
    structure = {
        "description_section": {"id": "description-section"},
        "description_page": {"id": "description-page"},
        "section_group_parent": {"id": "group-parent"},
    }
    items = [
        {"id": "notebook", "resource_type": "notebook", "name": "Notebook"},
        {
            "id": "description-section",
            "resource_type": "section",
            "name": "00-Description",
            "parent_id": "notebook",
        },
        {
            "id": "description-page",
            "resource_type": "page",
            "title": "00-Reorder-SectionGroup-Description",
            "section_id": "description-section",
        },
        {
            "id": "group-parent",
            "resource_type": "section_group",
            "name": "00-Group-Parent",
            "parent_id": "notebook",
        },
    ]
    for prefix, parent_id, label in (
        ("root", "notebook", "Root"),
        ("nested", "group-parent", "Nested"),
    ):
        for index, letter in enumerate("abc", start=1):
            group_id = f"{prefix}-group-{letter}"
            section_id = f"{prefix}-section-{letter}"
            page_id = f"{prefix}-page-{letter}"
            structure[f"{prefix}_group_{letter}"] = {"id": group_id}
            structure[f"{prefix}_section_{letter}"] = {"id": section_id}
            structure[f"{prefix}_page_{letter}"] = {"id": page_id}
            items.extend(
                [
                    {
                        "id": group_id,
                        "resource_type": "section_group",
                        "name": f"{index:02d}-{label}-Group-{letter.upper()}",
                        "parent_id": parent_id,
                    },
                    {
                        "id": section_id,
                        "resource_type": "section",
                        "name": f"{index:02d}-{label}-Section-{letter.upper()}",
                        "parent_id": group_id,
                    },
                    {
                        "id": page_id,
                        "resource_type": "page",
                        "title": f"{index:02d}-{label}-Page-{letter.upper()}",
                        "section_id": section_id,
                    },
                ]
            )

    checks = _validate_fixture_snapshot(
        "reorder-section-group", {"items": items}, structure, None
    )
    assert (
        "SectionGroup fixture covers both legal parent types: Notebook and SectionGroup"
        in checks
    )
    assert "both SectionGroup sibling sequences are exactly A/B/C" in checks

    next(item for item in items if item["id"] == "nested-group-b")[
        "parent_id"
    ] = "root-group-a"
    with pytest.raises(runtime.InvariantFailure, match="Notebook and SectionGroup parents"):
        _validate_fixture_snapshot(
            "reorder-section-group", {"items": items}, structure, None
        )


def test_fixture_validator_rejects_delete_target_outside_sandbox() -> None:
    structure = {
        "delete_sandbox": {"id": "sandbox"},
        "disposable_group": {"id": "target"},
        "disposable_section": {"id": "sentinel"},
    }
    snapshot = {
        "items": [
            {"id": "sandbox", "resource_type": "section_group"},
            {"id": "target", "resource_type": "section_group", "parent_id": "other"},
            {"id": "sentinel", "resource_type": "section", "parent_id": "target"},
        ]
    }
    with pytest.raises(runtime.InvariantFailure, match="Delete-Sandbox"):
        _validate_fixture_snapshot("delete", snapshot, structure, None)


def test_fixture_validation_failure_persists_manifest_and_snapshot(monkeypatch, tmp_path) -> None:
    created = iter(
        [
            {"id": "sandbox", "name": "Delete-Sandbox"},
            {"id": "target", "name": "Disposable-Group"},
            {"id": "sentinel", "name": "Disposable-Section"},
        ]
    )

    async def fake_group(*_args, **_kwargs):
        return next(created)

    async def fake_section(*_args, **_kwargs):
        return next(created)

    async def fake_snapshot(*_args, **_kwargs):
        return {
            "items": [
                {"id": "sandbox", "resource_type": "section_group"},
                {
                    "id": "target",
                    "resource_type": "section_group",
                    "parent_id": "wrong",
                },
                {
                    "id": "sentinel",
                    "resource_type": "section",
                    "parent_id": "target",
                },
            ],
            "page_hashes": {},
        }

    monkeypatch.setattr(delete_fixture, "ensure_group", fake_group)
    monkeypatch.setattr(delete_fixture, "ensure_section", fake_section)
    monkeypatch.setattr(fixture_runtime, "capture_snapshot", fake_snapshot)
    args = argparse.Namespace(scenario="delete")
    options = RuntimeOptions(tmp_path, 180, False, False)

    with pytest.raises(runtime.InvariantFailure, match="Delete-Sandbox"):
        asyncio.run(
            fixture_runtime.prepare_fixture(
                SCENARIO_REGISTRY.get("delete"),
                args,
                options,
                object(),
                {"id": "notebook", "name": "Notebook"},
                str(tmp_path / "notebooks" / "Notebook"),
                SCENARIO_SPECS["delete"],
            )
        )

    assert test_utils.read_json(tmp_path / "manifest.json")["fixture_validation"]["status"] == "failed"
    assert test_utils.read_json(tmp_path / "fixture-result.json")["validation"]["passed"] is False
    assert (tmp_path / "prepared.json").exists()


def test_default_identity_uses_one_timestamp(capsys) -> None:
    assert main(["rename", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    safe_timestamp = payload["run_identity"]["safe_timestamp"]
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}",
        safe_timestamp,
    )
    assert payload["notebook_name"] == f"__rename-{safe_timestamp}__"
    assert Path(payload["run_dir"]).name == f"run-{safe_timestamp}"


def test_keep_dry_run_omits_close(capsys, tmp_path) -> None:
    run_dir = tmp_path / "run"
    assert main(
        [
            "reparent-section",
            "--notebook-label",
            "custom",
            "--run-dir",
            str(run_dir),
            "--keep-notebook",
            "--dry-run",
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lifecycle"] == "keep"
    assert [step["step"] for step in payload["ordered_steps"]] == [
        "create-source-notebook",
        "reparent-section",
        "report",
    ]
    assert not run_dir.exists()


def test_keep_worksite_preserves_source_lifecycle_after_copy_success(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    async def preserved_copy(
        args,
        _options,
        _manifest_value,
        *,
        client=None,
        fixture_result=None,
    ):
        assert client is FakeMCP.active
        assert args.keep_worksite is True
        return {
            "scenario": args.scenario,
            "status": "passed",
            "worksite_preserved": True,
        }

    monkeypatch.setattr(SCENARIO_REGISTRY.get("copy-page"), "execute", preserved_copy)
    args = _args(tmp_path / "run", "copy-page")
    args.keep_worksite = True

    result = asyncio.run(
        validation.run_validate(
            args,
            RuntimeOptions(args.run_dir, 1_800, False, False),
        )
    )

    assert all(wrapper.closed is False for wrapper in FakeLifecycle.instances)
    assert result["lifecycle"]["status"] == "preserved_open"
    assert result["ordered_steps"][-1] == "preserve-notebook-bundle"


def test_cli_exposes_flat_scenarios_special_all_and_clear_maintenance() -> None:
    parser = build_parser()
    choices = next(
        action.choices
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert set(choices) == {*SCENARIOS, "all", "clear"}
    for removed in ("validate", "inspect", "read", "baseline", "report", "suite"):
        assert removed not in choices


class FakeLifecycle:
    instances: list["FakeLifecycle"] = []

    def __init__(self, run_dir: Path, *, timeout_seconds: int, role: str = "source") -> None:
        self.run_dir = run_dir
        self.timeout_seconds = timeout_seconds
        self.role = role
        self.lease_path = run_dir / (
            "lifecycle-lease.json" if role == "source" else f"lifecycle-lease-{role}.json"
        )
        self.closed = False
        self.preserved = False
        self.open_count = 0
        self.__class__.instances.append(self)

    def create_fresh_notebook(self, name: str):
        path = (self.run_dir / "notebooks" / name).resolve()
        path.mkdir(parents=True)
        notebook_id = "notebook-id" if self.role == "source" else f"{self.role}-notebook-id"
        lease = {
            "notebook_id": notebook_id,
            "expected_name": name,
            "expected_local_path": str(path),
        }
        test_utils.write_json(self.lease_path, {"schema_version": 1, **lease})
        return {"id": notebook_id, "name": name}, lease

    def get_exact_notebook(self, lease=None):
        lease = lease or test_utils.read_json(self.lease_path)
        return {"id": lease["notebook_id"], "name": lease["expected_name"]}

    def close_exact_notebook(self):
        self.closed = True
        if self.open_count == 0:
            notebook_id = (
                "notebook-id" if self.role == "source" else f"{self.role}-notebook-id"
            )
        else:
            notebook_id = (
                f"reopened-{self.open_count}-notebook-id"
                if self.role == "source"
                else f"reopened-{self.open_count}-{self.role}-notebook-id"
            )
        return {
            "closed": True,
            "source_notebook_id": notebook_id,
            "close_before": {"id": notebook_id},
        }

    def working_notebook_open_lock(self):
        from contextlib import nullcontext

        return nullcontext()

    def snapshot_open_notebooks(self):
        return {}

    def assert_no_active_working_conflict(self, **_kwargs):
        return None

    def open_working_notebook(
        self,
        name,
        working_path,
        *,
        template_paths,
        role="source",
        lease_archive_reason="cold-build",
        activate_hierarchy=True,
        _allow_activation_retry=True,
    ):
        assert role == self.role
        assert template_paths == ()
        self.open_count += 1
        assert lease_archive_reason == (
            "persistence-checkpoint"
            if self.open_count == 1
            else "persistence-import-checkpoint"
        )
        assert activate_hierarchy is (self.open_count == 1)
        assert _allow_activation_retry is False
        self.closed = False
        notebook_id = (
            f"reopened-{self.open_count}-notebook-id"
            if self.role == "source"
            else f"reopened-{self.open_count}-{self.role}-notebook-id"
        )
        lease = {
            "notebook_id": notebook_id,
            "expected_name": name,
            "expected_local_path": str(Path(working_path).resolve()),
            "actual_local_path": str(Path(working_path).resolve()),
            "hierarchy_open_status": (
                "passed" if activate_hierarchy else "deferred_to_fixture_convergence"
            ),
            "opened_hierarchy": ([{"object_id": "section"}] if activate_hierarchy else []),
        }
        test_utils.write_json(self.lease_path, {"schema_version": 1, **lease})
        return {"id": notebook_id, "name": name}, lease

class FakeMCP:
    starts = 0
    active: "FakeMCP | None" = None

    def __init__(self, **kwargs) -> None:
        self.policy = kwargs["policy"]
        self.allowed_tools = set(kwargs["allowed_tools"]) | {"health_check"}
        self.timeout_seconds = kwargs["timeout_seconds"]

    async def __aenter__(self):
        self.__class__.starts += 1
        self.__class__.active = self
        return self

    async def __aexit__(self, *_args):
        self.__class__.active = None


def _install_orchestration_fakes(monkeypatch, calls: list[str]) -> None:
    FakeLifecycle.instances.clear()
    FakeMCP.starts = 0
    monkeypatch.setattr(validation, "NotebookLifecycleWrapper", FakeLifecycle)
    monkeypatch.setattr(validation, "MCPStdioClient", FakeMCP)

    async def fake_fixture(scenario, args, options, client, _notebook, _path, spec):
        assert client is FakeMCP.active
        assert scenario is SCENARIO_REGISTRY.get(args.scenario)
        assert spec == scenario.runtime_spec(args)
        calls.append("fixture")
        manifest = _manifest(options.run_dir, args.notebook_name)
        test_utils.write_json(options.run_dir / "manifest.json", manifest)
        return manifest, {"profile": spec.fixture.name}

    async def fake_fixture_bundle(scenario, args, options, client, notebooks, paths, spec):
        assert client is FakeMCP.active
        calls.append("fixture")
        manifest = _manifest(options.run_dir, notebooks["source"]["name"])
        manifest["notebook"] = dict(notebooks["source"])
        manifest["notebooks"] = {role: dict(value) for role, value in notebooks.items()}
        manifest["notebook_paths"] = dict(paths)
        manifest["disposable_targets"].update(
            {f"{role}_notebook_path": path for role, path in paths.items()}
        )
        test_utils.write_json(options.run_dir / "manifest.json", manifest)
        return manifest, {"profile": spec.fixture.name}

    async def fake_reopened_fixture_bundle(
        scenario,
        args,
        options,
        client,
        notebooks,
        paths,
        prior_manifest,
        prior_result,
        close_results,
    ):
        assert client is FakeMCP.active
        assert scenario.fixture_recipe.requires_persistence_checkpoint is True
        assert set(close_results) == set(notebooks) == set(paths)
        calls.append("persistence-checkpoint")
        manifest = dict(prior_manifest)
        manifest["notebook"] = dict(notebooks["source"])
        manifest["notebooks"] = {
            role: dict(value) for role, value in notebooks.items()
        }
        test_utils.write_json(options.run_dir / "manifest.json", manifest)
        return manifest, dict(prior_result)

    def fake_report(run_dir):
        calls.append("report")
        return run_dir / "report.md"

    monkeypatch.setattr(validation, "prepare_fixture_bundle", fake_fixture_bundle)
    monkeypatch.setattr(
        validation,
        "prepare_reopened_fixture_bundle",
        fake_reopened_fixture_bundle,
    )
    monkeypatch.setattr(validation, "render_report", fake_report)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_each_scenario_uses_exactly_one_mcp_process(monkeypatch, tmp_path, scenario) -> None:
    calls: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    if SCENARIO_REGISTRY.get(scenario).fixture_recipe.consumer_scenario:
        with pytest.raises(runtime.RunnerFailure, match="require --use-cache"):
            asyncio.run(
                validation.run_validate(
                    _args(tmp_path / scenario, scenario),
                    RuntimeOptions(tmp_path / scenario, 1_800, False, False),
                )
            )
        assert FakeMCP.starts == 0
        return

    async def fake_scenario(
        args,
        _options,
        _manifest_value,
        *,
        client=None,
        fixture_result=None,
    ):
        assert client is FakeMCP.active
        calls.append(args.scenario)
        if args.scenario == "delete":
            assert args.delete_target_id == "disposable-group"
        return {"scenario": args.scenario, "status": "passed"}

    monkeypatch.setattr(SCENARIO_REGISTRY.get(scenario), "execute", fake_scenario)
    if getattr(SCENARIO_REGISTRY.get(scenario), "requires_lifecycle_wrappers", False):
        async def fake_lifecycle_scenario(
            args,
            _options,
            _manifest_value,
            *,
            client=None,
            fixture_result=None,
            wrappers=None,
        ):
            assert wrappers is not None
            return await fake_scenario(
                args,
                _options,
                _manifest_value,
                client=client,
                fixture_result=fixture_result,
            )

        monkeypatch.setattr(
            SCENARIO_REGISTRY.get(scenario),
            "execute_with_lifecycle",
            fake_lifecycle_scenario,
        )

    result = asyncio.run(
        validation.run_validate(
            _args(tmp_path / scenario, scenario),
            RuntimeOptions(tmp_path / scenario, 1_800, False, False),
        )
    )

    assert FakeMCP.starts == 1
    assert calls[0] == "fixture"
    expected_calls = (
        ["fixture", "persistence-checkpoint", scenario]
        if SCENARIO_REGISTRY.get(scenario).fixture_recipe.requires_persistence_checkpoint
        else ["fixture", scenario]
    )
    assert calls[: len(expected_calls)] == expected_calls
    assert FakeLifecycle.instances[0].closed is True
    assert result["metrics"]["observed_mcp_process_starts"] == 1
    multi_role = len(
        SCENARIO_REGISTRY.get(scenario).fixture_recipe.cache_identity.notebook_roles
    ) > 1
    assert result["ordered_steps"] == [
        "create-notebook-bundle" if multi_role else "create-source-notebook",
        scenario,
        "report",
        "close-notebook-bundle" if multi_role else "close-source-notebook",
    ]


def test_manual_scenario_fails_preflight_before_run_or_notebook_creation(
    monkeypatch, tmp_path
) -> None:
    from local_onenote_mcp.onenote_errors import OneNoteDesktopNotRunningError

    run_dir = tmp_path / "run"
    monkeypatch.setattr(
        validation,
        "require_onenote_desktop",
        lambda: (_ for _ in ()).throw(
            OneNoteDesktopNotRunningError(
                "OneNote Desktop is not running with a visible GUI. Start OneNote and retry.",
                operation="health_preflight",
            )
        ),
    )
    monkeypatch.setattr(
        validation,
        "NotebookLifecycleWrapper",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("desktop preflight must run before lifecycle")
        ),
    )

    with pytest.raises(runtime.RunnerFailure, match="Start OneNote and retry") as raised:
        asyncio.run(
            validation.run_validate(
                _args(run_dir, "rename"),
                RuntimeOptions(run_dir, 180, False, False),
            )
        )

    assert raised.value.exit_code == runtime.EXIT_MCP
    assert not run_dir.exists()


def test_manual_scenario_dry_run_never_probes_desktop(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        validation,
        "require_onenote_desktop",
        lambda: (_ for _ in ()).throw(
            AssertionError("dry-run must not inspect OneNote GUI state")
        ),
    )
    args = _args(tmp_path / "dry-run", "rename")
    args.dry_run = True

    result = asyncio.run(
        validation.run_validate(
            args,
            RuntimeOptions(
                run_dir=args.run_dir,
                timeout=180,
                json_output=False,
                dry_run=True,
            ),
        )
    )

    assert result["dry_run"] is True
    assert not args.run_dir.exists()


def test_scenario_execution_defers_close_to_top_level_failure_finalization(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    async def failing(*_args, **_kwargs):
        calls.append("rename")
        raise runtime.InvariantFailure("scenario mismatch")

    monkeypatch.setattr(SCENARIO_REGISTRY.get("rename"), "execute", failing)
    args = _args(tmp_path / "run", "rename")
    with pytest.raises(runtime.InvariantFailure):
        asyncio.run(validation.run_validate(args, RuntimeOptions(args.run_dir, 180, False, False)))
    assert calls == ["fixture", "rename"]
    assert FakeLifecycle.instances[0].closed is False
    state = test_utils.read_json(args.run_dir / "run-state.json")
    assert state["current_step"] == "rename"
    assert state["finalization_started"] is False


def test_interactive_detection_failure_defers_close_and_does_not_initialize_cache(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []
    cache_initializations: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    class ForbiddenCacheStore:
        def __init__(self, *_args, **_kwargs) -> None:
            cache_initializations.append("constructed")

    async def failing(*_args, **_kwargs):
        calls.append("interactive-detector")
        raise runtime.InvariantFailure("requested InkDrawing=1; observed=0")

    scenario = SCENARIO_REGISTRY.get("bootstrap-ink-drawing-fixture")
    monkeypatch.setattr(scenario, "execute", failing)
    monkeypatch.setattr(validation, "BundleCacheStore", ForbiddenCacheStore)
    args = _args(tmp_path / "run", scenario.name)

    with pytest.raises(runtime.InvariantFailure, match="InkDrawing"):
        asyncio.run(
            validation.run_validate(
                args,
                RuntimeOptions(args.run_dir, 1_800, False, False),
            )
        )

    assert calls == ["fixture", "interactive-detector"]
    assert cache_initializations == []
    assert FakeLifecycle.instances[0].closed is False
    state = test_utils.read_json(args.run_dir / "run-state.json")
    assert state["current_step"] == scenario.name
    assert state["finalization_started"] is False


def test_restore_failure_never_enters_source_finalization(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    async def restore_failed(*_args, **_kwargs):
        raise runtime.RestoreFailure("restored snapshot mismatch")

    monkeypatch.setattr(SCENARIO_REGISTRY.get("rename"), "execute", restore_failed)
    args = _args(tmp_path / "run", "rename")
    with pytest.raises(runtime.RestoreFailure, match="snapshot mismatch"):
        asyncio.run(validation.run_validate(args, RuntimeOptions(args.run_dir, 180, False, False)))

    assert FakeLifecycle.instances[0].closed is False
    state = test_utils.read_json(args.run_dir / "run-state.json")
    assert state["finalization_started"] is False


def test_copy_only_records_cleanup_and_closes_by_default(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    async def copy_only(
        args,
        options,
        _manifest_value,
        *,
        client=None,
        fixture_result=None,
    ):
        assert client is FakeMCP.active
        partial = {
            "outcome": "copy_only",
            "created_ids": ["copied-page"],
            "id_map": {"disposable-page": "copied-page"},
        }
        test_utils.write_json(
            test_utils.scenario_dir(options.run_dir, args.scenario) / "copy-result.json",
            partial,
        )
        raise ClientFailure("copy_only", envelope=partial)

    monkeypatch.setattr(
        SCENARIO_REGISTRY.get("move-page"), "execute", copy_only
    )
    args = _args(tmp_path / "run", "move-page")
    with pytest.raises(ClientFailure, match="copy_only"):
        asyncio.run(validation.run_validate(args, RuntimeOptions(args.run_dir, 1_800, False, False)))
    finalization = validation.record_failure(args, "copy_only", runtime.EXIT_MCP)

    assert finalization["status"] == "closed"
    assert finalization["isolation_passed"] is True
    assert set(finalization["roles"]) == {"source", "destination"}
    assert all(role["closed"] is True for role in finalization["roles"].values())
    assert any(wrapper.closed for wrapper in FakeLifecycle.instances[1:])
    failure = test_utils.read_json(
        args.run_dir / "scenarios" / args.scenario / "failure.json"
    )
    assert failure["status"] == "needs_manual_cleanup"
    assert failure["created_ids"] == ["copied-page"]
    state = test_utils.read_json(args.run_dir / "run-state.json")
    assert state["status"] == "failed_closed"
    assert state["failed_step"] == "move-page"
    run_failure = test_utils.read_json(args.run_dir / "run-failure.json")
    assert run_failure["failure_finalization"]["closed"] is True
    assert run_failure["filesystem_deleted"] is False


@pytest.mark.parametrize("flag", ("keep_notebook", "keep_worksite"))
def test_failure_explicit_keep_mode_preserves_open(monkeypatch, tmp_path, flag) -> None:
    calls: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    async def failed(*_args, **_kwargs):
        raise runtime.InvariantFailure("injected failure")

    monkeypatch.setattr(SCENARIO_REGISTRY.get("rename"), "execute", failed)
    args = _args(tmp_path / flag, "rename")
    setattr(args, flag, True)
    with pytest.raises(runtime.InvariantFailure, match="injected failure"):
        asyncio.run(
            validation.run_validate(
                args,
                RuntimeOptions(args.run_dir, 180, False, False),
            )
        )

    finalization = validation.record_failure(args, "injected failure", runtime.EXIT_INVARIANT)

    assert finalization["status"] == "preserved_open"
    assert finalization["isolation_passed"] is False
    assert not any(wrapper.closed for wrapper in FakeLifecycle.instances)
    assert test_utils.read_json(args.run_dir / "run-state.json")["status"] == "failed_preserved_open"


def test_failure_before_run_creation_is_already_isolated_without_creating_directory(tmp_path) -> None:
    args = _args(tmp_path / "absent", "rename")

    finalization = validation.record_failure(args, "preflight", runtime.EXIT_INVARIANT)

    assert finalization["status"] == "not_started"
    assert finalization["isolation_passed"] is True
    assert not args.run_dir.exists()


def test_failure_close_error_is_not_reported_as_isolated(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    async def failed(*_args, **_kwargs):
        raise runtime.InvariantFailure("injected failure")

    monkeypatch.setattr(SCENARIO_REGISTRY.get("rename"), "execute", failed)
    args = _args(tmp_path / "close-failed", "rename")
    with pytest.raises(runtime.InvariantFailure):
        asyncio.run(
            validation.run_validate(
                args,
                RuntimeOptions(args.run_dir, 180, False, False),
            )
        )

    def close_failed(_self):
        raise runtime.RestoreFailure("injected close failure")

    monkeypatch.setattr(FakeLifecycle, "close_exact_notebook", close_failed)
    finalization = validation.record_failure(args, "injected failure", runtime.EXIT_INVARIANT)

    assert finalization["status"] == "close_failed"
    assert finalization["closed"] is False
    assert finalization["isolation_passed"] is False
    assert finalization["roles"]["source"]["status"] == "close_failed"
    assert test_utils.read_json(args.run_dir / "run-state.json")["status"] == "failure_finalization_failed"


def test_copy_notebook_created_target_without_close_evidence_blocks_isolation(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []
    _install_orchestration_fakes(monkeypatch, calls)

    async def failed(args, options, *_args, **_kwargs):
        test_utils.write_json(
            test_utils.scenario_dir(options.run_dir, args.scenario)
            / "copy-result.json",
            {
                "created_ids": ["copied-notebook"],
                "item": {"id": "copied-notebook", "resource_type": "notebook"},
            },
        )
        raise runtime.InvariantFailure("post-copy failure")

    monkeypatch.setattr(SCENARIO_REGISTRY.get("copy-notebook"), "execute", failed)
    args = _args(tmp_path / "copy-target-unproven", "copy-notebook")
    with pytest.raises(runtime.InvariantFailure):
        asyncio.run(
            validation.run_validate(
                args,
                RuntimeOptions(args.run_dir, 1_800, False, False),
            )
        )

    finalization = validation.record_failure(
        args,
        "post-copy failure",
        runtime.EXIT_INVARIANT,
    )

    assert finalization["status"] == "close_failed"
    assert finalization["isolation_passed"] is False
    assert finalization["roles"]["source"]["closed"] is True
    assert finalization["roles"]["copy-target"]["status"] == "close_unproven"


def test_finalize_uses_lifecycle_lease_and_never_starts_mcp(tmp_path) -> None:
    run_dir = tmp_path / "run"
    wrapper = FakeLifecycle(run_dir, timeout_seconds=180)
    _notebook, _lease = wrapper.create_fresh_notebook("__ISOLATED__")
    manifest = _manifest(run_dir)
    result = asyncio.run(
        validation.finalize_notebook(
            _args(run_dir, "reparent-section"),
            RuntimeOptions(run_dir, 180, False, False),
            manifest,
            wrapper=wrapper,
        )
    )
    assert wrapper.closed is True
    assert result["status"] == "closed_preserved"
    assert Path(manifest["disposable_targets"]["source_notebook_path"]).exists()


def test_finalize_accepts_only_exact_durable_preclosed_lifecycle_evidence(tmp_path) -> None:
    run_dir = tmp_path / "run"
    wrapper = FakeLifecycle(run_dir, timeout_seconds=180)
    _notebook, _lease = wrapper.create_fresh_notebook("__ISOLATED__")
    lease = test_utils.read_json(wrapper.lease_path)
    lease.update(
        state="closed",
        close_result={
            "closed": True,
            "source_notebook_id": "notebook-id",
            "close_before": {"id": "notebook-id"},
            "filesystem_deleted": False,
        },
    )
    test_utils.write_json(wrapper.lease_path, lease)

    result = asyncio.run(
        validation.finalize_notebook(
            _args(run_dir, "query-metadata-scopes"),
            RuntimeOptions(run_dir, 180, False, False),
            _manifest(run_dir),
            wrapper=wrapper,
        )
    )

    assert wrapper.closed is False
    assert result["closed"] is True
    assert result["status"] == "closed_preserved"
    assert result["close_result"] == lease["close_result"]


def test_finalize_rejects_unbound_preclosed_lifecycle_evidence(tmp_path) -> None:
    run_dir = tmp_path / "run"
    wrapper = FakeLifecycle(run_dir, timeout_seconds=180)
    _notebook, _lease = wrapper.create_fresh_notebook("__ISOLATED__")
    lease = test_utils.read_json(wrapper.lease_path)
    lease.update(
        state="closed",
        close_result={"closed": True, "source_notebook_id": "other-notebook"},
    )
    test_utils.write_json(wrapper.lease_path, lease)

    with pytest.raises(runtime.RestoreFailure, match="exact close evidence"):
        asyncio.run(
            validation.finalize_notebook(
                _args(run_dir, "query-metadata-scopes"),
                RuntimeOptions(run_dir, 180, False, False),
                _manifest(run_dir),
                wrapper=wrapper,
            )
        )

    assert wrapper.closed is False


def test_bundle_finalization_failure_records_completed_roles_and_preserves_paths(tmp_path) -> None:
    run_dir = tmp_path / "run"
    destination = FakeLifecycle(run_dir, timeout_seconds=180, role="destination")
    source = FakeLifecycle(run_dir, timeout_seconds=180)
    destination_notebook, destination_lease = destination.create_fresh_notebook(
        "Destination"
    )
    source_notebook, source_lease = source.create_fresh_notebook("Source")
    manifest = _manifest(run_dir, "Source")
    manifest["notebook"] = source_notebook
    manifest["notebooks"] = {
        "destination": destination_notebook,
        "source": source_notebook,
    }
    manifest["disposable_targets"].update(
        destination_notebook_path=destination_lease["expected_local_path"],
        source_notebook_path=source_lease["expected_local_path"],
    )

    def fail_source_close():
        raise runtime.RestoreFailure("injected source close failure")

    source.close_exact_notebook = fail_source_close

    with pytest.raises(runtime.RestoreFailure, match="injected source close failure"):
        asyncio.run(
            validation.finalize_bundle(
                _args(run_dir, "copy-page"),
                RuntimeOptions(run_dir, 180, False, False),
                manifest,
                wrappers={"destination": destination, "source": source},
                roles=("destination", "source"),
            )
        )

    evidence = test_utils.read_json(run_dir / "lifecycle-bundle.json")
    assert evidence["status"] == "failed_preserved_bundle"
    assert evidence["completed_roles"] == ["destination"]
    assert evidence["filesystem_deleted"] is False
    assert Path(destination_lease["expected_local_path"]).exists()
    assert Path(source_lease["expected_local_path"]).exists()


def test_copy_notebook_finalization_closes_source_lease_and_preserves_both_paths(tmp_path) -> None:
    run_dir = tmp_path / "run"
    wrapper = FakeLifecycle(run_dir, timeout_seconds=180)
    wrapper.create_fresh_notebook("__ISOLATED__")
    manifest = _manifest(run_dir)
    copy_path = (run_dir / "notebook-copies" / "Copy").resolve()
    copy_path.mkdir(parents=True)
    test_utils.write_json(
        test_utils.scenario_dir(run_dir, "copy-notebook") / "restored.json",
        {"target_path": str(copy_path)},
    )

    result = asyncio.run(
        validation.finalize_notebook(
            _args(run_dir, "copy-notebook"),
            RuntimeOptions(run_dir, 180, False, False),
            manifest,
            wrapper=wrapper,
        )
    )

    assert wrapper.closed is True
    assert result["source_notebook_id"] == "notebook-id"
    assert set(result["preserved_paths"]) == {
        manifest["disposable_targets"]["source_notebook_path"],
        str(copy_path),
    }
    assert copy_path.exists()
    assert "close_notebook" in SCENARIO_SPECS["copy-notebook"].tool_allowlist
    assert "delete_section" not in SCENARIO_SPECS["copy-notebook"].tool_allowlist


def test_copy_notebook_keep_worksite_preserves_open_source_and_target_path(tmp_path) -> None:
    run_dir = tmp_path / "run"
    wrapper = FakeLifecycle(run_dir, timeout_seconds=180)
    wrapper.create_fresh_notebook("__ISOLATED__")
    manifest = _manifest(run_dir)
    copy_path = (run_dir / "notebook-copies" / "Copy").resolve()
    copy_path.mkdir(parents=True)
    test_utils.write_json(
        test_utils.scenario_dir(run_dir, "copy-notebook") / "worksite.json",
        {
            "target_path": str(copy_path),
            "manual_cleanup_required": True,
        },
    )
    args = _args(run_dir, "copy-notebook")
    args.keep_worksite = True

    result = asyncio.run(
        validation.finalize_notebook(
            args,
            RuntimeOptions(run_dir, 180, False, False),
            manifest,
            wrapper=wrapper,
        )
    )

    assert wrapper.closed is False
    assert result["status"] == "preserved_open"
    assert set(result["preserved_paths"]) == {
        manifest["disposable_targets"]["source_notebook_path"],
        str(copy_path),
    }


def test_keep_validates_lease_but_does_not_close(tmp_path) -> None:
    run_dir = tmp_path / "run"
    wrapper = FakeLifecycle(run_dir, timeout_seconds=180)
    wrapper.create_fresh_notebook("__ISOLATED__")
    result = asyncio.run(
        validation.finalize_notebook(
            _args(run_dir, "reparent-section", keep=True),
            RuntimeOptions(run_dir, 180, False, False),
            _manifest(run_dir),
            wrapper=wrapper,
        )
    )
    assert wrapper.closed is False
    assert result["status"] == "preserved_open"


def test_nonempty_run_dir_and_unsafe_name_fail_without_mutation(tmp_path, capsys) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "existing.txt").write_text("preserve", encoding="utf-8")
    assert main(["rename", "--notebook-label", "isolated", "--run-dir", str(run_dir), "--json"]) == 2
    assert "absent or empty" in capsys.readouterr().out
    assert sorted(path.name for path in run_dir.iterdir()) == ["existing.txt"]

    unsafe = tmp_path / "unsafe"
    assert main(["rename", "--notebook-label", "unsafe/name", "--run-dir", str(unsafe), "--dry-run", "--json"]) == 2
    assert "lowercase kebab-case" in capsys.readouterr().out
    assert not unsafe.exists()


def test_existing_empty_run_dir_is_accepted(tmp_path) -> None:
    path = tmp_path / "empty"
    path.mkdir()
    validation._assert_fresh_run_dir(path)
