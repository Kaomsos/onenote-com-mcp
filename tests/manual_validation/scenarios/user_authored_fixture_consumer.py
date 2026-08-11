import argparse

from .base import Scenario
from ..runtime import InvariantFailure
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.user_authored_consumer import RECIPE


@SCENARIO_REGISTRY.register
class UserAuthoredFixtureConsumerScenario(Scenario):
    name = "user-authored-fixture-consumer"
    help_text = "HUMAN-GATED: live-validate one explicitly selected frozen UserAuthored instance."
    fixture_recipe = RECIPE
    included_in_all = False
    worksite_dry_run_action = "preserve-selected-user-authored-working-copy"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--template-instance-id",
            help="Exact authored-<24 hex> instance ID emitted by the named bootstrap Scenario.",
        )

    async def execute(self, args, options, manifest, *, client, fixture_result):
        selected = self.fixture_recipe.select_template_instance_id(args)
        cache = manifest.get("fixture_cache", {})
        if cache.get("template_instance_id") != selected:
            raise InvariantFailure("Live manifest instance differs from the explicit selection.")
        return {
            "scenario": self.name,
            "status": "passed",
            "template_instance_id": selected,
            "live_materialized_revalidation": True,
            "mutation_eligible": True,
            "working_path": cache.get("working_path"),
            "opened_template": cache.get("opened_template", False),
        }


__all__ = ["UserAuthoredFixtureConsumerScenario"]
