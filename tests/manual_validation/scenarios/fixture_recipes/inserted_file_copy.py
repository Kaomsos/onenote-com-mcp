from .interactive import InsertedFileInteractiveFixtureRecipe


class InsertedFileCopyRecipe(InsertedFileInteractiveFixtureRecipe):
    bootstrap_scenario_name = "bootstrap-inserted-file-fixture"
    consumer_scenario = True

    def __init__(self) -> None:
        super().__init__(
            "interactive-copy-inserted-file",
            cache_recipe_name=self.bootstrap_scenario_name,
        )


RECIPE = InsertedFileCopyRecipe()
__all__ = ["InsertedFileCopyRecipe", "RECIPE"]
