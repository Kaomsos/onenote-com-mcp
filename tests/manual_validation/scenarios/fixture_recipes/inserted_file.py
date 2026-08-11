from .interactive import InsertedFileInteractiveFixtureRecipe


class InsertedFileRecipe(InsertedFileInteractiveFixtureRecipe):
    bootstrap_scenario_name = "bootstrap-inserted-file-fixture"

    def __init__(self) -> None:
        super().__init__(self.bootstrap_scenario_name)


RECIPE = InsertedFileRecipe()
__all__ = ["InsertedFileRecipe", "RECIPE"]
