"""Dispatch for human-gated, isolated, least-privilege scenario suites."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..runtime import RunnerFailure, RuntimeOptions
from ..run_identity import new_run_identity, validation_notebook_names

# Importing each public scenario applies its ``@SCENARIO_REGISTRY.register``
# wrapper.  This is the single, reviewable module list that controls discovery;
# common/registry.py contains no scenario imports or parallel construction list.
from .create import CreateScenario
from .rename import RenameScenario
from .reorder_page import ReorderPageScenario
from .reorder_section import ReorderSectionScenario
from .reorder_section_group import ReorderSectionGroupScenario
from .reparent_section import ReparentSectionScenario
from .reparent_page import ReparentPageScenario
from .reparent_page_scope import ReparentPageScopeScenario
from .reparent_section_group import ReparentSectionGroupScenario
from .delete import DeleteScenario
from .copy_page import CopyPageScenario
from .copy_section import CopySectionScenario
from .copy_section_group import CopySectionGroupScenario
from .copy_notebook import CopyNotebookScenario
from .copy_display_equation import CopyDisplayEquationScenario
from .move_page import MovePageScenario
from .move_section import MoveSectionScenario
from .move_section_group import MoveSectionGroupScenario
from .search_all_open_notebooks import SearchAllOpenNotebooksScenario
from .bootstrap_inserted_file_fixture import BootstrapInsertedFileFixtureScenario
from .bootstrap_ink_drawing_fixture import BootstrapInkDrawingFixtureScenario
from .bootstrap_media_file_fixture import BootstrapMediaFileFixtureScenario
from .bootstrap_shape_fixture import BootstrapShapeFixtureScenario
from .bootstrap_inline_equation_fixture import BootstrapInlineEquationFixtureScenario
from .bootstrap_user_authored_fixture import BootstrapUserAuthoredFixtureScenario
from .cache_invalidation import CacheInvalidationScenario
from .user_authored_fixture_consumer import UserAuthoredFixtureConsumerScenario
from .interactive_copy_inserted_file import InteractiveCopyInsertedFileScenario
from .interactive_copy_ink_drawing import InteractiveCopyInkDrawingScenario
from .interactive_copy_media_file import InteractiveCopyMediaFileScenario
from .interactive_copy_ui_shape import InteractiveCopyUIShapeScenario
from .interactive_copy_inline_equation import InteractiveCopyInlineEquationScenario

from .common.registry import SCENARIO_REGISTRY
from .common.orchestrator import PUBLIC_SCENARIOS, run_validate


async def dispatch_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.command not in PUBLIC_SCENARIOS:
        raise RunnerFailure(f"Unknown isolated scenario: {args.command}")
    args.scenario = args.command
    scenario = SCENARIO_REGISTRY.get(args.scenario)
    identity = new_run_identity()
    notebook_label = getattr(args, "notebook_label", None)
    legacy_label = getattr(args, "notebook_name", None)
    if notebook_label is not None and legacy_label is not None:
        raise RunnerFailure("Use only --notebook-label; --notebook-name is its deprecated alias.")
    selected_label = notebook_label or legacy_label or args.scenario
    roles = tuple(role.role for role in scenario.fixture_recipe.cache_identity.notebook_roles)
    try:
        fresh_names = validation_notebook_names(
            args.scenario,
            identity,
            roles,
            cached=False,
            label=selected_label,
        )
        cached_names = validation_notebook_names(
            args.scenario,
            identity,
            roles,
            cached=True,
            label=selected_label,
        )
    except ValueError as exc:
        raise RunnerFailure(str(exc)) from exc
    args.run_identity = identity
    args.notebook_label = selected_label
    args.fresh_notebook_names = fresh_names
    args.cached_notebook_names = cached_names
    args.notebook_name = fresh_names["source"]
    run_dir = args.run_dir or Path(".local-validation") / f"run-{identity.safe_timestamp}"
    args.run_dir = run_dir
    if args.timeout < 1:
        raise RunnerFailure("--timeout must be at least 1 second.")
    options = RuntimeOptions(
        run_dir=run_dir,
        timeout=args.timeout,
        json_output=args.json_output,
        dry_run=args.dry_run,
        use_cache=bool(args.use_cache),
        cache_root=(Path(".local-validation") / "fixture-cache").resolve(),
    )
    return await run_validate(args, options)


__all__ = [
    "CreateScenario",
    "RenameScenario",
    "ReorderPageScenario",
    "ReorderSectionScenario",
    "ReorderSectionGroupScenario",
    "ReparentSectionScenario",
    "ReparentPageScenario",
    "ReparentPageScopeScenario",
    "ReparentSectionGroupScenario",
    "DeleteScenario",
    "CopyPageScenario",
    "CopySectionScenario",
    "CopySectionGroupScenario",
    "CopyNotebookScenario",
    "CopyDisplayEquationScenario",
    "MovePageScenario",
    "MoveSectionScenario",
    "MoveSectionGroupScenario",
    "SearchAllOpenNotebooksScenario",
    "BootstrapInsertedFileFixtureScenario",
    "BootstrapInkDrawingFixtureScenario",
    "BootstrapMediaFileFixtureScenario",
    "BootstrapShapeFixtureScenario",
    "BootstrapInlineEquationFixtureScenario",
    "BootstrapUserAuthoredFixtureScenario",
    "CacheInvalidationScenario",
    "UserAuthoredFixtureConsumerScenario",
    "InteractiveCopyInsertedFileScenario",
    "InteractiveCopyInkDrawingScenario",
    "InteractiveCopyMediaFileScenario",
    "InteractiveCopyUIShapeScenario",
    "InteractiveCopyInlineEquationScenario",
    "dispatch_command",
]
