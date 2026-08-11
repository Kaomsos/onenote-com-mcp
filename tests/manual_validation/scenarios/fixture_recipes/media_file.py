from .interactive import MediaFileInteractiveFixtureRecipe


class MediaFileRecipe(MediaFileInteractiveFixtureRecipe):
    bootstrap_scenario_name = "bootstrap-media-file-fixture"

    def __init__(self) -> None:
        super().__init__(self.bootstrap_scenario_name)


RECIPE = MediaFileRecipe()
__all__ = ["MediaFileRecipe", "RECIPE"]
