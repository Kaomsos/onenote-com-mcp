"""Page Copy scenario."""

from __future__ import annotations

from .common.copy_runtime import execute_copy_page
from .copy_scenario_base import CopyScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.copy_page import RECIPE


@SCENARIO_REGISTRY.register
class CopyPageScenario(CopyScenario):
    name = "copy-page"
    fixture_recipe = RECIPE
    help_text = (
        "GATED: verify both default root-only Page Copy and explicit full-subtree Copy; "
        "clean up both targets by default or preserve them together for UI inspection."
    )

    execute_copy = staticmethod(execute_copy_page)

__all__ = ["CopyPageScenario"]
