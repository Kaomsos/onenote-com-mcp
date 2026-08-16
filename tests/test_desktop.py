from __future__ import annotations

import pytest
import inspect
from pathlib import Path

from local_onenote_mcp import desktop
from local_onenote_mcp.desktop import OneNoteDesktopState
from local_onenote_mcp.onenote_errors import (
    OneNoteDesktopLaunchTimeoutError,
    OneNoteDesktopNotRunningError,
    OneNoteDesktopProbeError,
    OneNoteDesktopWindowUnavailableError,
)


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


@pytest.mark.parametrize(
    "state",
    (
        OneNoteDesktopState(False, False),
        OneNoteDesktopState(True, False),
        OneNoteDesktopState(False, True),
    ),
)
def test_require_desktop_fails_closed_without_ready_visible_gui(
    monkeypatch, state
) -> None:
    monkeypatch.setattr(
        desktop,
        "probe_onenote_desktop",
        lambda: state,
    )

    with pytest.raises(OneNoteDesktopNotRunningError) as raised:
        desktop.require_onenote_desktop(
            operation="create_page", ui_control_enabled=False
        )

    assert raised.value.code == "onenote_desktop_not_running"
    assert raised.value.retryability == "after_user_action"
    assert raised.value.operation == "create_page"
    assert (
        raised.value.details["required_action"]
        == "restore_onenote_gui_readiness_and_retry"
    )
    assert raised.value.details["failed_precondition"] == "onenote_gui_ready"
    assert raised.value.details["onenote_desktop"]["ready"] is False
    assert raised.value.details["recovery"] == {
        "sequence": [
            "health_check",
            "launch_onenote_gui",
            "health_check",
            "retry_original_operation",
        ],
        "launch_requires_gate": "LOCAL_ONENOTE_ENABLE_UI_CONTROL=true",
        "manual_alternative": "Start OneNote Desktop manually with a visible window.",
        "ui_control_enabled": False,
        "required_user_action": (
            "Enable LOCAL_ONENOTE_ENABLE_UI_CONTROL=true and restart the MCP "
            "server, or start OneNote Desktop manually."
        ),
    }


def test_require_desktop_projects_uncertain_probe_as_failed_precondition(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        desktop,
        "probe_onenote_desktop",
        lambda: (_ for _ in ()).throw(
            OneNoteDesktopProbeError(
                "probe failed", operation="health_preflight"
            )
        ),
    )

    with pytest.raises(OneNoteDesktopProbeError) as raised:
        desktop.require_onenote_desktop(
            operation="delete_page", ui_control_enabled=True
        )

    assert raised.value.operation == "delete_page"
    assert raised.value.details["failed_precondition"] == "onenote_gui_ready"
    assert raised.value.details["recovery"]["ui_control_enabled"] is True


def test_require_desktop_accepts_existing_visible_gui(monkeypatch) -> None:
    expected = OneNoteDesktopState(True, True)
    monkeypatch.setattr(desktop, "probe_onenote_desktop", lambda: expected)

    assert desktop.require_onenote_desktop() is expected


def test_native_probe_has_no_com_or_process_launch_path() -> None:
    source = inspect.getsource(desktop.probe_onenote_desktop)

    for forbidden in (
        "OneNote.Application",
        "OneNoteBridge",
        "subprocess",
        "Popen",
        "Start-Process",
        "ShellExecute",
    ):
        assert forbidden not in source


def test_launch_returns_already_running_without_resolving_or_starting() -> None:
    calls: list[str] = []

    result = desktop.launch_onenote_gui(
        probe=lambda: OneNoteDesktopState(True, True),
        resolver=lambda: calls.append("resolve"),
        process_launcher=lambda _path: calls.append("launch"),
    )

    assert result["status"] == "already_running"
    assert result["launch_attempted"] is False
    assert result["launch_attempts"] == 0
    assert result["ready"] is True
    assert calls == []


def test_launch_starts_once_and_waits_for_visible_gui(tmp_path) -> None:
    executable = tmp_path / "ONENOTE.EXE"
    states = iter(
        (
            OneNoteDesktopState(False, False),
            OneNoteDesktopState(False, False),
            OneNoteDesktopState(True, True),
        )
    )
    launches: list[Path] = []
    now = [0.0]

    result = desktop.launch_onenote_gui(
        probe=lambda: next(states),
        resolver=lambda: executable,
        process_launcher=launches.append,
        clock=lambda: now[0],
        sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
        timeout_seconds=1,
    )

    assert result["status"] == "started"
    assert result["launch_attempts"] == 1
    assert result["ready"] is True
    assert launches == [executable]


