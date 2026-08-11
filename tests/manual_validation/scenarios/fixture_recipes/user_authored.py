from .interactive import UserAuthoredRecipe


class BoundedUserAuthoredRecipe(UserAuthoredRecipe):
    bootstrap_scenario_name = "bootstrap-user-authored-fixture"

    def __init__(self, scenario_name: str | None = None, *, consumer: bool = False) -> None:
        self.consumer_scenario = consumer
        super().__init__(
            scenario_name or self.bootstrap_scenario_name,
            cache_recipe_name="bounded-user-authored-fixture",
        )


RECIPE = BoundedUserAuthoredRecipe()
__all__ = ["BoundedUserAuthoredRecipe", "RECIPE"]
