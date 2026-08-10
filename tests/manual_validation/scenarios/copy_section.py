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
        "GATED: create and copy the prepared Section; clean up by default or "
        "preserve the verified worksite for inspection."
    )

    execute_copy = staticmethod(execute_copy)


__all__ = ["CopySectionScenario"]
