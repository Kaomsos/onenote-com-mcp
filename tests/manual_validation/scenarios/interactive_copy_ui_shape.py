from .common.interactive_copy import InteractiveCopyEvidenceScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.ui_shape_copy import RECIPE


@SCENARIO_REGISTRY.register
class InteractiveCopyUIShapeScenario(InteractiveCopyEvidenceScenario):
    name = "interactive-copy-ui-shape"
    help_text = (
        "HUMAN-GATED COPY-ONLY: compare one cached synthetic UI Shape source and target."
    )
    fixture_recipe = RECIPE


__all__ = ["InteractiveCopyUIShapeScenario"]
