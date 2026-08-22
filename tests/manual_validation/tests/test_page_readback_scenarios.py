"""Pure contracts for automatic Page path/Copy/Move readback scenarios."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_onenote_mcp.page.builder import build_page_update_xml
from tests.manual_validation.runtime import InvariantFailure
from tests.manual_validation.scenarios.common.copy_runtime import (
    copy_execute_arguments,
    copy_spec,
)
from tests.manual_validation.scenarios.common.page_readback import (
    PURE_RICH_TEXT_HTML,
    SPECIAL_PAGE_TITLE,
    SPECIAL_PAGE_TITLE_CASEFOLD,
    THREE_COLUMN_TABLE_HTML,
    assert_default_page_title_readback,
    assert_semantic_content_page_readback,
)
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.common.fixture_models import (
    FixtureValidationContext,
)
from tests.manual_validation.scenarios.fixture_recipes.recipe_base import (
    FixtureBuildResult,
)


def _semantic_report(*, override: bool = False) -> dict:
    return {
        "verified": True,
        "lossless": True,
        "copy_contract_satisfied": True,
        "page_results": [
            {
                "source_page_id": "source-page",
                "date_time": {"status": "verified"},
                "lossless": True,
                "equivalence": {
                    "verification_tier": "semantic_content_v1",
                    "equivalent": True,
                    "failed_content_object_types": [],
                    "semantic_content_comparison": {
                        "source_complete": True,
                        "target_complete": True,
                        "passed": True,
                        "table_column_width_comparisons": [
                            {
                                "content_object_type": "Table",
                                "component_type": "Column",
                                "field": "width",
                                "table_ordinal": 0,
                                "column_ordinal": 1,
                                "comparison": "relative_tolerance",
                                "allowed_relative_delta": 0.05,
                                "observed_relative_delta": 0.01,
                                "passed": True,
                                "content_exposed": False,
                            }
                        ],
                    },
                },
                "semantic_content_stages": {
                    "title_override_requested": override,
                    "source_to_transformed": {
                        "passed": True,
                        "checks": {"title": True},
                    },
                    "transformed_to_target": {
                        "passed": True,
                        "checks": {"title": True},
                    },
                },
                "title_readback_stages": {
                    "title_override_requested": override,
                    "source_to_transformed": {
                        "passed": True,
                        "checks": {"title": True},
                    },
                    "transformed_to_target": {
                        "passed": True,
                        "checks": {"title": True},
                    },
                },
            }
        ],
    }


def test_default_title_readback_requires_both_title_stages() -> None:
    evidence = assert_default_page_title_readback(
        _semantic_report(),
        source_page_id="source-page",
    )

    assert evidence == {
        "source_page_id": "source-page",
        "title_parameter": "omitted",
        "title_override_requested": False,
        "source_to_transformed_title": True,
        "transformed_to_target_title": True,
        "copy_contract_satisfied": True,
        "content_exposed": False,
    }
    with pytest.raises(InvariantFailure, match="did not preserve the title"):
        assert_default_page_title_readback(
            _semantic_report(override=True),
            source_page_id="source-page",
        )


def test_semantic_content_readback_requires_exact_complete_page_scope() -> None:
    evidence = assert_semantic_content_page_readback(
        _semantic_report(),
        source_page_ids=["source-page"],
    )

    assert evidence["verification_tier"] == "semantic_content_v1"
    assert evidence["semantic_projection_complete"] is True
    assert evidence["table_column_width_comparisons"] == [
        {
            "source_page_id": "source-page",
            "table_ordinal": 0,
            "column_ordinal": 1,
            "allowed_relative_delta": 0.05,
            "observed_relative_delta": 0.01,
            "passed": True,
            "content_exposed": False,
        }
    ]
    wrong_tier = _semantic_report()
    wrong_tier["page_results"][0]["equivalence"]["verification_tier"] = (
        "strict_canonical"
    )
    with pytest.raises(InvariantFailure, match="semantic_content_v1"):
        assert_semantic_content_page_readback(
            wrong_tier,
            source_page_ids=["source-page"],
        )


def test_copy_page_cross_notebook_case_omits_destination_title() -> None:
    def item(object_id: str, resource_type: str, **values) -> dict:
        return {"id": object_id, "resource_type": resource_type, **values}

    source = item(
        "source-page",
        "page",
        title=SPECIAL_PAGE_TITLE,
        section_id="source-section",
        modified="stable",
    )
    manifest = {
        "notebook": item("source-notebook", "notebook", name="Source"),
        "notebooks": {
            "source": item("source-notebook", "notebook", name="Source"),
            "destination": item(
                "destination-notebook", "notebook", name="Destination"
            ),
        },
        "structure": {
            "parent_page": source,
            "semantic_page": item(
                "child", "page", title="Child", section_id="source-section"
            ),
            "source_section": item("source-section", "section", name="Source"),
            "disposable_section": item(
                "other-section", "section", name="Other"
            ),
            "cross_section_anchor": item(
                "cross-anchor", "page", title="Child", section_id="other-section"
            ),
            "cross_section_root_title_anchor": item(
                "cross-root-title",
                "page",
                title=SPECIAL_PAGE_TITLE,
                section_id="other-section",
            ),
            "cross_section_root_title_casefold_anchor": item(
                "cross-root-title-casefold",
                "page",
                title=SPECIAL_PAGE_TITLE_CASEFOLD,
                section_id="other-section",
            ),
            "cross_notebook_section": item(
                "destination-section", "section", name="Destination"
            ),
            "cross_notebook_anchor": item(
                "notebook-anchor",
                "page",
                title="Child",
                section_id="destination-section",
            ),
            "cross_notebook_root_title_anchor": item(
                "notebook-root-title",
                "page",
                title=SPECIAL_PAGE_TITLE,
                section_id="destination-section",
            ),
        },
    }

    spec = copy_spec("copy-page", manifest, Path("run"), name_suffix="stamp")
    default_case = next(
        case for case in spec["cases"] if case["name"] == "cross-notebook-root-only"
    )
    arguments = copy_execute_arguments(
        {
            **spec,
            "destination": default_case["destination"],
            "destination_name": default_case["destination_name"],
            "destination_title_parameter": default_case[
                "destination_title_parameter"
            ],
            "include_descendants": default_case["include_descendants"],
        },
        source,
    )

    assert default_case["destination_name"] == SPECIAL_PAGE_TITLE
    assert default_case["destination_title_parameter"] == "omitted"
    assert "destination_title" not in arguments
    assert arguments["expected_title"] == SPECIAL_PAGE_TITLE
    assert sum(
        case["destination_title_parameter"] == "omitted"
        for case in spec["cases"]
    ) == 2


def test_move_page_recipe_owns_exact_three_column_special_title_fixture() -> None:
    recipe = SCENARIO_REGISTRY.get("move-page").fixture_recipe
    xml = build_page_update_xml(
        "root-page",
        title=SPECIAL_PAGE_TITLE,
        content=THREE_COLUMN_TABLE_HTML,
        content_format="html",
    )
    build = FixtureBuildResult({"root_only_page": {"id": "root-page"}}, {})

    recipe.begin_snapshot_content_validation()
    recipe.snapshot_page_observer("source", build)({"id": "root-page"}, xml)
    recipe.complete_snapshot_content_validation()

    contract = SCENARIO_REGISTRY.get("move-page").spec.execution_contract
    root_case = contract["cases"][0]
    assert root_case["destination_title"] == "omitted"
    assert SPECIAL_PAGE_TITLE in xml
    assert "<table>" not in PURE_RICH_TEXT_HTML


def test_move_page_recipe_requires_table_root_and_pure_rich_text_subtree() -> None:
    recipe = SCENARIO_REGISTRY.get("move-page").fixture_recipe
    structure = {
        "source_section": {
            "id": "section",
            "resource_type": "section",
            "parent_id": "notebook",
        },
        "root_only_page": {
            "id": "table-root",
            "resource_type": "page",
            "title": SPECIAL_PAGE_TITLE,
            "section_id": "section",
            "page_level": 1,
            "parent_page_id": None,
        },
        "root_only_child": {
            "id": "root-child",
            "resource_type": "page",
            "title": "02-Root-Only-Child",
            "section_id": "section",
            "page_level": 2,
            "parent_page_id": "table-root",
        },
        "subtree_page": {
            "id": "rich-root",
            "resource_type": "page",
            "title": "03-Subtree",
            "section_id": "section",
            "page_level": 1,
            "parent_page_id": None,
        },
        "subtree_child": {
            "id": "rich-child",
            "resource_type": "page",
            "title": "04-Subtree-Child",
            "section_id": "section",
            "page_level": 2,
            "parent_page_id": "rich-root",
        },
    }
    projections = {
        "table-root": {
            "complete": True,
            "capabilities": ["Outline", "RichText", "Table"],
            "unknown_nodes": [],
            "unsupported_page_roots": [],
        },
        "rich-root": {
            "complete": True,
            "capabilities": ["Outline", "RichText"],
            "unknown_nodes": [],
            "unsupported_page_roots": [],
        },
        "rich-child": {
            "complete": True,
            "capabilities": ["Outline", "RichText"],
            "unknown_nodes": [],
            "unsupported_page_roots": [],
        },
    }
    snapshot = {
        "notebook_id": "notebook",
        "items": list(structure.values()),
        "page_capability_projections": projections,
    }
    checks = recipe.validate(
        FixtureValidationContext(args=None, snapshot=snapshot),
        FixtureBuildResult(structure, {}),
    )

    assert "subtree Move source and child expose complete pure RichText projections" in checks
    projections["rich-child"]["capabilities"].append("Table")
    with pytest.raises(InvariantFailure, match="pure RichText projections"):
        recipe.validate(
            FixtureValidationContext(args=None, snapshot=snapshot),
            FixtureBuildResult(structure, {}),
        )
