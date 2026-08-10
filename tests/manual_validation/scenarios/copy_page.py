"""Page Copy scenario."""

from __future__ import annotations

from .common.copy_runtime import execute_copy
from .copy_scenario_base import CopyScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.copy_page import RECIPE


@SCENARIO_REGISTRY.register
class CopyPageScenario(CopyScenario):
    name = "copy-page"
    fixture_recipe = RECIPE
    help_text = (
        "GATED: create and copy the prepared Page subtree; clean up by default or "
        "preserve the verified worksite for inspection."
    )

    execute_copy = staticmethod(execute_copy)

__all__ = ["CopyPageScenario"]
