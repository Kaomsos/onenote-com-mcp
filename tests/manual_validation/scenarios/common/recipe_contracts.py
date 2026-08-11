"""Pure, stable Recipe contract-case catalog derived from the sole Scenario registry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from ..fixture_recipes.interactive import InteractiveFixtureRecipe, UserAuthoredRecipe


class RecipeContractDimension(str, Enum):
    OWNERSHIP = "ownership"
    FRESH_DEFAULT = "fresh-default"
    CACHE_COLD = "cache-cold"
    VALIDATED_HIT = "validated-hit"
    INVALIDATION = "invalidation"
    IMMUTABILITY = "immutability"
    MANIFEST = "manifest"
    RESPONSIBILITY = "responsibility"
    MULTI_ROLE = "multi-role"
    INTERACTIVE_BOOTSTRAP = "interactive-bootstrap"
    INTERACTIVE_DETECTOR = "interactive-detector"
    USER_AUTHORED = "user-authored"


class ContractOutcome(str, Enum):
    PASS = "pass"
    FAIL_CLOSED = "fail-closed"
    BOOTSTRAP_REQUIRED = "bootstrap-required"
    EVIDENCE_ONLY = "evidence-only"


@dataclass(frozen=True)
class RecipeContractCase:
    case_id: str
    scenario_name: str
    recipe_name: str
    dimension: RecipeContractDimension
    variant: str
    expected_outcome: ContractOutcome
    expected_roles: tuple[str, ...]

    def __post_init__(self) -> None:
        if re.fullmatch(r"recipe\.[a-z0-9-]+\.[a-z0-9-]+\.[a-z0-9-]+", self.case_id) is None:
            raise ValueError(f"Recipe contract case ID is not stable: {self.case_id}")
        if not self.scenario_name or not self.recipe_name or not self.expected_roles:
            raise ValueError("Recipe contract cases require owned Scenario/Recipe roles.")
        if len(set(self.expected_roles)) != len(self.expected_roles):
            raise ValueError("Recipe contract case roles must be unique.")


BASE_CASES = (
    (RecipeContractDimension.OWNERSHIP, "static", ContractOutcome.PASS),
    (RecipeContractDimension.FRESH_DEFAULT, "zero-cache-io", ContractOutcome.PASS),
    (RecipeContractDimension.CACHE_COLD, "publish-gated", ContractOutcome.PASS),
    (RecipeContractDimension.VALIDATED_HIT, "live-revalidate", ContractOutcome.PASS),
    (RecipeContractDimension.INVALIDATION, "incompatible", ContractOutcome.FAIL_CLOSED),
    (RecipeContractDimension.IMMUTABILITY, "working-only", ContractOutcome.PASS),
    (RecipeContractDimension.MANIFEST, "complete", ContractOutcome.PASS),
    (RecipeContractDimension.RESPONSIBILITY, "sentinel", ContractOutcome.PASS),
)


def required_recipe_contract_cases(registry) -> tuple[RecipeContractCase, ...]:
    cases: list[RecipeContractCase] = []
    for scenario in registry.values():
        recipe = scenario.fixture_recipe
        roles = tuple(role.role for role in recipe.cache_identity.notebook_roles)
        declarations = list(BASE_CASES)
        if len(roles) > 1:
            declarations.extend(
                (
                    (RecipeContractDimension.MULTI_ROLE, "role-collision", ContractOutcome.FAIL_CLOSED),
                    (RecipeContractDimension.MULTI_ROLE, "bundle-validation", ContractOutcome.PASS),
                )
            )
        if isinstance(recipe, InteractiveFixtureRecipe):
            declarations.extend(
                (
                    (
                        RecipeContractDimension.INTERACTIVE_BOOTSTRAP,
                        "ordinary-miss",
                        ContractOutcome.BOOTSTRAP_REQUIRED,
                    ),
                    (
                        RecipeContractDimension.INTERACTIVE_BOOTSTRAP,
                        "checkpoint-failure",
                        ContractOutcome.FAIL_CLOSED,
                    ),
                    (
                        RecipeContractDimension.INTERACTIVE_DETECTOR,
                        "success",
                        ContractOutcome.PASS,
                    ),
                    (
                        RecipeContractDimension.INTERACTIVE_DETECTOR,
                        "failure",
                        ContractOutcome.FAIL_CLOSED,
                    ),
                )
            )
        if isinstance(recipe, UserAuthoredRecipe):
            declarations.extend(
                (
                    (RecipeContractDimension.USER_AUTHORED, "ready", ContractOutcome.PASS),
                    (
                        RecipeContractDimension.USER_AUTHORED,
                        "unknown-capability",
                        ContractOutcome.EVIDENCE_ONLY,
                    ),
                    (
                        RecipeContractDimension.USER_AUTHORED,
                        "ambiguous-instance",
                        ContractOutcome.FAIL_CLOSED,
                    ),
                )
            )
        for dimension, variant, outcome in declarations:
            cases.append(
                RecipeContractCase(
                    case_id=(
                        f"recipe.{recipe.recipe_name}."
                        f"{scenario.name}-{dimension.value}.{variant}"
                    ),
                    scenario_name=scenario.name,
                    recipe_name=recipe.recipe_name,
                    dimension=dimension,
                    variant=variant,
                    expected_outcome=outcome,
                    expected_roles=roles,
                )
            )
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Recipe contract catalog contains duplicate stable case IDs.")
    return tuple(cases)


__all__ = [
    "ContractOutcome",
    "RecipeContractCase",
    "RecipeContractDimension",
    "required_recipe_contract_cases",
]
