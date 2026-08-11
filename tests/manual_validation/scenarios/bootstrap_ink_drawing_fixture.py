from .common.interactive_bootstrap import InteractiveBootstrapScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.ink_drawing import RECIPE


@SCENARIO_REGISTRY.register
class BootstrapInkDrawingFixtureScenario(InteractiveBootstrapScenario):
    name = "bootstrap-ink-drawing-fixture"
    help_text = "HUMAN-GATED: author and freeze one synthetic InkDrawing fixture."
    fixture_recipe = RECIPE


__all__ = ["BootstrapInkDrawingFixtureScenario"]
