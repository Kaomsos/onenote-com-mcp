from .interactive import InlineEquationInteractiveFixtureRecipe


class InlineEquationRecipe(InlineEquationInteractiveFixtureRecipe):
    bootstrap_scenario_name = "bootstrap-inline-equation-fixture"

    def __init__(self) -> None:
        super().__init__(self.bootstrap_scenario_name)


RECIPE = InlineEquationRecipe()
__all__ = ["InlineEquationRecipe", "RECIPE"]
