"""Bounded native wait for OneNote Desktop to become fully stopped.

This helper is shared by the standalone GUI check and ``com-refresh-mutation``.
It only interprets native ``health_check`` observations: no launch, hierarchy,
mutation, process kill, or generic retry framework.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Mapping

from .mcp_stdio_client import ClientFailure


NATIVE_DESKTOP_PROBE = "native_windows_process_and_visible_window"
POLL_INTERVAL_SECONDS = 0.25
_DESKTOP_FLAGS = ("process_running", "visible_window_present", "ready")

HealthProbe = Callable[[], Awaitable[dict[str, Any]]]
Sleep = Callable[[float], Awaitable[None]]
Monotonic = Callable[[], float]


class OneNoteExitWaitError(RuntimeError):
    """A fail-closed fully-stopped wait result with durable evidence."""

    def __init__(self, message: str, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


def is_fully_stopped_onenote_desktop(desktop: Any) -> bool:
    """Same fully-stopped predicate used by both acceptance entries."""

    return (
        isinstance(desktop, dict)
        and desktop.get("process_running") is False
        and desktop.get("visible_window_present") is False
        and desktop.get("ready") is False
    )


def classify_onenote_desktop(desktop: Any) -> str:
    if not _well_formed_desktop(desktop):
        return "unexpected_probe_or_envelope"
    if is_fully_stopped_onenote_desktop(desktop):
        return "fully_stopped"
    if (
        desktop.get("process_running") is True
        and desktop.get("visible_window_present") is False
        and desktop.get("ready") is False
    ):
        return "process_running_without_window"
    if desktop.get("visible_window_present") is True:
        return "window_still_present"
    return "not_fully_stopped"


def dry_run_bounded_wait_projection() -> dict[str, Any]:
    return {
        "bounded_native_fully_stopped_wait": True,
        "allowed_operations": ["health_check"],
        "stdin_read_performed": False,
        "sleep_performed": False,
        "gui_state_read": False,
    }


def project_onenote_desktop(desktop: Mapping[str, Any]) -> dict[str, Any]:
    projected = {key: desktop[key] for key in _DESKTOP_FLAGS}
    probe = desktop.get("probe")
    if probe is not None:
        projected["probe"] = probe
    return projected


def _well_formed_desktop(desktop: Any) -> bool:
    if not isinstance(desktop, dict):
        return False
    if any(not isinstance(desktop.get(key), bool) for key in _DESKTOP_FLAGS):
        return False
    probe = desktop.get("probe")
    if probe is not None and probe != NATIVE_DESKTOP_PROBE:
        return False
    expected_ready = (
        desktop["process_running"] is True and desktop["visible_window_present"] is True
    )
    return desktop["ready"] is expected_ready


def _observation_from_desktop(desktop: Any) -> dict[str, Any]:
    classification = classify_onenote_desktop(desktop)
    projected = project_onenote_desktop(desktop) if _well_formed_desktop(desktop) else None
    if classification == "fully_stopped":
        decision = "fully_stopped"
    elif classification == "unexpected_probe_or_envelope":
        decision = "unexpected"
    else:
        decision = "wait"
    return {
        "decision": decision,
        "classification": classification,
        "desktop": projected,
    }


def inspect_health_result(health: Any) -> dict[str, Any]:
    if not isinstance(health, dict):
        return _observation_from_desktop(None)
    return _observation_from_desktop(health.get("onenote_desktop"))


def inspect_health_failure(exc: BaseException) -> dict[str, Any]:
    envelope = getattr(exc, "envelope", None)
    if not isinstance(envelope, dict):
        return _observation_from_desktop(None)
    error = envelope.get("error")
    details = error.get("details") if isinstance(error, dict) else None
    desktop = details.get("onenote_desktop") if isinstance(details, dict) else None
    execution = envelope.get("execution")
    if (
        not isinstance(error, dict)
        or error.get("code") != "onenote_desktop_not_running"
        or not isinstance(execution, dict)
        or execution.get("operation") != "health_check"
        or execution.get("backend_calls") != 0
    ):
        return _observation_from_desktop(None)
    return _observation_from_desktop(desktop)


def _evidence(
    *,
    status: str,
    attempts: int,
    elapsed_seconds: float,
    desktop: dict[str, Any] | None,
    classification: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "attempts": attempts,
        "elapsed_seconds": round(elapsed_seconds, 6),
    }
    if desktop is not None:
        payload["last_onenote_desktop"] = desktop
    if classification is not None:
        payload["classification"] = classification
    return payload


async def wait_for_onenote_fully_stopped(
    health_probe: HealthProbe,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
    sleep: Sleep | None = None,
    monotonic: Monotonic | None = None,
) -> dict[str, Any]:
    """Poll native health until fully stopped, then return compact evidence."""

    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative.")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive.")
    sleeper = sleep or asyncio.sleep
    clock = monotonic or time.monotonic
    started = clock()
    deadline = started + timeout_seconds
    attempts = 0
    last_desktop: dict[str, Any] | None = None
    last_classification = "unexpected_probe_or_envelope"

    while True:
        try:
            observation = inspect_health_result(await health_probe())
        except ClientFailure as exc:
            observation = inspect_health_failure(exc)
        attempts += 1
        last_desktop = observation["desktop"]
        last_classification = str(observation["classification"])
        elapsed = max(0.0, clock() - started)
        if observation["decision"] == "fully_stopped":
            return _evidence(
                status="fully_stopped",
                attempts=attempts,
                elapsed_seconds=elapsed,
                desktop=last_desktop,
                classification="fully_stopped",
            )
        if observation["decision"] == "unexpected":
            evidence = _evidence(
                status="unexpected_probe_or_envelope",
                attempts=attempts,
                elapsed_seconds=elapsed,
                desktop=last_desktop,
                classification="unexpected_probe_or_envelope",
            )
            raise OneNoteExitWaitError(
                "Native health after user close returned an unexpected probe or envelope.",
                evidence,
            )
        remaining = deadline - clock()
        if remaining <= 0:
            evidence = _evidence(
                status="timeout",
                attempts=attempts,
                elapsed_seconds=max(0.0, clock() - started),
                desktop=last_desktop,
                classification=last_classification,
            )
            raise OneNoteExitWaitError(
                "OneNote Desktop did not become fully stopped before recovery launch "
                f"({last_classification}).",
                evidence,
            )
        await sleeper(min(poll_interval_seconds, remaining))


__all__ = [
    "NATIVE_DESKTOP_PROBE",
    "POLL_INTERVAL_SECONDS",
    "OneNoteExitWaitError",
    "classify_onenote_desktop",
    "dry_run_bounded_wait_projection",
    "inspect_health_failure",
    "inspect_health_result",
    "is_fully_stopped_onenote_desktop",
    "project_onenote_desktop",
    "wait_for_onenote_fully_stopped",
]
