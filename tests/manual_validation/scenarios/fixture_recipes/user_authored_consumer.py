from .user_authored import BoundedUserAuthoredRecipe


RECIPE = BoundedUserAuthoredRecipe(
    "user-authored-fixture-consumer",
    consumer=True,
)
__all__ = ["RECIPE"]
