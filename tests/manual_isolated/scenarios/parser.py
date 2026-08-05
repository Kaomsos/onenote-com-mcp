"""CLI parser registration for all named scenarios."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from ._config import DEFAULT_NOTEBOOK_NAME


RuntimeFlags = Callable[..., None]


def register_parsers(
    subparsers: argparse._SubParsersAction,
    runtime_flags: RuntimeFlags,
) -> None:
    inspect_parser = subparsers.add_parser(
        "inspect", help="Read-only exact-name discovery and tree inspection."
    )
    inspect_parser.add_argument("--notebook-name", required=True)
    runtime_flags(inspect_parser)

    create_parser = subparsers.add_parser(
        "create", help="Idempotently create/reuse the isolated fixture tree."
    )
    create_parser.add_argument("--notebook-name", default=DEFAULT_NOTEBOOK_NAME)
    create_parser.add_argument("--base-folder", default="")
    runtime_flags(create_parser)

    read_parser = subparsers.add_parser(
        "read",
        aliases=["baseline"],
        help="Capture a read-only hierarchy and Page hash baseline.",
    )
    target = read_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--notebook-name")
    target.add_argument("--notebook-id")
    read_parser.add_argument(
        "--export-onepkg",
        action="store_true",
        help="Also export the target Notebook to <output>/baseline.onepkg without overwriting.",
    )
    runtime_flags(read_parser)

    validate_parser = subparsers.add_parser(
        "validate", help="Run exactly one mutation and its checks."
    )
    validate_subparsers = validate_parser.add_subparsers(dest="scenario", required=True)
    rename = validate_subparsers.add_parser(
        "rename", help="Rename and restore one prepared container."
    )
    rename.add_argument(
        "--target", choices=["group_a", "group_b", "move_source"], default="move_source"
    )
    rename.add_argument("--new-name")
    rename.add_argument("--notebook-name")
    runtime_flags(rename, run_dir_required=True)

    reorder = validate_subparsers.add_parser(
        "reorder", help="Reorder/indent Sibling and restore it."
    )
    reorder.add_argument("--page-level", type=int, default=2)
    reorder.add_argument("--notebook-name")
    runtime_flags(reorder, run_dir_required=True)

    move = validate_subparsers.add_parser(
        "move", help="Move Move-Source to Group-B and restore it."
    )
    move.add_argument("--notebook-name")
    runtime_flags(move, run_dir_required=True)

    delete = validate_subparsers.add_parser(
        "delete", help="Non-permanently delete one manifest-allowlisted fixture."
    )
    delete.add_argument("--delete-target-id", required=True)
    delete.add_argument("--notebook-name")
    runtime_flags(delete, run_dir_required=True)

    for scenario, help_text in (
        ("copy-page", "Copy the prepared Parent Page subtree and clean up the target."),
        ("copy-section", "Copy Move-Source into Group-B and clean up the target."),
        ("copy-section-group", "Copy Group-A into the prepared Notebook and clean up the target."),
        ("copy-notebook", "Copy and close the Notebook in a manifest-scoped disposable folder."),
        (
            "reconstructive-move-page",
            "Move the disposable Page by verified Copy plus non-permanent source deletion.",
        ),
    ):
        command = validate_subparsers.add_parser(scenario, help=help_text)
        command.add_argument("--notebook-name")
        runtime_flags(command, run_dir_required=True, timeout_default=1_800)

    report = subparsers.add_parser(
        "report", help="Regenerate report.md from local artifacts only."
    )
    report.add_argument("--run-dir", type=Path, required=True)
    report.add_argument("--onenote-version", help="Record the OneNote version used for the manual run.")
    report.add_argument("--office-channel", help="Record the Office update channel used for the manual run.")
    report.add_argument("--json", action="store_true", dest="json_output")
