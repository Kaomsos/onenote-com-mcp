"""Completeness and feature-derived Recipe contract catalog checks."""

from __future__ import annotations

import argparse

import pytest

from tests.manual_validation.runtime import RunnerFailure
from tests.manual_validation.scenarios.common.recipe_contracts import (
    ContractOutcome,
    RecipeContractDimension,
    required_recipe_contract_cases,
)
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.fixture_recipes.interactive import (
    InteractiveBootstrapRequired,
    InteractiveFixtureRecipe,
    UserAuthoredRecipe,
)
from tests.manual_validation.scenarios.fixture_recipes.recipe_base import (
    FixtureBundleObservation,
    FixtureRoleObservation,
    FixtureCacheIdentity,
    NotebookRoleSpec,
    canonical_cache_fingerprint,
)
from tests.manual_validation.scenarios.common.fixture_models import FixtureBuildResult


CASES = required_recipe_contract_cases(SCENARIO_REGISTRY)
PINNED_RECIPE_VERSIONS = {
    "bootstrap-ink-drawing-fixture": 3,
    "bootstrap-inserted-file-fixture": 3,
    "bootstrap-media-file-fixture": 8,
    "bootstrap-shape-fixture": 5,
    "bootstrap-user-authored-fixture": 3,
    "copy-notebook": 3,
    "copy-page": 11,
    "copy-section": 4,
    "copy-section-group": 5,
    "create": 5,
    "reparent-page": 3,
}


def _interactive_observation(
    recipe,
    objects,
    capabilities,
    *,
    complete=True,
    structural_marker_counts=None,
):
    page_id = "canvas-page"
    build = FixtureBuildResult(
        {
            "canvas_section": {"id": "canvas-section"},
            "canvas_page": {"id": page_id},
        },
        {},
    )
    snapshot = {
        "items": [
            {
                "id": page_id,
                "resource_type": "page",
                "section_id": "canvas-section",
            }
        ],
        "page_objects": {page_id: list(objects)},
        "page_capability_projections": {
            page_id: {
                "schema_version": 1,
                "capabilities": list(capabilities),
                "object_kind_counts": {},
                "structural_marker_counts": dict(structural_marker_counts or {}),
                "unknown_nodes": [],
                "unsupported_page_roots": [],
                "complete": complete,
            }
        },
        "page_hashes": {page_id: "hash"},
    }
    return FixtureBundleObservation(
        roles={
            "source": FixtureRoleObservation(
                role="source",
                args=argparse.Namespace(),
                notebook={"id": "notebook"},
                notebook_path="C:/working/source",
                snapshot=snapshot,
                build=build,
            )
        }
    )


def test_catalog_is_unique_and_covers_every_owned_recipe_base_dimension() -> None:
    assert len({case.case_id for case in CASES}) == len(CASES)
    for scenario in SCENARIO_REGISTRY.values():
        recipe = scenario.fixture_recipe
        dimensions = {
            case.dimension for case in CASES if case.scenario_name == scenario.name
        }
        assert {
            RecipeContractDimension.OWNERSHIP,
            RecipeContractDimension.FRESH_DEFAULT,
            RecipeContractDimension.MANIFEST,
            RecipeContractDimension.RESPONSIBILITY,
        } <= dimensions
        if recipe.supports_cache:
            assert {
                RecipeContractDimension.CACHE_COLD,
                RecipeContractDimension.VALIDATED_HIT,
                RecipeContractDimension.INVALIDATION,
                RecipeContractDimension.IMMUTABILITY,
            } <= dimensions
            assert RecipeContractDimension.CACHE_UNSUPPORTED not in dimensions
        else:
            assert RecipeContractDimension.CACHE_UNSUPPORTED in dimensions
            assert not dimensions.intersection(
                {
                    RecipeContractDimension.CACHE_COLD,
                    RecipeContractDimension.VALIDATED_HIT,
                    RecipeContractDimension.INVALIDATION,
                    RecipeContractDimension.IMMUTABILITY,
                }
            )


def test_search_and_query_have_complete_cache_contracts() -> None:
    for scenario_name in ("search-all-open-notebooks", "query"):
        dimensions = {
            case.dimension for case in CASES if case.scenario_name == scenario_name
        }
        assert {
            RecipeContractDimension.CACHE_COLD,
            RecipeContractDimension.VALIDATED_HIT,
            RecipeContractDimension.INVALIDATION,
            RecipeContractDimension.IMMUTABILITY,
        } <= dimensions
        assert RecipeContractDimension.CACHE_UNSUPPORTED not in dimensions


def test_pinned_cache_recipe_versions_match_the_central_catalog() -> None:
    assert {
        name: SCENARIO_REGISTRY.get(name).fixture_recipe.recipe_version
        for name in PINNED_RECIPE_VERSIONS
    } == PINNED_RECIPE_VERSIONS


def test_fingerprint_is_structural_deterministic_and_runtime_value_free() -> None:
    recipe = SCENARIO_REGISTRY.get("copy-page").fixture_recipe
    identity = recipe.cache_identity
    recreated = FixtureCacheIdentity(
        schema_version=identity.schema_version,
        recipe_name=identity.recipe_name,
        recipe_version=identity.recipe_version,
        notebook_roles=tuple(
            NotebookRoleSpec(role.role, role.profile, dict(reversed(list(role.fixture_parameters.items()))))
            for role in identity.notebook_roles
        ),
        evidence_schema_version=identity.evidence_schema_version,
        contract_compatibility_version=identity.contract_compatibility_version,
        bundle_invariants=identity.bundle_invariants,
    )
    assert canonical_cache_fingerprint(recreated) == recipe.cache_fingerprint
    serialized = str(identity.as_dict())
    assert "run-dir" not in serialized
    assert "notebook-id" not in serialized
    assert "timestamp" not in serialized


