from .interactive import InsertedFileInteractiveFixtureRecipe


class InsertedFileRecipe(InsertedFileInteractiveFixtureRecipe):
    def __init__(self) -> None:
        super().__init__("interactive-copy-inserted-file")


RECIPE = InsertedFileRecipe()
__all__ = ["InsertedFileRecipe", "RECIPE"]