def test_launch_refuses_duplicate_start_when_process_has_no_window() -> None:
    launches: list[Path] = []

    with pytest.raises(OneNoteDesktopWindowUnavailableError) as raised:
        desktop.launch_onenote_gui(
            probe=lambda: OneNoteDesktopState(True, False),
            resolver=lambda: Path("unused"),
            process_launcher=launches.append,
        )

    assert raised.value.details["launch_attempts"] == 0
    assert launches == []


def test_launch_times_out_after_exactly_one_start(tmp_path) -> None:
    executable = tmp_path / "ONENOTE.EXE"
    launches: list[Path] = []
    now = [0.0]

    with pytest.raises(OneNoteDesktopLaunchTimeoutError) as raised:
        desktop.launch_onenote_gui(
            probe=lambda: OneNoteDesktopState(False, False),
            resolver=lambda: executable,
            process_launcher=launches.append,
            clock=lambda: now[0],
            sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds),
            timeout_seconds=0.3,
        )

    assert raised.value.details["launch_attempts"] == 1
    assert launches == [executable]


def test_registered_executable_accepts_only_exact_local_onenote_binary(tmp_path) -> None:
    executable = tmp_path / "ONENOTE.EXE"
    executable.write_bytes(b"test executable placeholder")

    assert desktop._registered_executable(f'"{executable}" /Embedding') == executable.resolve()
    with pytest.raises(ValueError, match="unsupported registered arguments"):
        desktop._registered_executable(f'"{executable}" /unsafe')
    with pytest.raises(ValueError, match="not ONENOTE.EXE"):
        other = tmp_path / "OTHER.EXE"
        other.write_bytes(b"not OneNote")
        desktop._registered_executable(f'"{other}"')


def test_registered_executable_rejects_reparse_target_before_resolution(
    tmp_path, monkeypatch
) -> None:
    executable = tmp_path / "ONENOTE.EXE"
    executable.write_bytes(b"test executable placeholder")
    original_lstat = desktop.os.lstat

    class ReparseStat:
        st_file_attributes = 0x400

    monkeypatch.setattr(
        desktop.os,
        "lstat",
        lambda path: ReparseStat()
        if Path(path) == executable
        else original_lstat(path),
    )

    with pytest.raises(ValueError, match="reparse point"):
        desktop._registered_executable(f'"{executable}"')


def test_registry_resolution_follows_progid_clsid_localserver_chain(tmp_path) -> None:
    executable = tmp_path / "ONENOTE.EXE"
    executable.write_bytes(b"test executable placeholder")
    clsid = "{DC67E480-C3CB-49F8-8232-60B0C2056C8E}"

    class Key:
        def __init__(self, name: str) -> None:
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class FakeWinreg:
        KEY_READ = 1
        KEY_WOW64_64KEY = 2
        KEY_WOW64_32KEY = 4
        HKEY_CLASSES_ROOT = object()
        REG_SZ = 1
        REG_EXPAND_SZ = 2

        values = {
            "OneNote.Application\\CLSID": (clsid, REG_SZ),
            f"CLSID\\{clsid}\\LocalServer32": (str(executable), REG_SZ),
        }

        @classmethod
        def OpenKey(cls, root, name, _reserved, _access):
            assert root is cls.HKEY_CLASSES_ROOT
            if name not in cls.values:
                raise FileNotFoundError(name)
            return Key(name)

        @classmethod
        def QueryValueEx(cls, key, value_name):
            assert value_name is None
            return cls.values[key.name]

    assert (
        desktop._resolve_registered_onenote_localserver(FakeWinreg)
        == executable.resolve()
    )


def test_registry_resolution_rejects_noncanonical_clsid_without_opening_target(tmp_path) -> None:
    opened: list[str] = []

    class Key:
        def __init__(self, name: str) -> None:
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class FakeWinreg:
        KEY_READ = 1
        KEY_WOW64_64KEY = 0
        KEY_WOW64_32KEY = 0
        HKEY_CLASSES_ROOT = object()
        REG_SZ = 1
        REG_EXPAND_SZ = 2

        @classmethod
        def OpenKey(cls, _root, name, _reserved, _access):
            opened.append(name)
            if name.endswith("\\CLSID"):
                return Key(name)
            raise AssertionError("invalid CLSID must not form a registry path")

        @staticmethod
        def QueryValueEx(_key, _value_name):
            return ("..\\unsafe", FakeWinreg.REG_SZ)

    assert desktop._resolve_registered_onenote_localserver(FakeWinreg) is None
    assert all("LocalServer32" not in name for name in opened)
