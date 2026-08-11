from .base import Scenario
from .common.registry import SCENARIO_REGISTRY
from .fixture_recipes.cache_invalidation import RECIPE


@SCENARIO_REGISTRY.register
class CacheInvalidationScenario(Scenario):
    name = "cache-invalidation"
    help_text = "HUMAN-GATED: invalidate and rebuild only this fixed Recipe cache entry."
    fixture_recipe = RECIPE
    included_in_all = False
    cache_invalidation_probe = True
    worksite_dry_run_action = "preserve-owned-cache-invalidation-working-copy"

    async def execute(self, args, options, manifest, *, client, fixture_result):
        cache = manifest.get("fixture_cache", {})
        return {
            "scenario": self.name,
            "status": "passed",
            "fixed_owned_entry_only": True,
            "fingerprint": self.fixture_recipe.cache_fingerprint,
            "template_instance_id": self.fixture_recipe.default_template_instance_id,
            "cache_decision": cache.get("decision"),
            "opened_template": cache.get("opened_template", False),
        }


__all__ = ["CacheInvalidationScenario"]
