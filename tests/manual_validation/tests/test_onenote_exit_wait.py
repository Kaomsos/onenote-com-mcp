from __future__ import annotations

import asyncio

import pytest

from tests.manual_validation import launch_onenote_gui_check as gui_check
from tests.manual_validation.mcp_stdio_client import ClientFailure
from tests.manual_validation.onenote_exit_wait import (
    NATIVE_DESKTOP_PROBE,
    OneNoteExitWaitError,
    classify_onenote_desktop,
    inspect_health_failure,
    inspect_health_result,
    is_fully_stopped_onenote_desktop,
    wait_for_onenote_fully_stopped,
)
from tests.manual_validation.scenarios import com_refresh_mutation


def _desktop(
    *,
    process_running: bool,
    visible_window_present: bool,
    ready: bool | None = None,
    probe: str = NATIVE_DESKTOP_PROBE,
) -> dict:
    return {
        "process_running": process_running,
        "visible_window_present": visible_window_present,
        "ready": process_running and visible_window_present if ready is None else ready,
        "probe": probe,
    }


def _health(desktop: dict) -> dict:
    return {"onenote_desktop": desktop}


def _not_running_failure(desktop: dict) -> ClientFailure:
    return ClientFailure(
        "health_check failed (onenote_desktop_not_running): visible GUI required",
        envelope={
            "ok": False,
            "error": {
                "code": "onenote_desktop_not_running",
                "message": "The operation requires OneNote Desktop to be running with a visible GUI.",
                "details": {"onenote_desktop": desktop},
            },
            "execution": {"operation": "health_check", "backend_calls": 0},
        },
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_gui_and_mutation_share_fully_stopped_predicate() -> None:
    assert gui_check.is_fully_stopped_onenote_desktop is is_fully_stopped_onenote_desktop
    assert (
        com_refresh_mutation.is_fully_stopped_onenote_desktop
        is is_fully_stopped_onenote_desktop
    )
    stopped = _desktop(process_running=False, visible_window_present=False)
    running = _desktop(process_running=True, visible_window_present=False)
    assert is_fully_stopped_onenote_desktop(stopped) is True
    assert is_fully_stopped_onenote_desktop(running) is False
    assert classify_onenote_desktop(running) == "process_running_without_window"
    assert (
        classify_onenote_desktop(
            _desktop(process_running=True, visible_window_present=True)
        )
        == "window_still_present"
    )


def test_inspect_admits_typed_async_exit_envelope_as_wait() -> None:
    observation = inspect_health_failure(
        _not_running_failure(
            _desktop(process_running=True, visible_window_present=False)
        )
    )
    assert observation["decision"] == "wait"
    assert observation["classification"] == "process_running_without_window"


def test_inspect_rejects_unexpected_probe_immediately() -> None:
    observation = inspect_health_failure(
        ClientFailure(
            "probe failed",
            envelope={
                "ok": False,
                "error": {"code": "onenote_desktop_probe_failed", "message": "probe"},
                "execution": {"operation": "health_check", "backend_calls": 0},
            },
        )
    )
    assert observation["decision"] == "unexpected"
    assert inspect_health_result({"onenote_desktop": {"ready": False}})["decision"] == (
        "unexpected"
    )


def test_wait_succeeds_on_first_fully_stopped_without_sleep() -> None:
    clock = _Clock()
    stopped = _health(_desktop(process_running=False, visible_window_present=False))

    async def probe():
        return stopped

    evidence = asyncio.run(
        wait_for_onenote_fully_stopped(
            probe,
            timeout_seconds=0.75,
            poll_interval_seconds=0.25,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    )
    assert evidence["status"] == "fully_stopped"
    assert evidence["attempts"] == 1
    assert evidence["last_onenote_desktop"]["ready"] is False
    assert clock.sleeps == []


def test_wait_polls_process_only_then_fully_stopped() -> None:
    clock = _Clock()
    states = [
        _not_running_failure(
            _desktop(process_running=True, visible_window_present=False)
        ),
        _health(_desktop(process_running=False, visible_window_present=False)),
    ]

    async def probe():
        item = states.pop(0)
        if isinstance(item, ClientFailure):
            raise item
        return item

    evidence = asyncio.run(
        wait_for_onenote_fully_stopped(
            probe,
            timeout_seconds=0.75,
            poll_interval_seconds=0.25,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    )
    assert evidence["status"] == "fully_stopped"
    assert evidence["attempts"] == 2
    assert evidence["last_onenote_desktop"] == _desktop(
        process_running=False, visible_window_present=False
    )
    assert clock.sleeps == [0.25]


def test_wait_times_out_when_never_fully_stopped() -> None:
    clock = _Clock()

    async def probe():
        return _health(_desktop(process_running=True, visible_window_present=False))

    with pytest.raises(OneNoteExitWaitError, match="process_running_without_window") as caught:
        asyncio.run(
            wait_for_onenote_fully_stopped(
                probe,
                timeout_seconds=0.75,
                poll_interval_seconds=0.25,
                sleep=clock.sleep,
                monotonic=clock.monotonic,
            )
        )
    evidence = caught.value.evidence
    assert evidence["status"] == "timeout"
    assert evidence["classification"] == "process_running_without_window"
    assert evidence["last_onenote_desktop"]["process_running"] is True
    assert evidence["last_onenote_desktop"]["visible_window_present"] is False
    assert evidence["attempts"] >= 2
    assert clock.sleeps


def test_wait_fails_closed_on_unexpected_envelope_without_extra_wait() -> None:
    clock = _Clock()

    async def probe():
        raise ClientFailure(
            "probe failed",
            envelope={
                "ok": False,
                "error": {"code": "onenote_desktop_probe_failed", "message": "probe"},
                "execution": {"operation": "health_check", "backend_calls": 0},
            },
        )

    with pytest.raises(OneNoteExitWaitError, match="unexpected probe or envelope") as caught:
        asyncio.run(
            wait_for_onenote_fully_stopped(
                probe,
                timeout_seconds=0.75,
                poll_interval_seconds=0.25,
                sleep=clock.sleep,
                monotonic=clock.monotonic,
            )
        )
    assert caught.value.evidence["status"] == "unexpected_probe_or_envelope"
    assert caught.value.evidence["attempts"] == 1
    assert clock.sleeps == []
