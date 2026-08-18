from .interactive import UIShapeInteractiveFixtureRecipe


class UIShapeRecipe(UIShapeInteractiveFixtureRecipe):
    def __init__(self) -> None:
        super().__init__("interactive-copy-ui-shape")


RECIPE = UIShapeRecipe()
__all__ = ["RECIPE", "UIShapeRecipe"]
