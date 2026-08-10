from .layered_copy import LayeredCopyFixtureRecipe, LayeredFixtureConfig, LayeredFixtureKind

class CopySectionFixtureRecipe(LayeredCopyFixtureRecipe):
    def __init__(self) -> None:
        super().__init__("copy-section", LayeredFixtureConfig(LayeredFixtureKind.SECTION))

RECIPE = CopySectionFixtureRecipe()
__all__ = ["CopySectionFixtureRecipe", "RECIPE"]
