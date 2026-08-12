from .common.interactive_copy import InteractiveCopyEvidenceScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.ink_drawing_copy import RECIPE


@SCENARIO_REGISTRY.register
class InteractiveCopyInkDrawingScenario(InteractiveCopyEvidenceScenario):
    name = "interactive-copy-ink-drawing"
    help_text = (
        "HUMAN-GATED COPY-ONLY: compare one cached synthetic InkDrawing source and target."
    )
    fixture_recipe = RECIPE


__all__ = ["InteractiveCopyInkDrawingScenario"]
