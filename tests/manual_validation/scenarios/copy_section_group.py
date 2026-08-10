"""Section Group Copy scenario."""

from __future__ import annotations

from .common.copy_runtime import execute_copy
from .copy_scenario_base import CopyScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.copy_section_group import RECIPE


@SCENARIO_REGISTRY.register
class CopySectionGroupScenario(CopyScenario):
    name = "copy-section-group"
    fixture_recipe = RECIPE
    help_text = (
        "GATED: create and copy the prepared Section Group; clean up by default or "
        "preserve the verified worksite for inspection."
    )

    execute_copy = staticmethod(execute_copy)


__all__ = ["CopySectionGroupScenario"]
