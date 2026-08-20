"""Private test helper for Recipe.validate() listification.

This module lives under tests/manual_validation/tests/ so it is not a
validation runtime dependency. Callers assemble their own evidence and args.
"""

from __future__ import annotations

from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY


def recipe_validate_checks(scenario_name, context, build_result):
    return list(
        SCENARIO_REGISTRY.get(scenario_name).fixture_recipe.validate(
            context,
            build_result,
        )
    )
