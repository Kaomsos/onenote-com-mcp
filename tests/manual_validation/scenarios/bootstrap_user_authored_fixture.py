from .common.interactive_bootstrap import InteractiveBootstrapScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.user_authored import RECIPE


@SCENARIO_REGISTRY.register
class BootstrapUserAuthoredFixtureScenario(InteractiveBootstrapScenario):
    name = "bootstrap-user-authored-fixture"
    help_text = "HUMAN-GATED: freeze bounded synthetic user-authored zones as a template instance."
    fixture_recipe = RECIPE


__all__ = ["BootstrapUserAuthoredFixtureScenario"]
