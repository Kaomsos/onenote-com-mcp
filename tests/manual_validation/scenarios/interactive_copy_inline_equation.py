from .common.interactive_copy import InteractiveCopyEvidenceScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.inline_equation import RECIPE


@SCENARIO_REGISTRY.register
class InteractiveCopyInlineEquationScenario(InteractiveCopyEvidenceScenario):
    name = "interactive-copy-inline-equation"
    help_text = (
        "HUMAN-GATED COPY-ONLY: check whether an inline equation gains a blank line."
    )
    fixture_recipe = RECIPE


__all__ = ["InteractiveCopyInlineEquationScenario"]
