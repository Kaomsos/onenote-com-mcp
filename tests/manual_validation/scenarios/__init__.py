"""Dispatch for human-gated, isolated, least-privilege scenario suites."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..runtime import RunnerFailure, RuntimeOptions
from ..test_utils import timestamp
from .common.config import ISOLATED_SCENARIO_NOTEBOOK_PREFIX

# Importing each public scenario applies its ``@SCENARIO_REGISTRY.register``
# wrapper.  This is the single, reviewable module list that controls discovery;
# common/registry.py contains no scenario imports or parallel construction list.
from .create import CreateScenario
from .rename import RenameScenario
from .reorder import ReorderScenario
from .move import MoveScenario
from .delete import DeleteScenario
from .copy_page import CopyPageScenario
from .copy_section import CopySectionScenario
from .copy_section_group import CopySectionGroupScenario
from .copy_notebook import CopyNotebookScenario
from .reconstructive_move_page import ReconstructiveMovePageScenario

from .common.orchestrator import PUBLIC_SCENARIOS, run_validate


async def dispatch_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.command not in PUBLIC_SCENARIOS:
        raise RunnerFailure(f"Unknown isolated scenario: {args.command}")
    args.scenario = args.command
    identity = timestamp()
    if args.notebook_name is None:
        args.notebook_name = f"{ISOLATED_SCENARIO_NOTEBOOK_PREFIX}{identity}"
    run_dir = args.run_dir or Path(".local-validation") / f"run-{identity}"
    args.run_dir = run_dir
    if args.timeout < 1:
        raise RunnerFailure("--timeout must be at least 1 second.")
    options = RuntimeOptions(
        run_dir=run_dir,
        timeout=args.timeout,
        json_output=args.json_output,
        dry_run=args.dry_run,
    )
    return await run_validate(args, options)


__all__ = [
    "CreateScenario",
    "RenameScenario",
    "ReorderScenario",
    "MoveScenario",
    "DeleteScenario",
    "CopyPageScenario",
    "CopySectionScenario",
    "CopySectionGroupScenario",
    "CopyNotebookScenario",
    "ReconstructiveMovePageScenario",
    "dispatch_command",
]
