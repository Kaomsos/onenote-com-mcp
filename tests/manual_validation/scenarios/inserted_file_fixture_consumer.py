from .base import Scenario
from ..runtime import InvariantFailure
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.inserted_file_consumer import RECIPE


@SCENARIO_REGISTRY.register
class InsertedFileFixtureConsumerScenario(Scenario):
    name = "inserted-file-fixture-consumer"
    help_text = (
        "CACHE-ONLY: materialize and programmatically live-validate the named "
        "InsertedFile fixture."
    )
    fixture_recipe = RECIPE
    included_in_all = False
    worksite_dry_run_action = "preserve-live-validated-inserted-file-working-copy"

    async def execute(self, args, options, manifest, *, client, fixture_result):
        cache = manifest.get("fixture_cache", {})
        if cache.get("template_instance_id") != self.fixture_recipe.default_template_instance_id:
            raise InvariantFailure("Live manifest instance differs from the InsertedFile Recipe.")
        validation = cache.get("interactive_live_validation", {})
        if validation.get("passed") is not True:
            raise InvariantFailure("Cached InsertedFile did not pass live authored-content validation.")
        return {
            "scenario": self.name,
            "status": "passed",
            "template_instance_id": cache["template_instance_id"],
            "live_materialized_revalidation": True,
            "observed": dict(validation.get("observed", {})),
            "representation_status": validation.get("representation_status"),
            "working_path": cache.get("working_path"),
            "opened_template": cache.get("opened_template", False),
        }


__all__ = ["InsertedFileFixtureConsumerScenario"]
