from .common.interactive_copy import InteractiveCopyEvidenceScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.media_file import RECIPE


@SCENARIO_REGISTRY.register
class InteractiveCopyMediaFileScenario(InteractiveCopyEvidenceScenario):
    name = "interactive-copy-media-file"
    help_text = (
        "HUMAN-GATED COPY-ONLY: author or reuse one synthetic MediaFile fixture and compare Copy fidelity."
    )
    fixture_recipe = RECIPE
    include_cross_section_case = True


__all__ = ["InteractiveCopyMediaFileScenario"]
