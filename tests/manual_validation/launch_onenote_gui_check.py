"""HUMAN-GATED standalone acceptance check for ``launch_onenote_gui``.

This entry point is deliberately outside the Scenario registry and ``all``. Importing
the module is side-effect free; only a user-confirmed interactive invocation may start
OneNote Desktop.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Callable


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from manual_validation.mcp_stdio_client import (
        ClientFailure,
        MCPStdioClient,
        READ_ONLY_POLICY,
        ScenarioPolicy,
    )
    from manual_validation.onenote_exit_wait import (
        POLL_INTERVAL_SECONDS,
        OneNoteExitWaitError,
        dry_run_bounded_wait_projection,
        is_fully_stopped_onenote_desktop,
        wait_for_onenote_fully_stopped,
    )
    from manual_validation.path_budget import managed_absolute, preflight_paths
    from manual_validation.progress import VERBOSITY_LEVELS, RunProgressReporter
    from manual_validation.run_identity import RunIdentity, new_run_identity
    from manual_validation.test_utils import write_json
else:
    from .mcp_stdio_client import (
        ClientFailure,
        MCPStdioClient,
        READ_ONLY_POLICY,
        ScenarioPolicy,
    )
    from .onenote_exit_wait import (
        POLL_INTERVAL_SECONDS,
        OneNoteExitWaitError,
        dry_run_bounded_wait_projection,
        is_fully_stopped_onenote_desktop,
        wait_for_onenote_fully_stopped,
    )
    from .path_budget import managed_absolute, preflight_paths
    from .progress import VERBOSITY_LEVELS, RunProgressReporter
    from .run_identity import RunIdentity, new_run_identity
    from .test_utils import write_json


COMMAND = "launch-onenote-gui-check"
RUN_SCHEMA_VERSION = 2
MINIMUM_TIMEOUT_SECONDS = 20
REFRESH_REPEAT_COUNT = 3
UI_CONTROL_POLICY = ScenarioPolicy(ui_control_enabled=True)
ClientFactory = Callable[..., MCPStdioClient]
ConfirmationReader = Callable[[str], str]
TerminalCheck = Callable[[], bool]


class LaunchCheckFailure(RuntimeError):
    """A fail-closed GUI acceptance result."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_confirmation_reader(prompt: str) -> str:
    print(prompt, file=sys.stderr, flush=True)
    return sys.stdin.readline()


def _interactive_terminal() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty())


def _desktop_state(health: dict[str, Any], phase: str) -> dict[str, Any]:
    desktop = health.get("onenote_desktop")
    if not isinstance(desktop, dict):
        raise LaunchCheckFailure(f"{phase} omitted OneNote Desktop readiness evidence.")
    return desktop


def _require_fully_stopped(health: dict[str, Any], phase: str) -> None:
    desktop = _desktop_state(health, phase)
    if not is_fully_stopped_onenote_desktop(desktop):
        raise LaunchCheckFailure(
            f"{phase} requires OneNote Desktop to be fully exited before the check."
        )


def _require_ready(health: dict[str, Any], phase: str) -> None:
    desktop = _desktop_state(health, phase)
    if (
        desktop.get("process_running") is not True
        or desktop.get("visible_window_present") is not True
        or desktop.get("ready") is not True
    ):
        raise LaunchCheckFailure(
            f"{phase} did not prove a running OneNote process with a visible GUI."
        )


_REFRESH_OUTCOMES = frozenset(
    {
        "refreshed",
        "not_needed",
        "rejected_closed",
        "not_attempted",
        "host_discarded",
        "host_discard_unconfirmed",
    }
)


def _require_com_client_refresh(result: dict[str, Any], phase: str) -> dict[str, Any]:
    refresh = result.get("com_client_refresh")
    if not isinstance(refresh, dict) or refresh.get("outcome") not in _REFRESH_OUTCOMES:
        raise LaunchCheckFailure(
            f"{phase} did not project a content-free com_client_refresh outcome."
        )
    return refresh


