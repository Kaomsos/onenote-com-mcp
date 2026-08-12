from .common.interactive_copy import InteractiveCopyEvidenceScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.inserted_file_copy import RECIPE


@SCENARIO_REGISTRY.register
class InteractiveCopyInsertedFileScenario(InteractiveCopyEvidenceScenario):
    name = "interactive-copy-inserted-file"
    help_text = (
        "HUMAN-GATED COPY-ONLY: compare one cached synthetic InsertedFile source and target."
    )
    fixture_recipe = RECIPE


__all__ = ["InteractiveCopyInsertedFileScenario"]