def test_interactive_miss_never_calls_scaffold_or_waits_for_input(monkeypatch) -> None:
    recipe = SCENARIO_REGISTRY.get("bootstrap-ink-drawing-fixture").fixture_recipe
    assert isinstance(recipe, InteractiveFixtureRecipe)
    called = False

    async def forbidden(_context):
        nonlocal called
        called = True

    monkeypatch.setattr(recipe, "build_scaffold", forbidden)
    with pytest.raises(InteractiveBootstrapRequired):
        import asyncio

        asyncio.run(recipe.build(object()))
    assert called is False


@pytest.mark.parametrize(
    "scenario_name",
    [
        "bootstrap-inserted-file-fixture",
        "bootstrap-ink-drawing-fixture",
        "bootstrap-media-file-fixture",
        "bootstrap-shape-fixture",
    ],
)
def test_each_concrete_interactive_detector_has_success_and_failure_cases(scenario_name) -> None:
    scenario = SCENARIO_REGISTRY.get(scenario_name)
    recipe = scenario.fixture_recipe
    cases = [case for case in CASES if case.scenario_name == scenario_name]
    variants = {
        case.variant
        for case in cases
        if case.dimension == RecipeContractDimension.INTERACTIVE_DETECTOR
    }
    assert variants == {"success", "failure"}
    public_kind = next(iter(recipe.requested_object_types))
    requested = ({"kind": public_kind},)
    assert recipe.compare_capability(requested, requested)["equivalent"] is True
    assert recipe.compare_capability(requested, ())["equivalent"] is False

    observation = _interactive_observation(
        recipe,
        ({"kind": "Outline"}, {"kind": "OE"}, *requested),
        ("Outline", recipe.capability),
        structural_marker_counts=(
            {"ShapeInfo": 1} if recipe.capability == "UIShape" else {}
        ),
    )
    success_report = recipe.authored_content_report(observation)
    assert success_report["passed"] is True
    assert success_report["representation_status"] == (
        "requested_composite_observed"
        if recipe.capability == "UIShape"
        else "requested_kind_observed"
    )
    assert success_report["template_publish_allowed"] is True

    legacy = _interactive_observation(
        recipe,
        ({"type": recipe.capability},),
        (recipe.capability,),
        structural_marker_counts=(
            {"ShapeInfo": 1} if recipe.capability == "UIShape" else {}
        ),
    )
    report = recipe.authored_content_report(legacy)
    assert report["passed"] is False
    assert report["unexpected"] == ["invalid-object-schema"]


def test_user_authored_catalog_requires_ready_evidence_only_and_ambiguity() -> None:
    recipe = SCENARIO_REGISTRY.get("bootstrap-user-authored-fixture").fixture_recipe
    assert isinstance(recipe, UserAuthoredRecipe)
    outcomes = {
        (case.variant, case.expected_outcome)
        for case in CASES
        if case.scenario_name == "bootstrap-user-authored-fixture"
        and case.dimension == RecipeContractDimension.USER_AUTHORED
    }
    assert outcomes == {
        ("ready", ContractOutcome.PASS),
        ("unknown-capability", ContractOutcome.EVIDENCE_ONLY),
        ("ambiguous-instance", ContractOutcome.FAIL_CLOSED),
    }


def test_user_authored_freeze_uses_kind_and_fails_closed_on_unknown_or_legacy_schema() -> None:
    recipe = SCENARIO_REGISTRY.get("bootstrap-user-authored-fixture").fixture_recipe
    stable = _interactive_observation(
        recipe,
        ({"kind": "Outline"}, {"kind": "OE"}),
        ("Outline",),
    )
    ready = recipe.freeze_authored_instance(stable)
    assert ready.state == "ready"
    assert ready.mutation_eligible is True
    assert ready.move_source_deletion_allowed is True

    unknown = _interactive_observation(
        recipe,
        ({"kind": "Outline"}, {"kind": "OE"}, {"kind": "MediaFile"}),
        ("Outline", "MediaFile"),
    )
    evidence_only = recipe.freeze_authored_instance(unknown)
    assert evidence_only.state == "evidence_only"
    assert evidence_only.mutation_eligible is False
    assert evidence_only.move_source_deletion_allowed is False
    assert evidence_only.unknown_capabilities == ("MediaFile",)

    legacy = _interactive_observation(
        recipe,
        ({"type": "Outline"},),
        ("Outline",),
    )
    report = recipe.authored_content_report(legacy)
    assert report["passed"] is False
    legacy_instance = recipe.freeze_authored_instance(legacy)
    assert legacy_instance.state == "evidence_only"
    assert "invalid-object-schema" in legacy_instance.unknown_capabilities


def test_user_authored_consumer_shares_contract_fingerprint_but_requires_exact_instance() -> None:
    bootstrap = SCENARIO_REGISTRY.get("bootstrap-user-authored-fixture").fixture_recipe
    consumer = SCENARIO_REGISTRY.get("user-authored-fixture-consumer").fixture_recipe
    assert bootstrap is not consumer
    assert bootstrap.cache_fingerprint == consumer.cache_fingerprint
    with pytest.raises(RunnerFailure, match="explicit --template-instance-id"):
        consumer.select_template_instance_id(argparse.Namespace())
    assert consumer.select_template_instance_id(
        argparse.Namespace(template_instance_id="authored-" + "a" * 24)
    ) == "authored-" + "a" * 24
