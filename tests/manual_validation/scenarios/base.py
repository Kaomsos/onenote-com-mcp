"""Class contract shared by executable manual-validation scenarios."""

from __future__ import annotations

import argparse
from typing import Any, Callable

from ..mcp_stdio_client import MCPStdioClient
from ..runtime import RuntimeOptions
from .common.specs import ScenarioSpec, get_scenario_spec


RuntimeFlags = Callable[..., None]


class Scenario:
    """One independently runnable isolated scenario suite."""

    name = ""
    help_text = ""
    timeout_default = 180
    registered_for_all = False

    @property
    def spec(self) -> ScenarioSpec:
        return get_scenario_spec(self.name)

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
        parser.add_argument("--notebook-name")
        parser.add_argument(
            "--keep-notebook",
            action="store_true",
            help="Leave the fresh isolated source Notebook open after this scenario succeeds.",
        )
        parser.add_argument(
            "--keep-worksite",
            action="store_true",
            help=(
                "Preserve this action's verified post-mutation state for manual inspection, "
                "leave the isolated source Notebook open, and record exact cleanup IDs."
            ),
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
