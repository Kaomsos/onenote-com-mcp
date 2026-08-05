"""Command registry and dispatch for the isolated manual runner."""

from __future__ import annotations

import argparse
from typing import Any

from ..runner import RunnerFailure, RuntimeOptions, default_run_dir
from .create import run_create
from .inspect import run_inspect
from .read import run_read
from .report import run_report
from .validation import run_validate


COMMAND_RUNNERS = {
    "inspect": run_inspect,
    "create": run_create,
    "read": run_read,
    "baseline": run_read,
    "validate": run_validate,
}


async def dispatch_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "report":
        return await run_report(args)

    run_dir = args.run_dir or default_run_dir()
    if args.timeout < 1:
        raise RunnerFailure("--timeout must be at least 1 second.")
    options = RuntimeOptions(
        run_dir=run_dir,
        timeout=args.timeout,
        json_output=args.json_output,
        dry_run=args.dry_run,
    )
    return await COMMAND_RUNNERS[args.command](args, options)


__all__ = ["dispatch_command"]
