"""CLI startup and top-level dispatch for human-gated OneNote validation."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from .mcp_stdio_client import ClientFailure
from .progress import (
    VERBOSITY_LEVELS,
    RunProgressReporter,
    bounded_terminal_text,
    print_compact_scenario_result,
    safe_error_text,
)
from .runtime import (
    ALL_CHILD_ISOLATION_PREFIX,
    EXIT_ARGUMENT,
    EXIT_MCP,
    RestoreFailure,
    RunnerFailure,
)


def _emit_all_child_isolation(args: argparse.Namespace, result: dict[str, Any]) -> None:
    if not bool(getattr(args, "all_child", False)):
        return
    payload = {
        "passed": result.get("isolation_passed") is True,
        "status": str(result.get("status", "unknown")),
    }
    print(
        ALL_CHILD_ISOLATION_PREFIX
        + json.dumps(payload, ensure_ascii=False, sort_keys=True),
        file=sys.stderr,
        flush=True,
    )


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
            help=(
                "Fresh artifact directory; defaults to .local-validation/run-"
                "<local YYYY-MM-DD-HH-MM-SS timestamp>."
            ),
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
        command.add_argument(
            "--verbosity",
            choices=VERBOSITY_LEVELS,
            default="normal",
            help=(
                "Terminal detail: quiet shows major phases, normal also shows case and "
                "scenario mutation progress (default), verbose adds content-free timing. "
                "Ignored when --json is present."
            ),
        )
        command.add_argument(
            "--use-cache",
            action="store_true",
            help=(
                "Use or build the managed immutable fixture bundle cache, then open only "
                "a new run-scoped working copy. Default is a fresh uncached fixture."
            ),
        )

    from .all_scenarios import register_all_parser
    from .maintenance import register_maintenance_parsers
    from .scenarios.common.registry import SCENARIO_REGISTRY

    SCENARIO_REGISTRY.register_parsers(subparsers, runtime_flags)
    register_all_parser(subparsers)
    register_maintenance_parsers(subparsers)
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


def print_failure(
    exc: RunnerFailure,
    *,
    json_output: bool,
    run_dir: Path | None = None,
    verbosity: str = "normal",
    phase: str = "preflight",
    failure_finalization: dict[str, Any] | None = None,
) -> None:
    if json_output:
        print(json.dumps(exc.as_error_dict(), ensure_ascii=False, sort_keys=True))
        return
    diagnostic = bounded_terminal_text(
        "\n".join(safe_error_text(line) for line in exc.terminal_lines()),
        verbosity=verbosity,
    )
    print(diagnostic, flush=True)
    print(f"Phase: {phase}", flush=True)
    if failure_finalization is not None:
        status = str(failure_finalization.get("status", "unknown"))
        if status == "closed":
            lifecycle_message = (
                "exact leased Notebook bundle closed; working files preserved"
            )
        elif status == "preserved_open":
            lifecycle_message = "explicit keep mode preserved Notebook bundle open"
        elif status == "not_started":
            lifecycle_message = "Notebook lifecycle was not started"
        else:
            lifecycle_message = (
                "exact close was not proven; do not continue another real scenario"
            )
        print(f"Failure lifecycle: {lifecycle_message}", flush=True)
    if run_dir is not None:
        resolved = run_dir.resolve()
        print(f"Working files preserved: {str(resolved.exists()).lower()}", flush=True)
        print(f"Artifacts: {resolved}", flush=True)
        print(f"Failure evidence: {resolved / 'run-failure.json'}", flush=True)


async def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    from .scenarios import dispatch_command

    return await dispatch_command(args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    from .maintenance import MAINTENANCE_COMMAND

    if args.command == MAINTENANCE_COMMAND:
        from .maintenance import run_maintenance

        try:
            result, exit_code = run_maintenance(args)
        except RunnerFailure as exc:
            print_failure(exc, json_output=bool(args.json_output))
            return exc.exit_code
        print_result(result, json_output=bool(args.json_output))
        return exit_code

    if args.command == "all":
        from .all_scenarios import run_all
        from .scenarios.common.registry import get_all_scenario_names

        try:
            return run_all(args, scenarios=get_all_scenario_names())
        except ValueError as exc:
            error = {"ok": False, "error": str(exc), "exit_code": EXIT_ARGUMENT}
            print_result(error, json_output=bool(args.json_output))
            return EXIT_ARGUMENT

    if getattr(args, "scenario", None) is None:
        args.scenario = args.command

    try:
        result = asyncio.run(dispatch(args))
    except RunnerFailure as exc:
        from .scenarios.common.orchestrator import record_failure

        failure_finalization = record_failure(args, exc, exc.exit_code)
        _emit_all_child_isolation(args, failure_finalization)
        progress = getattr(args, "progress", None)
        if isinstance(progress, RunProgressReporter):
            progress.failure(str(exc), run_dir=getattr(args, "run_dir", None))
        print_failure(
            exc,
            json_output=bool(getattr(args, "json_output", False)),
            run_dir=getattr(args, "run_dir", None),
            verbosity=str(getattr(args, "verbosity", "normal")),
            phase=(
                progress.current_phase
                if isinstance(progress, RunProgressReporter)
                else "preflight"
            ),
            failure_finalization=failure_finalization,
        )
        return exc.exit_code
    except ClientFailure as exc:
        from .scenarios.common.orchestrator import record_failure

        failure_finalization = record_failure(args, str(exc), EXIT_MCP)
        _emit_all_child_isolation(args, failure_finalization)
        progress = getattr(args, "progress", None)
        if isinstance(progress, RunProgressReporter):
            progress.failure(str(exc), run_dir=getattr(args, "run_dir", None))
        if bool(getattr(args, "json_output", False)):
            error = {"ok": False, "error": str(exc), "exit_code": EXIT_MCP}
            print_result(error, json_output=True)
        else:
            print_failure(
                RunnerFailure(str(exc), EXIT_MCP),
                json_output=False,
                run_dir=getattr(args, "run_dir", None),
                verbosity=str(getattr(args, "verbosity", "normal")),
                phase=(
                    progress.current_phase
                    if isinstance(progress, RunProgressReporter)
                    else "preflight"
                ),
                failure_finalization=failure_finalization,
            )
        return EXIT_MCP
    except Exception as exc:
        from local_onenote_mcp.onenote_errors import OneNoteBridgeError
        from .scenarios.common.orchestrator import record_failure

        progress = getattr(args, "progress", None)
        phase = (
            progress.current_phase
            if isinstance(progress, RunProgressReporter)
            else "preflight"
        )
        if isinstance(exc, OneNoteBridgeError) or phase == "lifecycle":
            wrapped: RunnerFailure = RestoreFailure(
                f"Exact Notebook lifecycle failed: {exc}"
            )
        else:
            wrapped = RunnerFailure(
                f"Unexpected validation failure: {type(exc).__name__}: {exc}",
                EXIT_MCP,
            )
        failure_finalization = record_failure(args, wrapped, wrapped.exit_code)
        _emit_all_child_isolation(args, failure_finalization)
        if isinstance(progress, RunProgressReporter):
            progress.failure(str(wrapped), run_dir=getattr(args, "run_dir", None))
        print_failure(
            wrapped,
            json_output=bool(getattr(args, "json_output", False)),
            run_dir=getattr(args, "run_dir", None),
            verbosity=str(getattr(args, "verbosity", "normal")),
            phase=phase,
            failure_finalization=failure_finalization,
        )
        return wrapped.exit_code
    result = {"ok": True, **result}
    if bool(getattr(args, "json_output", False)):
        print_result(result, json_output=True)
    else:
        print_compact_scenario_result(
            result,
            verbosity=str(getattr(args, "verbosity", "normal")),
            dry_run=bool(getattr(args, "dry_run", False)),
            progress=getattr(args, "progress", None),
        )
    return 0


__all__ = ["build_parser", "dispatch", "main", "print_failure", "print_result"]
