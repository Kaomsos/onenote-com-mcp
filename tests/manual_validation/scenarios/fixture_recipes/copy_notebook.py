from .layered_copy import LayeredCopyFixtureRecipe, LayeredFixtureConfig, LayeredFixtureKind

class CopyNotebookFixtureRecipe(LayeredCopyFixtureRecipe):
    def __init__(self) -> None:
        super().__init__("copy-notebook", LayeredFixtureConfig(LayeredFixtureKind.NOTEBOOK))

RECIPE = CopyNotebookFixtureRecipe()
__all__ = ["CopyNotebookFixtureRecipe", "RECIPE"]
