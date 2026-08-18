from .interactive import MediaFileInteractiveFixtureRecipe


class MediaFileRecipe(MediaFileInteractiveFixtureRecipe):
    def __init__(self) -> None:
        super().__init__("interactive-copy-media-file")


RECIPE = MediaFileRecipe()
__all__ = ["MediaFileRecipe", "RECIPE"]
