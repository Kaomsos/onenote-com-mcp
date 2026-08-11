from .layered_copy import LayeredCopyFixtureRecipe, LayeredFixtureConfig, LayeredFixtureKind

class MovePageFixtureRecipe(LayeredCopyFixtureRecipe):
    recipe_version = 3

    def __init__(self) -> None:
        super().__init__("move-page", LayeredFixtureConfig(LayeredFixtureKind.MOVE, parent_title="Disposable-Page"))

RECIPE = MovePageFixtureRecipe()
__all__ = ["MovePageFixtureRecipe", "RECIPE"]
