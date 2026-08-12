from .common.interactive_bootstrap import InteractiveBootstrapScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.ui_shape import RECIPE


@SCENARIO_REGISTRY.register
class BootstrapShapeFixtureScenario(InteractiveBootstrapScenario):
    name = "bootstrap-shape-fixture"
    help_text = (
        "HUMAN-GATED: author and freeze one synthetic UI Shape fixture."
    )
    fixture_recipe = RECIPE


__all__ = ["BootstrapShapeFixtureScenario"]
