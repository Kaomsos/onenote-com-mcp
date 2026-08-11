from .interactive import InkDrawingInteractiveFixtureRecipe


class InkDrawingRecipe(InkDrawingInteractiveFixtureRecipe):
    bootstrap_scenario_name = "bootstrap-ink-drawing-fixture"

    def __init__(self) -> None:
        super().__init__(self.bootstrap_scenario_name)


RECIPE = InkDrawingRecipe()
__all__ = ["InkDrawingRecipe", "RECIPE"]
