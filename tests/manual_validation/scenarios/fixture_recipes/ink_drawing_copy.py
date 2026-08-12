from .interactive import InkDrawingInteractiveFixtureRecipe


class InkDrawingCopyRecipe(InkDrawingInteractiveFixtureRecipe):
    bootstrap_scenario_name = "bootstrap-ink-drawing-fixture"
    consumer_scenario = True

    def __init__(self) -> None:
        super().__init__(
            "interactive-copy-ink-drawing",
            cache_recipe_name=self.bootstrap_scenario_name,
        )


RECIPE = InkDrawingCopyRecipe()
__all__ = ["InkDrawingCopyRecipe", "RECIPE"]