def _require_positive_int(value: Any, field: str, phase: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LaunchCheckFailure(f"{phase} omitted a valid {field}.")
    return value


def _require_launch_ready(result: dict[str, Any], phase: str) -> None:
    if result.get("ready") is not True:
        raise LaunchCheckFailure(f"{phase} did not prove GUI readiness.")
    status = result.get("status")
    if status == "started":
        if result.get("launch_attempted") is not True or result.get("launch_attempts") != 1:
            raise LaunchCheckFailure(
                f"{phase} started status did not prove exactly one launch request."
            )
        return
    if status == "already_running":
        if result.get("launch_attempted") is not False or result.get("launch_attempts") != 0:
            raise LaunchCheckFailure(
                f"{phase} already_running status did not prove launch idempotency."
            )
        return
    raise LaunchCheckFailure(f"{phase} returned an unexpected launch status {status!r}.")


def _require_started_launch(result: dict[str, Any], phase: str) -> None:
    if (
        result.get("status") != "started"
        or result.get("launch_attempted") is not True
        or result.get("launch_attempts") != 1
        or result.get("ready") is not True
    ):
        raise LaunchCheckFailure(
            f"{phase} did not prove exactly one launch request and GUI readiness."
        )


def _require_already_running(result: dict[str, Any], phase: str) -> None:
    if (
        result.get("status") != "already_running"
        or result.get("launch_attempted") is not False
        or result.get("launch_attempts") != 0
        or result.get("ready") is not True
    ):
        raise LaunchCheckFailure(
            f"{phase} did not prove already-running launch idempotency."
        )


def _hierarchy_evidence(result: dict[str, Any], phase: str) -> dict[str, Any]:
    items = result.get("items")
    count = result.get("count")
    if not isinstance(items, list) or not isinstance(count, int) or count != len(items):
        raise LaunchCheckFailure(
            f"{phase} did not return a consistent typed hierarchy result."
        )
    return {
        "count": count,
        "operation_execution": result.get("execution"),
        "typed_hierarchy_read_passed": True,
    }


def _require_refreshed(
    refresh: dict[str, Any],
    phase: str,
    *,
    generation: int,
    minimum_epoch: int,
) -> int:
    if refresh.get("outcome") != "refreshed":
        raise LaunchCheckFailure(
            f"{phase} required refreshed; got {refresh.get('outcome')!r}."
        )
    got_generation = _require_positive_int(refresh.get("generation"), "generation", phase)
    if got_generation != generation:
        raise LaunchCheckFailure(
            f"{phase} changed host generation from {generation} to {got_generation}."
        )
    epoch = _require_positive_int(refresh.get("com_epoch"), "com_epoch", phase)
    if epoch < minimum_epoch:
        raise LaunchCheckFailure(
            f"{phase} com_epoch {epoch} was not strictly greater than the previous epoch."
        )
    return epoch


def _require_recovery_refresh(
    refresh: dict[str, Any],
    phase: str,
    *,
    previous_generation: int,
    previous_epoch: int,
) -> tuple[int | None, int]:
    outcome = refresh.get("outcome")
    if outcome == "refreshed":
        epoch = _require_refreshed(
            refresh,
            phase,
            generation=previous_generation,
            minimum_epoch=previous_epoch + 1,
        )
        return previous_generation, epoch
    if outcome == "host_discarded":
        discarded = refresh.get("discarded_generation")
        if discarded != previous_generation:
            raise LaunchCheckFailure(
                f"{phase} host_discarded omitted discarded_generation {previous_generation}."
            )
        return None, previous_epoch
    raise LaunchCheckFailure(
        f"{phase} recovery refresh must be refreshed or host_discarded; got {outcome!r}."
    )


def build_plan(
    identity: RunIdentity,
    run_dir: Path,
    timeout: int,
    verbosity: str = "normal",
) -> dict[str, Any]:
    """Return a zero-side-effect, machine-readable execution plan."""

    return {
        "agent_execution_prohibited": True,
        "command": COMMAND,
        "entry_type": "standalone_human_gated_acceptance_check",
        "human_only": True,
        "included_in_all": False,
        "registered_scenario": False,
        "run_dir": str(run_dir.resolve()),
        "run_identity": identity.as_dict(),
        "timeout_seconds": timeout,
        "verbosity": verbosity,
        "mcp_runtime_logging": {
            "bridge_audit_file": False,
            "calls_jsonl": False,
            "server_stderr_file": False,
            "structured_acceptance_evidence": True,
            "terminal_output": True,
        },
        "mcp_processes": [
            {
                "order": 1,
                "policy": READ_ONLY_POLICY.as_dict(),
                "tools": ["health_check", "launch_onenote_gui"],
                "purpose": "prove check-only health and pre-side-effect UI Control rejection",
            },
            {
                "order": 2,
                "policy": UI_CONTROL_POLICY.as_dict(),
                "tools": ["health_check", "launch_onenote_gui", "list_notebooks"],
                "purpose": (
                    "prove single start, host-establishing hierarchy read, warm refresh, "
                    "same-process recovery after OneNote close, repeated refreshed epochs, "
                    "follow-up health/hierarchy COM reads, and the human GUI verdict "
                    "while this MCP process is still alive"
                ),
            },
        ],
        "ordered_phases": [
            "interactive-begin-confirmation",
            "health-while-fully-stopped",
            "unauthorized-launch-rejection",
            "health-after-rejection",
            "authorized-single-launch",
            "ready-health-check",
            "establish-host-hierarchy-read",
            "warm-already-running-refresh",
            "human-onenote-closed-confirmation",
            "bounded-native-fully-stopped-wait",
            "recover-after-onenote-close",
            "recover-health-check",
            "recover-hierarchy-read",
            "repeated-already-running-refresh",
            "ready-health-at-human-verdict",
            "run-bound-human-gui-verdict",
        ],
        "refresh_repeat_count": REFRESH_REPEAT_COUNT,
        **dry_run_bounded_wait_projection(),
        "side_effects": {
            "starts_onenote": True,
            "closes_onenote": False,
            "creates_notebook": False,
            "mutates_notebook_content": False,
            "onenote_visible_at_verdict": True,
            "post_mcp_teardown_onenote_state": "not_asserted",
        },
    }


def _initial_state(identity: RunIdentity, run_dir: Path) -> dict[str, Any]:
    return {
        "agent_execution_prohibited": True,
        "command": COMMAND,
        "scenario": COMMAND,
        "schema_version": RUN_SCHEMA_VERSION,
        "human_only": True,
        "run_dir": str(run_dir.resolve()),
        "run_identity": identity.as_dict(),
        "started_at": _utc_now(),
        "status": "running",
        "current_step": "health-while-fully-stopped",
        "notebook_lifecycle": "not_applicable",
        "filesystem_deleted": False,
    }


def _require_policy_rejection(exc: ClientFailure) -> dict[str, Any]:
    envelope = exc.envelope
    if not isinstance(envelope, dict):
        raise LaunchCheckFailure(
            "Unauthorized launch did not return a structured MCP failure envelope."
        ) from exc
    error = envelope.get("error")
    execution = envelope.get("execution")
    if (
        not isinstance(error, dict)
        or error.get("code") != "policy_disabled"
        or not isinstance(execution, dict)
        or execution.get("operation") != "launch_onenote_gui"
        or execution.get("stage") != "authorization"
        or execution.get("backend_calls") != 0
    ):
        raise LaunchCheckFailure(
            "Unauthorized launch did not fail at the zero-backend authorization gate."
        ) from exc
    return envelope


async def _wait_until_fully_stopped(
    client: MCPStdioClient,
    *,
    timeout: int,
    wait_options: dict[str, Any],
) -> dict[str, Any]:
    async def probe() -> dict[str, Any]:
        return await client.call_health_preflight(allow_desktop_not_running=True)

    try:
        return await wait_for_onenote_fully_stopped(
            probe,
            timeout_seconds=float(wait_options.get("timeout_seconds", timeout)),
            poll_interval_seconds=float(
                wait_options.get("poll_interval_seconds", POLL_INTERVAL_SECONDS)
            ),
            sleep=wait_options.get("sleep"),
            monotonic=wait_options.get("monotonic"),
        )
    except OneNoteExitWaitError as exc:
        raise LaunchCheckFailure(str(exc)) from exc


async def _execute_protocol(
    *,
    run_dir: Path,
    timeout: int,
    client_factory: ClientFactory,
    confirmation_reader: ConfirmationReader,
    progress: RunProgressReporter,
    onenote_exit_wait: dict[str, Any] | None = None,
) -> dict[str, Any]:
    disabled_dir = run_dir / "ui-control-disabled-mcp"
    enabled_dir = run_dir / "ui-control-enabled-mcp"

    progress.phase_started("UI Control disabled proof", 1, 6)
    async with client_factory(
        policy=READ_ONLY_POLICY,
        allowed_tools={"launch_onenote_gui"},
        run_dir=disabled_dir,
        timeout_seconds=timeout,
        require_desktop_ready=False,
        persist_runtime_logs=False,
        progress=progress,
    ) as disabled:
        before = dict(disabled.health_result or {})
        _require_fully_stopped(before, "Initial health_check")
        write_json(run_dir / "health-before.json", before)
        try:
            await disabled.call_tool(
                "launch_onenote_gui", {}, retry_read=False
            )
        except ClientFailure as exc:
            rejection = _require_policy_rejection(exc)
        else:
            raise LaunchCheckFailure(
                "launch_onenote_gui unexpectedly succeeded while UI Control was disabled."
            )
        write_json(run_dir / "unauthorized-rejection.json", rejection)
        after_rejection = await disabled.call_health_preflight(
            allow_desktop_not_running=True
        )
        _require_fully_stopped(after_rejection, "Health after authorization rejection")
        write_json(run_dir / "health-after-rejection.json", after_rejection)
    progress.phase_completed("UI Control disabled proof")

    progress.phase_started("authorized single launch", 2, 6)
    async with client_factory(
        policy=UI_CONTROL_POLICY,
        allowed_tools={"launch_onenote_gui", "list_notebooks"},
        run_dir=enabled_dir,
        timeout_seconds=timeout,
        require_desktop_ready=False,
        persist_runtime_logs=False,
        progress=progress,
    ) as enabled:
        authorized_before = dict(enabled.health_result or {})
        _require_fully_stopped(authorized_before, "Authorized-client initial health_check")
        write_json(run_dir / "authorized-health-before.json", authorized_before)

        launched = await enabled.call_tool(
            "launch_onenote_gui", {}, retry_read=False
        )
        _require_started_launch(launched, "Authorized launch")
        _require_com_client_refresh(launched, "Authorized launch")
        write_json(run_dir / "authorized-launch.json", launched)
        progress.phase_completed("authorized single launch")

        progress.phase_started("establish host and warm refresh", 3, 6)
        ready_health = await enabled.call_health_preflight(
            allow_desktop_not_running=False
        )
        enabled.validate_health_contract(
            ready_health,
            require_desktop_ready=True,
        )
        _require_ready(ready_health, "Post-launch health_check")
        write_json(run_dir / "health-ready.json", ready_health)

        notebooks = await enabled.call_tool(
            "list_notebooks", {}, retry_read=False
        )
        hierarchy_evidence = _hierarchy_evidence(
            notebooks, "Host-establishing list_notebooks"
        )
        write_json(run_dir / "hierarchy-read.json", hierarchy_evidence)
        count = hierarchy_evidence["count"]

        warm = await enabled.call_tool(
            "launch_onenote_gui", {}, retry_read=False
        )
        _require_already_running(warm, "Warm already-running launch")
        warm_refresh = _require_com_client_refresh(warm, "Warm already-running launch")
        active_generation = _require_positive_int(
            warm_refresh.get("generation"),
            "generation",
            "Warm already-running launch",
        )
        active_epoch = _require_refreshed(
            warm_refresh,
            "Warm already-running launch",
            generation=active_generation,
            minimum_epoch=2,
        )
        write_json(run_dir / "warm-refresh-launch.json", warm)
        progress.phase_completed("establish host and warm refresh")

        progress.phase_started("recover after OneNote close", 4, 6)
        expected_closed = f"CLOSED {run_dir.name} ONENOTE CONTINUE"
        closed = confirmation_reader(
            "Fully exit OneNote Desktop now. Leave this MCP process running. "
            "After OneNote has exited, continue so the same process can recover the COM client.\n"
            f"Type exactly: {expected_closed}"
        ).strip()
        if closed != expected_closed:
            raise LaunchCheckFailure(
                "Run-bound OneNote-closed confirmation was not provided."
            )
        write_json(
            run_dir / "onenote-closed-confirmation.json",
            {
                "accepted": True,
                "confirmation_mode": "interactive_stdin",
                "confirmation_value_recorded": False,
                "onenote_closed_by_user": True,
                "mcp_process_kept": True,
            },
        )

        try:
            stopped_health = await _wait_until_fully_stopped(
                enabled,
                timeout=timeout,
                wait_options=onenote_exit_wait or {},
            )
        except LaunchCheckFailure as exc:
            evidence = getattr(exc.__cause__, "evidence", None)
            if isinstance(evidence, dict):
                write_json(run_dir / "health-after-user-close.json", evidence)
            raise
        write_json(run_dir / "health-after-user-close.json", stopped_health)

        recovered = await enabled.call_tool(
            "launch_onenote_gui", {}, retry_read=False
        )
        _require_launch_ready(recovered, "Recovery launch")
        recover_refresh = _require_com_client_refresh(recovered, "Recovery launch")
        active_generation, active_epoch = _require_recovery_refresh(
            recover_refresh,
            "Recovery launch",
            previous_generation=active_generation,
            previous_epoch=active_epoch,
        )
        write_json(run_dir / "recover-launch.json", recovered)

        recover_health = await enabled.call_health_preflight(
            allow_desktop_not_running=False
        )
        enabled.validate_health_contract(
            recover_health,
            require_desktop_ready=True,
        )
        _require_ready(recover_health, "Post-recovery health_check")
        write_json(run_dir / "recover-health.json", recover_health)

        recover_notebooks = await enabled.call_tool(
            "list_notebooks", {}, retry_read=False
        )
        recover_hierarchy = _hierarchy_evidence(
            recover_notebooks, "Post-recovery list_notebooks"
        )
        write_json(run_dir / "recover-hierarchy-read.json", recover_hierarchy)
        count = recover_hierarchy["count"]
        progress.phase_completed("recover after OneNote close")

        progress.phase_started("repeated already-running refresh", 5, 6)
        repeats: list[dict[str, Any]] = []
        for index in range(1, REFRESH_REPEAT_COUNT + 1):
            phase = f"Repeat {index} already-running launch"
            repeated = await enabled.call_tool(
                "launch_onenote_gui", {}, retry_read=False
            )
            _require_already_running(repeated, phase)
            repeat_refresh = _require_com_client_refresh(repeated, phase)
            if active_generation is None:
                discarded_generation = recover_refresh.get("discarded_generation")
                active_generation = _require_positive_int(
                    repeat_refresh.get("generation"),
                    "generation",
                    phase,
                )
                if (
                    not isinstance(discarded_generation, int)
                    or isinstance(discarded_generation, bool)
                    or active_generation <= discarded_generation
                ):
                    raise LaunchCheckFailure(
                        f"{phase} did not advance host generation past "
                        f"discarded_generation {discarded_generation}."
                    )
                active_epoch = _require_refreshed(
                    repeat_refresh,
                    phase,
                    generation=active_generation,
                    minimum_epoch=1,
                )
            else:
                active_epoch = _require_refreshed(
                    repeat_refresh,
                    phase,
                    generation=active_generation,
                    minimum_epoch=active_epoch + 1,
                )
            repeat_notebooks = await enabled.call_tool(
                "list_notebooks", {}, retry_read=False
            )
            repeat_hierarchy = _hierarchy_evidence(
                repeat_notebooks, f"Repeat {index} list_notebooks"
            )
            repeats.append(
                {
                    "index": index,
                    "launch": {
                        "status": repeated.get("status"),
                        "com_client_refresh": repeat_refresh,
                    },
                    "hierarchy": repeat_hierarchy,
                }
            )
            count = repeat_hierarchy["count"]
        write_json(
            run_dir / "refresh-repeats.json",
            {
                "count": len(repeats),
                "final_generation": active_generation,
                "final_com_epoch": active_epoch,
                "repeats": repeats,
            },
        )
        progress.phase_completed("repeated already-running refresh")

        progress.phase_started("human single-GUI verdict", 6, 6)
        verdict_health = await enabled.call_health_preflight(
            allow_desktop_not_running=False
        )
        write_json(run_dir / "health-at-human-verdict.json", verdict_health)
        enabled.validate_health_contract(
            verdict_health,
            require_desktop_ready=True,
        )
        _require_ready(verdict_health, "Health at human GUI verdict")
        expected_verdict = f"ACCEPT {run_dir.name} ONE VISIBLE ONENOTE GUI"
        verdict = confirmation_reader(
            "Inspect the desktop now while this MCP process is still running. "
            "Confirm that exactly one visible OneNote GUI exists "
            "and the already-running refresh calls opened no additional window.\n"
            f"Type exactly: {expected_verdict}"
        ).strip()
        if verdict != expected_verdict:
            raise LaunchCheckFailure(
                "Run-bound GUI verdict was not accepted."
            )
        user_verdict = {
            "accepted": True,
            "confirmation_mode": "interactive_stdin",
            "confirmation_value_recorded": False,
            "exactly_one_visible_gui": True,
            "already_running_refresh_opened_no_additional_window": True,
            "enabled_mcp_still_running": True,
        }
        write_json(run_dir / "user-verdict.json", user_verdict)
        progress.phase_completed("human single-GUI verdict")

    return {
        "agent_execution_prohibited": True,
        "command": COMMAND,
        "filesystem_deleted": False,
        "human_only": True,
        "included_in_all": False,
        "registered_scenario": False,
        "status": "passed",
        "checks": {
            "health_check_did_not_launch": True,
            "unauthorized_rejection_before_backend": True,
            "authorized_single_launch": True,
            "host_established_by_hierarchy_read": True,
            "warm_refresh_same_generation": True,
            "onenote_fully_stopped_after_user_close": True,
            "recovered_after_onenote_close": True,
            "post_recovery_health_ready": True,
            "post_recovery_hierarchy_read": True,
            "repeated_already_running_refresh": True,
            "onenote_ready_at_human_verdict": True,
            "human_single_gui_verdict": True,
        },
        "refresh_repeat_count": REFRESH_REPEAT_COUNT,
        "final_host_generation": active_generation,
        "final_com_epoch": active_epoch,
        "mcp_processes_started": 2,
        "notebook_count_observed": count,
        "onenote_visible_at_verdict": True,
        "post_mcp_teardown_onenote_state": "not_asserted",
        "mcp_runtime_logs_persisted": False,
        "mcp_runtime_logs_streamed_to_terminal": True,
        "user_verdict": user_verdict,
        "completed_at": _utc_now(),
    }


def run_real_check(
    *,
    identity: RunIdentity,
    run_dir: Path,
    timeout: int,
    client_factory: ClientFactory = MCPStdioClient,
    confirmation_reader: ConfirmationReader = _default_confirmation_reader,
    terminal_check: TerminalCheck = _interactive_terminal,
    progress: RunProgressReporter | None = None,
    onenote_exit_wait: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the user-only protocol and persist durable pass/fail evidence."""

    if not terminal_check():
        raise LaunchCheckFailure(
            "Real GUI acceptance requires an interactive foreground terminal; "
            "agents, CI, redirected stdin, timers, and background tasks are prohibited."
        )
    if timeout < MINIMUM_TIMEOUT_SECONDS:
        raise LaunchCheckFailure(
            f"--timeout must be at least {MINIMUM_TIMEOUT_SECONDS} seconds."
        )
    run_dir = managed_absolute(run_dir)
    if run_dir.exists():
        raise LaunchCheckFailure(f"Fresh evidence directory already exists: {run_dir}")
    planned_paths: list[tuple[Path, str, str | None]] = [
        (run_dir, "run_root", None),
    ]
    for evidence_name in (
        "run-state.json",
        "run-result.json",
        "run-failure.json",
        "health-before.json",
        "unauthorized-rejection.json",
        "health-after-rejection.json",
        "authorized-health-before.json",
        "authorized-launch.json",
        "health-ready.json",
        "hierarchy-read.json",
        "warm-refresh-launch.json",
        "onenote-closed-confirmation.json",
        "health-after-user-close.json",
        "recover-launch.json",
        "recover-health.json",
        "recover-hierarchy-read.json",
        "refresh-repeats.json",
        "health-at-human-verdict.json",
        "user-verdict.json",
    ):
        evidence_path = run_dir / evidence_name
        planned_paths.extend(
            (
                (evidence_path, "run_evidence", None),
                (
                    evidence_path.with_name(
                        f".{evidence_path.name}.{'0' * 16}.tmp"
                    ),
                    "atomic_metadata_temp",
                    None,
                ),
            )
        )
    for process_dir_name in (
        "ui-control-disabled-mcp",
        "ui-control-enabled-mcp",
    ):
        process_dir = run_dir / process_dir_name
        planned_paths.append((process_dir / "temp", "temporary_root", None))
    preflight_paths(planned_paths, phase="launch_gui_check_path_preflight")

    expected_begin = f"BEGIN {run_dir.name} LAUNCH ONENOTE GUI CHECK"
    begin = confirmation_reader(
        "Fully exit OneNote Desktop before continuing. This check will make one "
        "authorized start, then later ask you to close OneNote once so the same "
        "MCP process can recover the COM client. Confirm the visible GUI while "
        "that MCP process is still running; OneNote state after teardown is not asserted.\n"
        f"Type exactly: {expected_begin}"
    ).strip()
    if begin != expected_begin:
        raise LaunchCheckFailure("Run-bound begin confirmation was not provided.")

    state = _initial_state(identity, run_dir)
    write_json(run_dir / "run-state.json", state)
    progress = progress or RunProgressReporter.disabled()
    progress.run_started(COMMAND, run_dir)
    try:
        result = asyncio.run(
            _execute_protocol(
                run_dir=run_dir,
                timeout=timeout,
                client_factory=client_factory,
                confirmation_reader=confirmation_reader,
                progress=progress,
                onenote_exit_wait=onenote_exit_wait,
            )
        )
    except Exception as exc:
        failure = {
            "agent_execution_prohibited": True,
            "command": COMMAND,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "failed_at": _utc_now(),
            "filesystem_deleted": False,
            "human_only": True,
            "onenote_may_be_running": (run_dir / "authorized-launch.json").is_file(),
            "status": "failed_preserved",
        }
        write_json(run_dir / "run-failure.json", failure)
        progress.failure(str(exc), run_dir=run_dir)
        state.update(
            status="failed_preserved",
            current_step=None,
            error=str(exc),
            failed_at=failure["failed_at"],
        )
        write_json(run_dir / "run-state.json", state)
        if isinstance(exc, LaunchCheckFailure):
            raise
        raise LaunchCheckFailure(str(exc)) from exc

    result["run_dir"] = str(run_dir)
    result["run_identity"] = identity.as_dict()
    write_json(run_dir / "run-result.json", result)
    state.update(
        status="passed",
        current_step=None,
        completed_at=result["completed_at"],
    )
    write_json(run_dir / "run-state.json", state)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "HUMAN-GATED standalone launch_onenote_gui acceptance check. This is "
            "not a Scenario and never enters run.py all."
        )
    )
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument(
        "--verbosity",
        choices=VERBOSITY_LEVELS,
        default="normal",
        help="Terminal detail: quiet, normal (default), or verbose.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    identity = new_run_identity()
    run_dir = args.run_dir or Path(".local-validation") / f"run-{identity.safe_timestamp}"
    plan = build_plan(identity, run_dir, args.timeout, args.verbosity)
    if args.dry_run:
        if args.json_output:
            print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    try:
        progress = RunProgressReporter(
            args.verbosity,
            writer=lambda line: print(line, file=sys.stderr, flush=True),
        )
        result = run_real_check(
            identity=identity,
            run_dir=run_dir,
            timeout=args.timeout,
            progress=progress,
        )
    except LaunchCheckFailure as exc:
        payload = {"ok": False, "error": str(exc), "exit_code": 1}
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("PASSED: launch_onenote_gui standalone acceptance check")
        print(f"Evidence: {result['run_dir']}")
        print(
            "OneNote was visible at the human verdict; "
            "state after MCP teardown is not asserted."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
