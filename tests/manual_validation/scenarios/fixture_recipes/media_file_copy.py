from .interactive import MediaFileInteractiveFixtureRecipe


class MediaFileCopyRecipe(MediaFileInteractiveFixtureRecipe):
    bootstrap_scenario_name = "bootstrap-media-file-fixture"
    consumer_scenario = True

    def __init__(self) -> None:
        super().__init__(
            "interactive-copy-media-file",
            cache_recipe_name=self.bootstrap_scenario_name,
        )


RECIPE = MediaFileCopyRecipe()
__all__ = ["MediaFileCopyRecipe", "RECIPE"]
