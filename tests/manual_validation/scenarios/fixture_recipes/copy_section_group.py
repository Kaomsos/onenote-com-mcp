from .layered_copy import LayeredCopyFixtureRecipe, LayeredFixtureConfig, LayeredFixtureKind

class CopySectionGroupFixtureRecipe(LayeredCopyFixtureRecipe):
    def __init__(self) -> None:
        super().__init__("copy-section-group", LayeredFixtureConfig(LayeredFixtureKind.SECTION_GROUP))

RECIPE = CopySectionGroupFixtureRecipe()
__all__ = ["CopySectionGroupFixtureRecipe", "RECIPE"]
