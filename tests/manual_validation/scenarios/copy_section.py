"""Section Copy scenario."""

from __future__ import annotations

from .common.copy_runtime import execute_copy
from .copy_scenario_base import CopyScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.copy_section import RECIPE


@SCENARIO_REGISTRY.register
class CopySectionScenario(CopyScenario):
    name = "copy-section"
    fixture_recipe = RECIPE
    help_text = (
        "GATED: copy one prepared Section inside its source Notebook and across to a "
        "destination Notebook; clean up by default or preserve both verified targets."
    )

    execute_copy = staticmethod(execute_copy)


__all__ = ["CopySectionScenario"]
