from .interactive import InsertedFileInteractiveFixtureRecipe


class InsertedFileConsumerRecipe(InsertedFileInteractiveFixtureRecipe):
    bootstrap_scenario_name = "bootstrap-inserted-file-fixture"
    consumer_scenario = True

    def __init__(self) -> None:
        super().__init__(
            "inserted-file-fixture-consumer",
            cache_recipe_name=self.bootstrap_scenario_name,
        )


RECIPE = InsertedFileConsumerRecipe()
__all__ = ["InsertedFileConsumerRecipe", "RECIPE"]
