import argparse

from .base import Scenario
from ..runtime import InvariantFailure
from .common.interactive_bootstrap import InteractiveBootstrapScenarioMixin
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.user_authored import RECIPE


@SCENARIO_REGISTRY.register
class InteractiveUserAuthoredFixtureScenario(InteractiveBootstrapScenarioMixin, Scenario):
    name = "interactive-user-authored-fixture"
    help_text = (
        "HUMAN-GATED: author or reuse one bounded UserAuthored template and live-validate it."
    )
    fixture_recipe = RECIPE
    included_in_all = False
    worksite_dry_run_action = "preserve-selected-user-authored-working-copy"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        InteractiveBootstrapScenarioMixin.add_arguments(self, parser)
        parser.add_argument(
            "--template-instance-id",
            help=(
                "Exact authored-<24 hex> instance ID for --use-cache; "
                "omit on fresh runs or when exactly one ready template exists."
            ),
        )

    async def execute(self, args, options, manifest, *, client, fixture_result):
        del client, fixture_result
        cache = manifest.get("fixture_cache", {})
        selected = str(cache.get("template_instance_id", "") or "")
        if not selected:
            raise InvariantFailure("Live manifest is missing the resolved template instance.")
        explicit = str(getattr(args, "template_instance_id", "") or "")
        if explicit and explicit != selected:
            raise InvariantFailure("Live manifest instance differs from the explicit instance.")
        template_state = str(cache.get("template_state", "") or "")
        mutation_eligible = cache.get("mutation_eligible")
        if template_state not in {"ready", "evidence_only"} or not isinstance(
            mutation_eligible, bool
        ):
            raise InvariantFailure("Live manifest is missing authored template eligibility.")
        if mutation_eligible is not (template_state == "ready"):
            raise InvariantFailure("Live authored template state and eligibility disagree.")
        roles = cache.get("roles", {})
        source_cache = roles.get("source", {}) if isinstance(roles, dict) else {}
        return {
            "scenario": self.name,
            "status": "passed",
            "template_instance_id": selected,
            "live_materialized_revalidation": True,
            "template_state": template_state,
            "mutation_eligible": mutation_eligible,
            "working_path": source_cache.get("working_path"),
            "opened_template": cache.get("opened_template", False),
        }


__all__ = ["InteractiveUserAuthoredFixtureScenario"]
