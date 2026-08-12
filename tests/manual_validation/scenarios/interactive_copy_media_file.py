from .common.interactive_copy import InteractiveCopyEvidenceScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.media_file_copy import RECIPE


@SCENARIO_REGISTRY.register
class InteractiveCopyMediaFileScenario(InteractiveCopyEvidenceScenario):
    name = "interactive-copy-media-file"
    help_text = (
        "HUMAN-GATED COPY-ONLY: compare one cached synthetic MediaFile source and target."
    )
    fixture_recipe = RECIPE
    include_cross_section_case = True


__all__ = ["InteractiveCopyMediaFileScenario"]
