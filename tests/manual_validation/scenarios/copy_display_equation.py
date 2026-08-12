"""Programmatic DisplayEquation Copy scenario."""

from __future__ import annotations

from .common.copy_runtime import execute_copy_display_equation
from .common.registry import SCENARIO_REGISTRY
from .copy_scenario_base import CopyScenario
from .fixture_recipes.display_equation_copy import RECIPE


@SCENARIO_REGISTRY.register
class CopyDisplayEquationScenario(CopyScenario):
    name = "copy-display-equation"
    fixture_recipe = RECIPE
    included_in_all = False
    help_text = (
        "GATED: programmatically build one standalone DisplayEquation, copy it through "
        "a fixed three-hop chain, require bounded COM span/break normalization, and "
        "clean up all targets by default."
    )

    execute_copy = staticmethod(execute_copy_display_equation)


__all__ = ["CopyDisplayEquationScenario"]
