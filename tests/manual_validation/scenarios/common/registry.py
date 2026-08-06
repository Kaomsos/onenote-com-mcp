"""Decorator registry for public and ``all``-eligible scenarios."""

from __future__ import annotations

import argparse
from typing import TypeVar

from ..base import RuntimeFlags, Scenario


ScenarioType = TypeVar("ScenarioType", bound=type[Scenario])


class ScenarioRegistry:
    """Singleton-friendly class decorator backed by instantiated scenarios."""

    def __init__(self) -> None:
        self._scenarios: dict[str, Scenario] = {}

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
        self._scenarios[scenario.name] = scenario
        return scenario_type

    @property
    def public_names(self) -> tuple[str, ...]:
        return tuple(self._scenarios)

    @property
    def registered_test_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, scenario in self._scenarios.items()
            if scenario.registered_for_all
        )

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


def get_registered_test_scenarios() -> tuple[str, ...]:
    return SCENARIO_REGISTRY.registered_test_names


__all__ = [
    "SCENARIO_REGISTRY",
    "ScenarioRegistry",
    "get_registered_test_scenarios",
]
