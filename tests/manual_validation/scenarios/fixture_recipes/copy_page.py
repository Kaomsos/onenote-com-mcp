from .layered_copy import LayeredCopyFixtureRecipe, LayeredFixtureConfig, LayeredFixtureKind

class CopyPageFixtureRecipe(LayeredCopyFixtureRecipe):
    def __init__(self) -> None:
        super().__init__("copy-page", LayeredFixtureConfig(LayeredFixtureKind.PAGE))

RECIPE = CopyPageFixtureRecipe()
__all__ = ["CopyPageFixtureRecipe", "RECIPE"]
