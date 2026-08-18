from .interactive import UserAuthoredRecipe


class InteractiveUserAuthoredRecipe(UserAuthoredRecipe):
    recipe_version = 4

    def __init__(self) -> None:
        super().__init__("interactive-user-authored-fixture")


RECIPE = InteractiveUserAuthoredRecipe()
__all__ = ["InteractiveUserAuthoredRecipe", "RECIPE"]
