"""Small base class for statically owned fixture recipes."""

from __future__ import annotations

import argparse
from typing import Any

from ..common.fixture_models import FixtureBuildResult, FixtureValidationContext
from ..common.specs import FixtureProfile, ScenarioSpec, get_scenario_spec


class RecipeBase:
    scenario_name: str
    manifest_keys: frozenset[str]

    def __init__(self, scenario_name: str, manifest_keys: frozenset[str] | None = None) -> None:
        self.scenario_name = scenario_name
        self.profile: FixtureProfile = get_scenario_spec(scenario_name).fixture
        self.manifest_keys = manifest_keys or frozenset(self.profile.manifest_keys)

    def required_manifest_keys(self, args: argparse.Namespace) -> frozenset[str]:
        return self.manifest_keys

    def validate_registration(self, spec: ScenarioSpec) -> None:
        if self.scenario_name != spec.name or self.profile != spec.fixture:
            raise ValueError(f"Fixture recipe/profile mismatch: {self.scenario_name}")
        if self.manifest_keys != frozenset(spec.fixture.manifest_keys):
            raise ValueError(
                f"Fixture recipe manifest keys differ from profile: {self.scenario_name}"
            )

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        raise NotImplementedError


def evidence(build: FixtureBuildResult, key: str) -> dict[str, Any] | None:
    value = build.evidence.get(key)
    return value if isinstance(value, dict) else None


__all__ = ["RecipeBase", "evidence"]
