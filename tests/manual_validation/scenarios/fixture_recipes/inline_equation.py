from .interactive import InlineEquationInteractiveFixtureRecipe


class InlineEquationRecipe(InlineEquationInteractiveFixtureRecipe):
    def __init__(self) -> None:
        super().__init__("interactive-copy-inline-equation")


RECIPE = InlineEquationRecipe()
__all__ = ["InlineEquationRecipe", "RECIPE"]
