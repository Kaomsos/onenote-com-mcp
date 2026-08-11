from .container_move import ContainerMoveFixtureRecipe


class MoveSectionFixtureRecipe(ContainerMoveFixtureRecipe):
    def __init__(self) -> None:
        super().__init__("move-section", "section")


RECIPE = MoveSectionFixtureRecipe()
__all__ = ["MoveSectionFixtureRecipe", "RECIPE"]
