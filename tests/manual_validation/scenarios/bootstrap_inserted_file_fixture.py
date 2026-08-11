from .common.interactive_bootstrap import InteractiveBootstrapScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.inserted_file import RECIPE


@SCENARIO_REGISTRY.register
class BootstrapInsertedFileFixtureScenario(InteractiveBootstrapScenario):
    name = "bootstrap-inserted-file-fixture"
    help_text = "HUMAN-GATED: author and freeze one synthetic InsertedFile fixture."
    fixture_recipe = RECIPE


__all__ = ["BootstrapInsertedFileFixtureScenario"]
