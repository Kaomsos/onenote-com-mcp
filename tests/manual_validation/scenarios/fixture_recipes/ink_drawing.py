from .interactive import InkDrawingInteractiveFixtureRecipe


class InkDrawingRecipe(InkDrawingInteractiveFixtureRecipe):
    def __init__(self) -> None:
        super().__init__("interactive-copy-ink-drawing")


RECIPE = InkDrawingRecipe()
__all__ = ["InkDrawingRecipe", "RECIPE"]
