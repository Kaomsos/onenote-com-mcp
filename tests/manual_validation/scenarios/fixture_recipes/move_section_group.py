from .container_move import ContainerMoveFixtureRecipe
from .recipe_base import NESTED_SECTION_CACHE_UNSAFE_REASON


class MoveSectionGroupFixtureRecipe(ContainerMoveFixtureRecipe):
    supports_cache = False
    fresh_only_reason = NESTED_SECTION_CACHE_UNSAFE_REASON

    def __init__(self) -> None:
        super().__init__("move-section-group", "section_group")


RECIPE = MoveSectionGroupFixtureRecipe()
__all__ = ["MoveSectionGroupFixtureRecipe", "RECIPE"]
