from __future__ import annotations

import pytest
import inspect

from local_onenote_mcp import desktop
from local_onenote_mcp.desktop import OneNoteDesktopState
from local_onenote_mcp.onenote_errors import OneNoteDesktopNotRunningError


def test_desktop_state_requires_process_and_visible_window() -> None:
    assert OneNoteDesktopState(True, True).ready is True
    assert OneNoteDesktopState(True, False).ready is False
    assert OneNoteDesktopState(False, True).ready is False
    assert OneNoteDesktopState(False, False).as_dict() == {
        "process_running": False,
        "visible_window_present": False,
        "ready": False,
        "probe": "native_windows_process_and_visible_window",
    }


def test_require_desktop_fails_closed_without_starting_any_process(monkeypatch) -> None:
    monkeypatch.setattr(
        desktop,
        "probe_onenote_desktop",
        lambda: OneNoteDesktopState(False, False),
    )

    with pytest.raises(OneNoteDesktopNotRunningError) as raised:
        desktop.require_onenote_desktop()

    assert raised.value.code == "onenote_desktop_not_running"
    assert raised.value.retryability == "after_user_action"
    assert raised.value.details["required_action"] == "start_onenote_desktop_and_retry"
    assert raised.value.details["onenote_desktop"]["ready"] is False


def test_require_desktop_accepts_existing_visible_gui(monkeypatch) -> None:
    expected = OneNoteDesktopState(True, True)
    monkeypatch.setattr(desktop, "probe_onenote_desktop", lambda: expected)

    assert desktop.require_onenote_desktop() is expected


def test_native_probe_has_no_com_or_process_launch_path() -> None:
    source = inspect.getsource(desktop)

    for forbidden in (
        "OneNote.Application",
        "OneNoteBridge",
        "subprocess",
        "Popen",
        "Start-Process",
        "ShellExecute",
    ):
        assert forbidden not in source
