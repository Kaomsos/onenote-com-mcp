"""Cross-Notebook reconstructive Section Move scenario."""

from .common.config import MOVE_SECTION_TOOLS
from .common.registry import SCENARIO_REGISTRY
from .container_move_scenario import ContainerMoveScenario
from .fixture_recipes.move_section import RECIPE


@SCENARIO_REGISTRY.register
class MoveSectionScenario(ContainerMoveScenario):
    name = "move-section"
    fixture_recipe = RECIPE
    help_text = (
        "GATED: move one disposable Section across Notebooks by verified Copy and "
        "one non-permanent source root deletion."
    )
    resource_type = "section"
    plan_tool = "plan_move_section"
    move_tool = "move_section"
    tool_allowlist = MOVE_SECTION_TOOLS


__all__ = ["MoveSectionScenario"]
