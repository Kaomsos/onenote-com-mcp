"""Decorator registry for public and ``all``-eligible scenarios."""

from __future__ import annotations

import argparse
from typing import TypeVar

from ..base import RuntimeFlags, Scenario
from .dry_run import DryRunCase, DryRunExpectations


ScenarioType = TypeVar("ScenarioType", bound=type[Scenario])


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
        if not scenario.worksite_dry_run_action or not scenario.worksite_dry_run_action.replace(
            "-", ""
        ).isalnum():
            raise ValueError(f"Scenario {scenario.name} has an invalid worksite plan action.")
        if not spec.fixture.creation_tools.issubset(spec.tool_allowlist):
            raise ValueError(
                f"Scenario {scenario.name} fixture creation tools exceed its allowlist."
            )
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
    "SCENARIO_REGISTRY",
    "ScenarioRegistry",
    "get_all_scenario_names",
]
