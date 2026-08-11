from .container_move import ContainerMoveFixtureRecipe


class MoveSectionGroupFixtureRecipe(ContainerMoveFixtureRecipe):
    def __init__(self) -> None:
        super().__init__("move-section-group", "section_group")


RECIPE = MoveSectionGroupFixtureRecipe()
__all__ = ["MoveSectionGroupFixtureRecipe", "RECIPE"]
