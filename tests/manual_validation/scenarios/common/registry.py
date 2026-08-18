"""Decorator registry for public and ``all``-eligible scenarios."""

from __future__ import annotations

import argparse
from typing import TypeVar

from ..base import RuntimeFlags, Scenario
from .dry_run import DryRunCase, DryRunExpectations


ScenarioType = TypeVar("ScenarioType", bound=type[Scenario])


SCENARIO_TOOL_POLICY_REQUIREMENTS: dict[str, frozenset[str]] = {
    "health_check": frozenset(),
    "list_notebooks": frozenset(),
    "get_notebook_metadata": frozenset(),
    "expand_notebook": frozenset(),
    "expand_section_group": frozenset(),
    "expand_section": frozenset(),
    "expand_page": frozenset(),
    "expand_hierarchy": frozenset(),
    "get_hierarchy_path": frozenset(),
    "get_page_xml": frozenset(),
    "get_page_text": frozenset(),
    "get_page_content_objects": frozenset(),
    "get_page_content_object_binary": frozenset(),
    "query_notebook": frozenset(),
    "query_section_group": frozenset(),
    "query_section": frozenset(),
    "query_page": frozenset(),
    "search_pages": frozenset(),
    "get_hyperlink": frozenset(),
    "create_notebook": frozenset({"create_enabled"}),
    "create_section_group": frozenset({"create_enabled"}),
    "create_section": frozenset({"create_enabled"}),
    "create_page": frozenset({"create_enabled", "writes_enabled"}),
    "rename_page": frozenset({"writes_enabled"}),
    "rename_section": frozenset({"writes_enabled"}),
    "rename_section_group": frozenset({"writes_enabled"}),
    "append_page_content": frozenset({"writes_enabled"}),
    "reorder_page": frozenset({"writes_enabled"}),
    "reorder_section": frozenset({"writes_enabled"}),
    "reorder_section_group": frozenset({"writes_enabled"}),
    "sort_children": frozenset({"writes_enabled"}),
    "replace_page_body": frozenset({"writes_enabled", "deletes_enabled"}),
    "add_page_image_from_file": frozenset(
        {"writes_enabled", "local_file_io_enabled"}
    ),
    "delete_page_content_object": frozenset({"deletes_enabled"}),
    "delete_page": frozenset({"deletes_enabled"}),
    "delete_section": frozenset({"deletes_enabled"}),
    "delete_section_group": frozenset({"deletes_enabled"}),
    "reparent_page": frozenset({"writes_enabled", "organize_enabled"}),
    "reparent_section": frozenset({"writes_enabled", "organize_enabled"}),
    "reparent_section_group": frozenset(
        {"writes_enabled", "organize_enabled"}
    ),
    "copy_page": frozenset({"create_enabled", "writes_enabled"}),
    "copy_section": frozenset({"create_enabled", "writes_enabled"}),
    "copy_section_group": frozenset({"create_enabled", "writes_enabled"}),
    "copy_notebook": frozenset({"create_enabled", "writes_enabled"}),
    "move_page": frozenset(
        {"create_enabled", "writes_enabled", "deletes_enabled"}
    ),
    "move_section": frozenset(
        {"create_enabled", "writes_enabled", "deletes_enabled"}
    ),
    "move_section_group": frozenset(
        {"create_enabled", "writes_enabled", "deletes_enabled"}
    ),
    "export_object_to_pdf": frozenset({"local_file_io_enabled"}),
    "navigate_to": frozenset({"ui_control_enabled"}),
    "close_notebook": frozenset({"notebook_lifecycle_enabled"}),
    "request_notebook_sync": frozenset({"notebook_lifecycle_enabled"}),
}

# Kept as a named alias because fixture creation closure is a separately
# reported registration invariant, even though both checks share one catalog.
FIXTURE_TOOL_POLICY_REQUIREMENTS = SCENARIO_TOOL_POLICY_REQUIREMENTS


def _missing_policy_fields(
    tools: set[str],
    policy: object,
) -> tuple[list[str], list[str]]:
    unknown_tools = sorted(tools - SCENARIO_TOOL_POLICY_REQUIREMENTS.keys())
    required_fields = {
        field
        for tool in tools
        for field in SCENARIO_TOOL_POLICY_REQUIREMENTS.get(tool, ())
    }
    missing = sorted(
        field for field in required_fields if not getattr(policy, field)
    )
    return unknown_tools, missing


def _validate_fixture_policy_closure(scenario: Scenario) -> None:
    """Reject a fresh fixture whose declared tools exceed its static policy gates."""

    recipe = scenario.fixture_recipe
    if getattr(recipe, "consumer_scenario", False):
        return
    creation_tools = set(scenario.spec.fixture.creation_tools)
    for role in getattr(recipe, "notebook_roles", ()):
        creation_tools.update(role.profile.creation_tools)
    unknown_tools, missing = _missing_policy_fields(
        creation_tools,
        scenario.spec.policy,
    )
    if unknown_tools:
        raise ValueError(
            f"Scenario {scenario.name} fixture tools have no policy mapping: "
            + ", ".join(unknown_tools)
        )
    if missing:
        raise ValueError(
            f"Scenario {scenario.name} fixture policy is missing required gates: "
            + ", ".join(missing)
        )
    fixture_only_tools = sorted(creation_tools - set(scenario.spec.tool_allowlist))
    if fixture_only_tools:
        raise ValueError(
            f"Scenario {scenario.name} fixture creation tools exceed its allowlist: "
            + ", ".join(fixture_only_tools)
        )


