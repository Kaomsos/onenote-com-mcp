"""CLI startup and top-level dispatch for human-gated OneNote validation."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .mcp_stdio_client import ClientFailure
from .runtime import EXIT_ARGUMENT, EXIT_MCP, RunnerFailure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "HUMAN-GATED isolated OneNote mutation validation. Named scenarios are "
            "fresh-Notebook least-privilege suites; all serially launches each suite. "
            "Agents/CI must not run real commands."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def runtime_flags(
        command: argparse.ArgumentParser,
        *,
        timeout_default: int = 180,
    ) -> None:
        command.add_argument(
            "--run-dir",
            type=Path,
            help="Fresh artifact directory; defaults to .local-validation/run-<UTC timestamp>.",
        )
        command.add_argument(
            "--timeout",
            type=int,
            default=timeout_default,
            help=f"Per MCP operation timeout in seconds (default: {timeout_default}).",
        )
        command.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the static plan without starting MCP.",
        )
        command.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Print stable JSON only.",
        )

    from .all_scenarios import register_all_parser
    from .scenarios.common.registry import SCENARIO_REGISTRY

    SCENARIO_REGISTRY.register_parsers(subparsers, runtime_flags)
    register_all_parser(subparsers)
    return parser


def print_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return
    for key, value in result.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        else:
            rendered = str(value)
        print(f"{key}: {rendered}")


async def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    from .scenarios import dispatch_command

    return await dispatch_command(args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "all":
        from .all_scenarios import run_all
        from .scenarios.common.registry import get_all_scenario_names

        try:
            return run_all(args, scenarios=get_all_scenario_names())
        except ValueError as exc:
            error = {"ok": False, "error": str(exc), "exit_code": EXIT_ARGUMENT}
            print_result(error, json_output=bool(args.json_output))
            return EXIT_ARGUMENT

    try:
        result = asyncio.run(dispatch(args))
    except RunnerFailure as exc:
        from .scenarios.common.orchestrator import record_failure

        record_failure(args, str(exc), exc.exit_code)
        error = {"ok": False, "error": str(exc), "exit_code": exc.exit_code}
        print_result(error, json_output=bool(getattr(args, "json_output", False)))
        return exc.exit_code
    except ClientFailure as exc:
        from .scenarios.common.orchestrator import record_failure

        record_failure(args, str(exc), EXIT_MCP)
        error = {"ok": False, "error": str(exc), "exit_code": EXIT_MCP}
        print_result(error, json_output=bool(getattr(args, "json_output", False)))
        return EXIT_MCP
    result = {"ok": True, **result}
    print_result(result, json_output=bool(getattr(args, "json_output", False)))
    return 0


__all__ = ["build_parser", "dispatch", "main", "print_result"]
