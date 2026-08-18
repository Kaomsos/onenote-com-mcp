from .common.interactive_copy import InteractiveCopyEvidenceScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.ink_drawing import RECIPE


@SCENARIO_REGISTRY.register
class InteractiveCopyInkDrawingScenario(InteractiveCopyEvidenceScenario):
    name = "interactive-copy-ink-drawing"
    help_text = (
        "HUMAN-GATED: author or reuse one synthetic InkDrawing fixture and compare Copy fidelity."
    )
    fixture_recipe = RECIPE


__all__ = ["InteractiveCopyInkDrawingScenario"]
