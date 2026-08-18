from .common.interactive_copy import InteractiveCopyEvidenceScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.ui_shape import RECIPE


@SCENARIO_REGISTRY.register
class InteractiveCopyUIShapeScenario(InteractiveCopyEvidenceScenario):
    name = "interactive-copy-ui-shape"
    help_text = (
        "HUMAN-GATED COPY-ONLY: author or reuse one synthetic UI Shape fixture and compare Copy fidelity."
    )
    fixture_recipe = RECIPE


__all__ = ["InteractiveCopyUIShapeScenario"]
