from .interactive import InlineEquationInteractiveFixtureRecipe


class InlineEquationCopyRecipe(InlineEquationInteractiveFixtureRecipe):
    bootstrap_scenario_name = "bootstrap-inline-equation-fixture"
    consumer_scenario = True

    def __init__(self) -> None:
        super().__init__(
            "interactive-copy-inline-equation",
            cache_recipe_name=self.bootstrap_scenario_name,
        )


RECIPE = InlineEquationCopyRecipe()
__all__ = ["InlineEquationCopyRecipe", "RECIPE"]
