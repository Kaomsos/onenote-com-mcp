"""Class contract shared by executable manual-validation scenarios."""

from __future__ import annotations

import argparse
from typing import Any, Callable

from ..mcp_stdio_client import MCPStdioClient
from ..runtime import RuntimeOptions
from .fixture_recipes.recipe_base import RecipeBase
from .common.dry_run import DryRunCase, DryRunExpectations, DryRunVariant
from .common.specs import ScenarioSpec, get_scenario_spec


RuntimeFlags = Callable[..., None]


class Scenario:
    """One independently runnable isolated scenario suite."""

    name = ""
    help_text = ""
    timeout_default = 180
    included_in_all = False
    capability_assessment: dict[str, str] | None = None
    fixture_recipe: RecipeBase
    dry_run_variants: tuple[DryRunVariant, ...] = ()
    worksite_dry_run_action = "preserve-verified-worksite"
    cache_invalidation_probe = False
    requires_lifecycle_wrappers = False
    production_close_handoff = False
    requires_index_activation_checkpoint = False

    @property
    def spec(self) -> ScenarioSpec:
        return get_scenario_spec(self.name)

    @property
    def fixture_profile(self):
        return self.fixture_recipe.profile

    @property
    def dry_run_cases(self) -> tuple[DryRunCase, ...]:
        no_cache_processes = (
            0 if getattr(self.fixture_recipe, "consumer_scenario", False) else 1
        )
        cases = [
            DryRunCase(
                case_id=f"{self.name}.default",
                scenario_name=self.name,
                expected=DryRunExpectations(
                    expected_mcp_process_starts=no_cache_processes
                ),
                documentation_key=f"{self.name}.default",
            ),
            DryRunCase(
                case_id=f"{self.name}.keep-worksite",
                scenario_name=self.name,
                scenario_args=("--keep-worksite",),
                expected=DryRunExpectations(
                    lifecycle="keep",
                    expected_mcp_process_starts=no_cache_processes,
                ),
            ),
        ]
        if getattr(self.fixture_recipe, "supports_cache", True):
            cases.append(
                DryRunCase(
                    case_id=f"{self.name}.use-cache",
                    scenario_name=self.name,
                    scenario_args=("--use-cache",),
                    expected=DryRunExpectations(
                        expected_mcp_process_starts=(
                            0
                            if getattr(
                                self.fixture_recipe,
                                "representation_discovery_only",
                                False,
                            )
                            else 1
                        )
                    ),
                )
            )
        cases.extend(
            DryRunCase(
                case_id=f"{self.name}.{variant.case_suffix}",
                scenario_name=self.name,
                scenario_args=variant.scenario_args,
                expected=variant.expectations,
                documentation_key=variant.documentation_key,
            )
            for variant in self.dry_run_variants
        )
        return tuple(cases)

    def runtime_spec(self, args: argparse.Namespace) -> ScenarioSpec:
        """Return the fixed scenario spec selected by explicit CLI mode."""

        return self.spec

    def register_parser(
        self,
        subparsers: argparse._SubParsersAction,
        runtime_flags: RuntimeFlags,
    ) -> None:
        parser = subparsers.add_parser(self.name, help=self.help_text)
        self.add_arguments(parser)
        parser.add_argument(
            "--notebook-label",
            help=(
                "Optional lowercase kebab-case label inside the canonical validation "
                "Notebook name; defaults to the scenario name."
            ),
        )
        parser.add_argument(
            "--notebook-name",
            help=(
                "Deprecated alias for --notebook-label; arbitrary complete Notebook "
                "names are no longer accepted."
            ),
        )
        parser.add_argument(
            "--keep-notebook",
            action="store_true",
            help=(
                "Leave the fresh isolated Notebook bundle open after success or failure; "
                "default failure finalization closes every exact leased Notebook."
            ),
        )
        parser.add_argument(
            "--keep-worksite",
            action="store_true",
            help=(
                "Preserve this action's verified post-mutation state for manual inspection, "
                "leave the isolated source Notebook open, and record exact cleanup IDs."
            ),
        )
        parser.add_argument(
            "--all-child",
            action="store_true",
            dest="all_child",
            help=argparse.SUPPRESS,
        )
        runtime_flags(parser, timeout_default=self.timeout_default)

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add scenario-specific CLI flags before the shared lifecycle flags."""

    def prepare_arguments(
        self,
        args: argparse.Namespace,
        manifest: dict[str, Any],
    ) -> None:
        """Derive scenario-only arguments from the fresh manifest before execution."""

    async def execute(
        self,
        args: argparse.Namespace,
        options: RuntimeOptions,
        manifest: dict[str, Any],
        *,
        client: MCPStdioClient | None,
        fixture_result: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(f"Scenario '{self.name}' does not implement execute().")


__all__ = ["RuntimeFlags", "Scenario"]
