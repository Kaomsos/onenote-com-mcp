from .common.interactive_copy import InteractiveCopyEvidenceScenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.inserted_file import RECIPE


@SCENARIO_REGISTRY.register
class InteractiveCopyInsertedFileScenario(InteractiveCopyEvidenceScenario):
    name = "interactive-copy-inserted-file"
    help_text = (
        "HUMAN-GATED COPY-ONLY: author or reuse one synthetic InsertedFile fixture and compare Copy fidelity."
    )
    fixture_recipe = RECIPE


__all__ = ["InteractiveCopyInsertedFileScenario"]
