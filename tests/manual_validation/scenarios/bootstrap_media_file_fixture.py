from .common.interactive_bootstrap import InteractiveBootstrapScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.media_file import RECIPE


@SCENARIO_REGISTRY.register
class BootstrapMediaFileFixtureScenario(InteractiveBootstrapScenario):
    name = "bootstrap-media-file-fixture"
    help_text = "HUMAN-GATED: author and freeze one synthetic MediaFile fixture."
    fixture_recipe = RECIPE


__all__ = ["BootstrapMediaFileFixtureScenario"]
