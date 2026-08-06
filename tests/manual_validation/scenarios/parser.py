"""Gated CLI registration: every public command is one complete scenario suite."""

from __future__ import annotations

import argparse
from typing import Callable


RuntimeFlags = Callable[..., None]


def register_parsers(
    subparsers: argparse._SubParsersAction,
    runtime_flags: RuntimeFlags,
) -> None:
    create = subparsers.add_parser(
        "create",
        help="GATED: create the preset isolated Notebook fixture, report, then close or keep.",
    )
    create.add_argument("--notebook-name")
    _scenario_flags(create, runtime_flags)

    rename = subparsers.add_parser(
        "rename", help="GATED: create, rename/restore, report, then close or keep."
    )
    rename.add_argument(
        "--target", choices=["group_a", "group_b", "move_source"], default="move_source"
    )
    rename.add_argument("--new-name")
    rename.add_argument("--notebook-name")
    _scenario_flags(rename, runtime_flags)

    reorder = subparsers.add_parser(
        "reorder", help="GATED: create, reorder/restore, report, then close or keep."
    )
    reorder.add_argument("--page-level", type=int, default=2)
    reorder.add_argument("--notebook-name")
    _scenario_flags(reorder, runtime_flags)

    move = subparsers.add_parser(
        "move", help="GATED: create, move/restore, report, then close or keep."
    )
    move.add_argument("--notebook-name")
    _scenario_flags(move, runtime_flags)

    delete = subparsers.add_parser(
        "delete",
        help="GATED: create, non-permanently delete the disposable group, report, then close or keep.",
    )
    delete.add_argument("--notebook-name")
    _scenario_flags(delete, runtime_flags)

    for scenario, help_text in (
        ("copy-page", "copy the prepared Page subtree and clean up the target"),
        ("copy-section", "copy the prepared Section and clean up the target"),
        ("copy-section-group", "copy the prepared Section Group and clean up the target"),
        ("copy-notebook", "copy and close the Notebook while preserving its folder"),
        (
            "reconstructive-move-page",
            "strictly move the disposable Page by verified Copy plus source recycle",
        ),
    ):
        command = subparsers.add_parser(
            scenario,
            help=f"GATED: create, {help_text}, report, then close or keep.",
        )
        command.add_argument("--notebook-name")
        _scenario_flags(command, runtime_flags, timeout_default=1_800)


def _scenario_flags(
    parser: argparse.ArgumentParser,
    runtime_flags: RuntimeFlags,
    *,
    timeout_default: int = 180,
) -> None:
    parser.add_argument(
        "--keep-notebook",
        action="store_true",
        help="Leave the fresh isolated source Notebook open after this scenario succeeds.",
    )
    runtime_flags(parser, timeout_default=timeout_default)
