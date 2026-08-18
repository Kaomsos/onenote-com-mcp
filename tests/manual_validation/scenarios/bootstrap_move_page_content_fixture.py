"""Human-gated bootstrap for representative Page Move content."""

from .common.interactive_bootstrap import InteractiveBootstrapScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.move_page_content import BOOTSTRAP_RECIPE


@SCENARIO_REGISTRY.register
class BootstrapMovePageContentFixtureScenario(InteractiveBootstrapScenario):
    name = "bootstrap-move-page-content-fixture"
    help_text = (
        "HUMAN-GATED: author and freeze one representative real-content leaf Page "
        "plus an isolated Move destination."
    )
    fixture_recipe = BOOTSTRAP_RECIPE


__all__ = ["BootstrapMovePageContentFixtureScenario"]
