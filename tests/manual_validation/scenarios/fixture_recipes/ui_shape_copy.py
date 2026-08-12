from .interactive import UIShapeInteractiveFixtureRecipe


class UIShapeCopyRecipe(UIShapeInteractiveFixtureRecipe):
    bootstrap_scenario_name = "bootstrap-shape-fixture"
    consumer_scenario = True

    def __init__(self) -> None:
        super().__init__(
            "interactive-copy-ui-shape",
            cache_recipe_name=self.bootstrap_scenario_name,
        )


RECIPE = UIShapeCopyRecipe()
__all__ = ["RECIPE", "UIShapeCopyRecipe"]
