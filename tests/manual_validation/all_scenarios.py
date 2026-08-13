"""Serial subprocess orchestration for the explicit human-gated ``all`` command."""

from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
from queue import Queue
import shlex
import subprocess
import sys
from threading import Thread
import time
from typing import Any, Sequence

from .progress import VERBOSITY_LEVELS, bounded_terminal_text, safe_error_text
from .runtime import ALL_CHILD_ISOLATION_PREFIX, EXIT_MCP


def register_all_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "all",
        help=(
            "GATED: serially launch explicitly registered test scenario suites; "
            "after a failure, continue only when exact Notebook isolation is proven."
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
            "Output detail: quiet streams each child's major phases and failures "
            "(default), normal also streams case/mutation progress and results, "
            "verbose additionally streams child commands, timing, and stderr."
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
    if not args.dry_run:
        command.append("--all-child")
    command.extend(["--verbosity", args.verbosity])
    return command


def _extract_isolation_marker(stderr: str) -> tuple[str, bool | None]:
    passed: bool | None = None
    retained: list[str] = []
    for line in stderr.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(ALL_CHILD_ISOLATION_PREFIX):
            try:
                payload = json.loads(stripped[len(ALL_CHILD_ISOLATION_PREFIX) :])
                passed = payload.get("passed") is True
            except (json.JSONDecodeError, AttributeError):
                passed = False
            continue
        retained.append(line)
    return "".join(retained), passed


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
        elif event == "failure-isolated":
            print(
                f"  isolated {fields['scenario']}: exact run Notebook bundle closed; "
                "continuing.",
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
            if fields["skipped"]:
                print(
                    f"Stopped after {fields['attempted']}/{fields['total']} scenarios: "
                    f"{fields['passed']} passed, {fields['failed']} failed, "
                    f"{fields['skipped']} not started ({fields['elapsed_seconds']:.2f}s).",
                    flush=True,
                )
                print(
                    "Stopped because exact failure isolation could not be proven. Review "
                    "failure-finalization.json before starting another real scenario; "
                    "validated cache templates remain reusable.",
                    flush=True,
                )
            else:
                print(
                    f"Completed {fields['total']} scenarios: {fields['passed']} passed, "
                    f"{fields['failed']} failed ({fields['elapsed_seconds']:.2f}s).",
                    flush=True,
                )

    def child_line(self, *, scenario: str, stream: str, line: str) -> None:
        """Forward one already content-free child line with unambiguous context."""

        text = safe_error_text(line.rstrip("\r\n"))
        if text:
            print(f"  {scenario} | {text}", flush=True)

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
            value = (
                _parse_child_json(stdout)
                if self.json_output
                else bounded_terminal_text(stdout, verbosity=self.verbosity)
            )
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
                text=(
                    stderr
                    if self.json_output
                    else bounded_terminal_text(stderr, verbosity=self.verbosity)
                ),
            )


def _stream_child(
    command: list[str],
    *,
    reporter: ProgressReporter,
    scenario: str,
    start_child: Any,
) -> tuple[int, str, str, bool | None]:
    """Stream a text child without allowing either pipe to block the other."""

    process = start_child(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    if process.stdout is None or process.stderr is None:
        raise OSError("Scenario child did not expose both output pipes.")

    events: Queue[tuple[str, str | None]] = Queue()
    stderr_tail: deque[str] = deque(maxlen=400)
    isolation_passed: bool | None = None

    def read_stream(stream_name: str, stream: Any) -> None:
        try:
            for line in iter(stream.readline, ""):
                events.put((stream_name, line))
        except (OSError, UnicodeError) as exc:
            events.put(
                (
                    "stderr",
                    f"child {stream_name} reader failed: {type(exc).__name__}\n",
                )
            )
        finally:
            try:
                stream.close()
            except OSError:
                pass
            events.put((stream_name, None))

    readers = [
        Thread(
            target=read_stream,
            args=(stream_name, stream),
            daemon=True,
            name=f"manual-validation-{scenario}-{stream_name}",
        )
        for stream_name, stream in (
            ("stdout", process.stdout),
            ("stderr", process.stderr),
        )
    ]
    for reader in readers:
        reader.start()

    completed_streams = 0
    while completed_streams < len(readers):
        stream_name, line = events.get()
        if line is None:
            completed_streams += 1
            continue
        if stream_name == "stdout":
            reporter.child_line(scenario=scenario, stream=stream_name, line=line)
            continue
        cleaned, marker = _extract_isolation_marker(line)
        if marker is not None:
            isolation_passed = marker
        if not cleaned:
            continue
        stderr_tail.append(cleaned)
        if reporter.verbosity == "verbose":
            reporter.child_line(scenario=scenario, stream=stream_name, line=cleaned)

    exit_code = int(process.wait())
    for reader in readers:
        reader.join(timeout=1)
    return (
        exit_code,
        "",
        "" if reporter.verbosity == "verbose" else "".join(stderr_tail),
        isolation_passed,
    )


def run_all(
    args: argparse.Namespace,
    *,
    scenarios: Sequence[str],
    run_child: Any | None = None,
    start_child: Any | None = None,
) -> int:
    """Run suites serially, continuing only across proven-isolated failures."""

    if args.timeout is not None and args.timeout < 1:
        raise ValueError("--timeout must be at least 1 second.")

    reporter = ProgressReporter(
        json_output=bool(args.json_output),
        verbosity=args.verbosity,
    )
    started = time.perf_counter()
    failures: list[dict[str, Any]] = []
    total = len(scenarios)

    attempted = 0
    for index, scenario in enumerate(scenarios, start=1):
        attempted = index
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
            if args.json_output or run_child is not None:
                completed = (run_child or subprocess.run)(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                exit_code = int(completed.returncode)
                stdout = completed.stdout or ""
                stderr, isolation_passed = _extract_isolation_marker(
                    completed.stderr or ""
                )
            else:
                exit_code, stdout, stderr, isolation_passed = _stream_child(
                    command,
                    reporter=reporter,
                    scenario=scenario,
                    start_child=start_child or subprocess.Popen,
                )
        except OSError as exc:
            exit_code = EXIT_MCP
            stdout = ""
            stderr = str(exc)
            isolation_passed = True

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
            failures.append(
                {
                    "scenario": scenario,
                    "exit_code": exit_code,
                    "isolation_passed": isolation_passed,
                }
            )
            if not args.dry_run:
                if isolation_passed is not True:
                    break
                reporter.emit("failure-isolated", scenario=scenario)

    reporter.emit(
        "all-completed",
        total=total,
        attempted=attempted,
        skipped=total - attempted,
        passed=attempted - len(failures),
        failed=len(failures),
        failures=failures,
        elapsed_seconds=round(time.perf_counter() - started, 6),
    )
    return failures[0]["exit_code"] if failures else 0


__all__ = ["ProgressReporter", "VERBOSITY_LEVELS", "register_all_parser", "run_all"]
