"""Serial subprocess orchestration for the explicit human-gated ``all`` command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any, Sequence

from .runtime import EXIT_MCP


VERBOSITY_LEVELS = ("quiet", "normal", "verbose")


def register_all_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "all",
        help=(
            "GATED: serially launch every explicitly registered test scenario suite; "
            "each child keeps its own default Notebook and run directory."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help=(
            "Pass one per-operation timeout to every scenario; omit to preserve each "
            "scenario's own default."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass --dry-run to every scenario; no MCP or OneNote access occurs.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Pass --json to every scenario and emit machine-readable JSON Lines progress.",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Pass --use-cache to every independently launched included scenario.",
    )
    parser.add_argument(
        "--verbosity",
        choices=VERBOSITY_LEVELS,
        default="quiet",
        help=(
            "Output detail: quiet shows progress and failures (default), normal also "
            "shows scenario results, verbose additionally shows child commands/stderr."
        ),
    )


def _child_command(args: argparse.Namespace, scenario: str) -> list[str]:
    command = [sys.executable, str(Path(__file__).with_name("run.py")), scenario]
    if args.timeout is not None:
        command.extend(["--timeout", str(args.timeout)])
    if args.dry_run:
        command.append("--dry-run")
    if args.json_output:
        command.append("--json")
    if bool(getattr(args, "use_cache", False)):
        command.append("--use-cache")
    return command


def _parse_child_json(stdout: str) -> Any:
    stripped = stdout.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


class ProgressReporter:
    def __init__(self, *, json_output: bool, verbosity: str) -> None:
        self.json_output = json_output
        self.verbosity = verbosity

    def emit(self, event: str, **fields: Any) -> None:
        if self.json_output:
            print(
                json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True),
                flush=True,
            )
            return
        if event == "scenario-started":
            print(
                f"[{fields['index']}/{fields['total']}] {fields['scenario']} ...",
                flush=True,
            )
        elif event == "scenario-passed":
            print(
                f"[{fields['index']}/{fields['total']}] PASS {fields['scenario']} "
                f"({fields['elapsed_seconds']:.2f}s)",
                flush=True,
            )
        elif event == "scenario-failed":
            print(
                f"[{fields['index']}/{fields['total']}] FAIL {fields['scenario']} "
                f"(exit {fields['exit_code']}, {fields['elapsed_seconds']:.2f}s)",
                flush=True,
            )
        elif event == "scenario-command":
            print(f"  command: {fields['command']}", flush=True)
        elif event == "scenario-output":
            stream = fields["stream"]
            text = str(fields["text"]).rstrip()
            if text:
                for line in text.splitlines():
                    print(f"  {stream}: {line}", flush=True)
        elif event == "all-completed":
            print(
                f"Completed {fields['total']} scenarios: {fields['passed']} passed, "
                f"{fields['failed']} failed ({fields['elapsed_seconds']:.2f}s).",
                flush=True,
            )

    def child_output(
        self,
        *,
        scenario: str,
        passed: bool,
        stdout: str,
        stderr: str,
    ) -> None:
        show_stdout = not passed or self.verbosity in {"normal", "verbose"}
        show_stderr = bool(stderr.strip()) and (not passed or self.verbosity == "verbose")
        if show_stdout and stdout.strip():
            value = _parse_child_json(stdout) if self.json_output else stdout
            self.emit(
                "scenario-output",
                scenario=scenario,
                stream="stdout",
                text=value,
            )
        if show_stderr:
            self.emit(
                "scenario-output",
                scenario=scenario,
                stream="stderr",
                text=stderr,
            )


def run_all(
    args: argparse.Namespace,
    *,
    scenarios: Sequence[str],
    run_child: Any = subprocess.run,
) -> int:
    """Run the supplied registered test scenarios and return the first non-zero exit code."""

    if args.timeout is not None and args.timeout < 1:
        raise ValueError("--timeout must be at least 1 second.")

    reporter = ProgressReporter(
        json_output=bool(args.json_output),
        verbosity=args.verbosity,
    )
    started = time.perf_counter()
    failures: list[dict[str, Any]] = []
    total = len(scenarios)

    for index, scenario in enumerate(scenarios, start=1):
        command = _child_command(args, scenario)
        reporter.emit("scenario-started", index=index, total=total, scenario=scenario)
        if args.verbosity == "verbose":
            reporter.emit(
                "scenario-command",
                scenario=scenario,
                command=shlex.join(command),
            )
        scenario_started = time.perf_counter()
        try:
            completed = run_child(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            exit_code = int(completed.returncode)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
        except OSError as exc:
            exit_code = EXIT_MCP
            stdout = ""
            stderr = str(exc)

        elapsed = time.perf_counter() - scenario_started
        passed = exit_code == 0
        reporter.emit(
            "scenario-passed" if passed else "scenario-failed",
            index=index,
            total=total,
            scenario=scenario,
            exit_code=exit_code,
            elapsed_seconds=round(elapsed, 6),
        )
        reporter.child_output(
            scenario=scenario,
            passed=passed,
            stdout=stdout,
            stderr=stderr,
        )
        if not passed:
            failures.append({"scenario": scenario, "exit_code": exit_code})

    reporter.emit(
        "all-completed",
        total=total,
        passed=total - len(failures),
        failed=len(failures),
        failures=failures,
        elapsed_seconds=round(time.perf_counter() - started, 6),
    )
    return failures[0]["exit_code"] if failures else 0


__all__ = ["ProgressReporter", "VERBOSITY_LEVELS", "register_all_parser", "run_all"]
