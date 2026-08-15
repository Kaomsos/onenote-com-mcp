"""Cross-Notebook reconstructive SectionGroup Move scenario."""

from .common.config import MOVE_SECTION_GROUP_TOOLS
from .common.registry import SCENARIO_REGISTRY
from .container_move_scenario import ContainerMoveScenario
from .fixture_recipes.move_section_group import RECIPE


@SCENARIO_REGISTRY.register
class MoveSectionGroupScenario(ContainerMoveScenario):
    name = "move-section-group"
    included_in_all = True
    fixture_recipe = RECIPE
    help_text = (
        "GATED: move one disposable SectionGroup tree across Notebooks by verified Copy "
        "and one non-permanent source root deletion."
    )
    resource_type = "section_group"
    move_tool = "move_section_group"
    tool_allowlist = MOVE_SECTION_GROUP_TOOLS


__all__ = ["MoveSectionGroupScenario"]
