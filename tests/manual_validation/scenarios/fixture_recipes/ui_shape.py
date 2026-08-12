from .interactive import UIShapeInteractiveFixtureRecipe


class UIShapeRecipe(UIShapeInteractiveFixtureRecipe):
    bootstrap_scenario_name = "bootstrap-shape-fixture"

    def __init__(self) -> None:
        super().__init__(self.bootstrap_scenario_name)


RECIPE = UIShapeRecipe()
__all__ = ["RECIPE", "UIShapeRecipe"]
