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
        "GATED: copy one source Page across same-Section, cross-Section, and "
        "cross-Notebook destinations with and without descendants; clean up all six "
        "targets by default or preserve the two-Notebook worksite for UI inspection."
    )

    execute_copy = staticmethod(execute_copy_page)

__all__ = ["CopyPageScenario"]
