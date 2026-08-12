from .common.interactive_bootstrap import InteractiveBootstrapScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.inline_equation import RECIPE


@SCENARIO_REGISTRY.register
class BootstrapInlineEquationFixtureScenario(InteractiveBootstrapScenario):
    name = "bootstrap-inline-equation-fixture"
    help_text = (
        "HUMAN-GATED: inspect and freeze one automatically generated inline equation."
    )
    fixture_recipe = RECIPE


__all__ = ["BootstrapInlineEquationFixtureScenario"]