def _validate_scenario_policy_closure(scenario: Scenario) -> None:
    """Reject any allowed tool whose independent policy gates are not enabled."""

    tools = set(scenario.spec.tool_allowlist)
    unknown_tools, missing = _missing_policy_fields(tools, scenario.spec.policy)
    if unknown_tools:
        raise ValueError(
            f"Scenario {scenario.name} allowed tools have no policy mapping: "
            + ", ".join(unknown_tools)
        )
    if missing:
        raise ValueError(
            f"Scenario {scenario.name} policy is missing required gates: "
            + ", ".join(missing)
        )


class ScenarioRegistry:
    """Singleton-friendly class decorator backed by instantiated scenarios."""

    def __init__(self) -> None:
        self._scenarios: dict[str, Scenario] = {}
        self._recipe_ids: set[int] = set()
        self._dry_run_cases: list[DryRunCase] = []
        self._dry_run_case_ids: set[str] = set()
        self._dry_run_documentation_keys: set[str] = set()

    def register(self, scenario_type: ScenarioType) -> ScenarioType:
        """Wrap a Scenario class, register one instance, and preserve the class."""

        if not issubclass(scenario_type, Scenario):
            raise TypeError("Only Scenario subclasses can be registered.")
        scenario = scenario_type()
        if not scenario.name:
            raise ValueError("Scenario classes must declare a non-empty name.")
        if scenario.name in self._scenarios:
            raise ValueError(f"Duplicate scenario registration: {scenario.name}")
        spec = scenario.spec
        if spec.name != scenario.name:
            raise ValueError(
                f"Scenario class/spec name mismatch: {scenario.name} != {spec.name}"
            )
        recipe = getattr(scenario, "fixture_recipe", None)
        if recipe is None:
            raise ValueError(f"Scenario {scenario.name} must own one fixture recipe.")
        if recipe.scenario_name != scenario.name or recipe.profile != spec.fixture:
            raise ValueError(f"Scenario {scenario.name} fixture recipe/profile mismatch.")
        if id(recipe) in self._recipe_ids:
            raise ValueError(f"Fixture recipe instance is already owned: {scenario.name}")
        if not recipe.manifest_keys or any(
            not key or not key.replace("_", "").isalnum() for key in recipe.manifest_keys
        ):
            raise ValueError(f"Scenario {scenario.name} has invalid fixture manifest keys.")
        recipe.validate_registration(spec)
        _validate_fixture_policy_closure(scenario)
        _validate_scenario_policy_closure(scenario)
        if not scenario.worksite_dry_run_action or not scenario.worksite_dry_run_action.replace(
            "-", ""
        ).isalnum():
            raise ValueError(f"Scenario {scenario.name} has an invalid worksite plan action.")
        self._recipe_ids.add(id(recipe))
        cases = scenario.dry_run_cases
        if not cases:
            raise ValueError(f"Scenario {scenario.name} has no dry-run cases.")
        for case in cases:
            if case.scenario_name != scenario.name:
                raise ValueError(f"Dry-run case {case.case_id} belongs to the wrong scenario.")
            if case.case_id in self._dry_run_case_ids:
                raise ValueError(f"Duplicate dry-run case ID: {case.case_id}")
            if (
                case.documentation_key is not None
                and case.documentation_key in self._dry_run_documentation_keys
            ):
                raise ValueError(
                    f"Duplicate dry-run documentation key: {case.documentation_key}"
                )
            self._dry_run_case_ids.add(case.case_id)
            if case.documentation_key is not None:
                self._dry_run_documentation_keys.add(case.documentation_key)
            self._dry_run_cases.append(case)
        self._scenarios[scenario.name] = scenario
        return scenario_type

    @property
    def public_names(self) -> tuple[str, ...]:
        return tuple(self._scenarios)

    @property
    def all_scenario_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, scenario in self._scenarios.items()
            if scenario.included_in_all
        )

    @property
    def dry_run_cases(self) -> tuple[DryRunCase, ...]:
        covered = {case.scenario_name for case in self._dry_run_cases}
        missing = set(self.public_names) - covered
        if missing:
            raise ValueError(f"Public scenarios missing dry-run cases: {sorted(missing)}")
        all_case = DryRunCase(
            case_id="all.default",
            scenario_name="all",
            expected=DryRunExpectations(
                lifecycle="child-isolated",
                expected_mcp_process_starts=0,
            ),
            documentation_key="all.default",
        )
        return (*self._dry_run_cases, all_case)

    def get(self, name: str) -> Scenario:
        try:
            return self._scenarios[name]
        except KeyError as exc:
            raise ValueError(f"Unknown scenario registration: {name}") from exc

    def register_parsers(
        self,
        subparsers: argparse._SubParsersAction,
        runtime_flags: RuntimeFlags,
    ) -> None:
        for scenario in self._scenarios.values():
            scenario.register_parser(subparsers, runtime_flags)

    def values(self) -> tuple[Scenario, ...]:
        return tuple(self._scenarios.values())


SCENARIO_REGISTRY = ScenarioRegistry()


def get_all_scenario_names() -> tuple[str, ...]:
    return SCENARIO_REGISTRY.all_scenario_names


__all__ = [
    "FIXTURE_TOOL_POLICY_REQUIREMENTS",
    "SCENARIO_TOOL_POLICY_REQUIREMENTS",
    "SCENARIO_REGISTRY",
    "ScenarioRegistry",
    "_validate_fixture_policy_closure",
    "_validate_scenario_policy_closure",
    "get_all_scenario_names",
]
