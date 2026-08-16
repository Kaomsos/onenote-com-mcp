"""Read-only Windows preflight for an already running OneNote Desktop GUI."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

from .onenote_errors import (
    OneNoteDesktopExecutableError,
    OneNoteDesktopLaunchError,
    OneNoteDesktopLaunchTimeoutError,
    OneNoteDesktopNotRunningError,
    OneNoteDesktopProbeError,
    OneNoteDesktopWindowUnavailableError,
)


ONENOTE_PROCESS_NAME = "ONENOTE.EXE"
ONENOTE_GUI_LAUNCH_TIMEOUT_SECONDS = 15.0
ONENOTE_GUI_READINESS_POLL_SECONDS = 0.25


@dataclass(frozen=True)
class OneNoteDesktopState:
    """Content-free process/window evidence collected without COM activation."""

    process_running: bool
    visible_window_present: bool

    @property
    def ready(self) -> bool:
        return self.process_running and self.visible_window_present

    def as_dict(self) -> dict[str, Any]:
        return {
            "process_running": self.process_running,
            "visible_window_present": self.visible_window_present,
            "ready": self.ready,
            "probe": "native_windows_process_and_visible_window",
        }


def probe_onenote_desktop() -> OneNoteDesktopState:
    """Inspect ONENOTE.EXE and its visible top-level windows without starting COM."""

    if sys.platform != "win32":
        raise OneNoteDesktopProbeError(
            "OneNote Desktop readiness can only be checked on Windows.",
            operation="health_preflight",
        )

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        create_snapshot = kernel32.CreateToolhelp32Snapshot
        create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        create_snapshot.restype = wintypes.HANDLE
        process_first = kernel32.Process32FirstW
        process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        process_first.restype = wintypes.BOOL
        process_next = kernel32.Process32NextW
        process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        process_next.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        snapshot = create_snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        invalid_handle = ctypes.c_void_p(-1).value
        if snapshot == invalid_handle:
            raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
        process_ids: set[int] = set()
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ctypes.set_last_error(0)
            if not process_first(snapshot, ctypes.byref(entry)):
                raise OSError(ctypes.get_last_error(), "Process32FirstW failed")
            while True:
                if entry.szExeFile.casefold() == ONENOTE_PROCESS_NAME.casefold():
                    process_ids.add(int(entry.th32ProcessID))
                ctypes.set_last_error(0)
                if not process_next(snapshot, ctypes.byref(entry)):
                    error_code = ctypes.get_last_error()
                    if error_code not in (0, 18):  # ERROR_NO_MORE_FILES
                        raise OSError(error_code, "Process32NextW failed")
                    break
        finally:
            close_handle(snapshot)

        if not process_ids:
            return OneNoteDesktopState(False, False)

        visible_window_present = False
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        get_window_process_id = user32.GetWindowThreadProcessId
        get_window_process_id.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        get_window_process_id.restype = wintypes.DWORD
        is_window_visible = user32.IsWindowVisible
        is_window_visible.argtypes = [wintypes.HWND]
        is_window_visible.restype = wintypes.BOOL
        get_window = user32.GetWindow
        get_window.argtypes = [wintypes.HWND, wintypes.UINT]
        get_window.restype = wintypes.HWND

        @callback_type
        def visit(hwnd: int, _lparam: int) -> bool:
            nonlocal visible_window_present
            process_id = wintypes.DWORD()
            get_window_process_id(hwnd, ctypes.byref(process_id))
            if (
                int(process_id.value) in process_ids
                and bool(is_window_visible(hwnd))
                and not get_window(hwnd, 4)  # GW_OWNER
            ):
                visible_window_present = True
                return False
            return True

        enum_windows = user32.EnumWindows
        enum_windows.argtypes = [callback_type, wintypes.LPARAM]
        enum_windows.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        completed = enum_windows(visit, 0)
        if not completed and not visible_window_present:
            error_code = ctypes.get_last_error()
            if error_code:
                raise OSError(error_code, "EnumWindows failed")
        return OneNoteDesktopState(True, visible_window_present)
    except OneNoteDesktopProbeError:
        raise
    except Exception as exc:
        raise OneNoteDesktopProbeError(
            "OneNote Desktop readiness could not be determined safely.",
            operation="health_preflight",
        ) from exc


def _readiness_recovery(
    *, ui_control_enabled: bool | None,
) -> dict[str, Any]:
    recovery: dict[str, Any] = {
        "sequence": [
            "health_check",
            "launch_onenote_gui",
            "health_check",
            "retry_original_operation",
        ],
        "launch_requires_gate": "LOCAL_ONENOTE_ENABLE_UI_CONTROL=true",
        "manual_alternative": "Start OneNote Desktop manually with a visible window.",
    }
    if ui_control_enabled is not None:
        recovery["ui_control_enabled"] = ui_control_enabled
        if not ui_control_enabled:
            recovery["required_user_action"] = (
                "Enable LOCAL_ONENOTE_ENABLE_UI_CONTROL=true and restart the MCP "
                "server, or start OneNote Desktop manually."
            )
    return recovery


def require_onenote_desktop(
    *,
    operation: str = "health_preflight",
    ui_control_enabled: bool | None = None,
) -> OneNoteDesktopState:
    """Fail closed unless an existing OneNote Desktop GUI is ready."""

    try:
        state = probe_onenote_desktop()
    except OneNoteDesktopProbeError as exc:
        if operation == "health_preflight" and ui_control_enabled is None:
            raise
        raise OneNoteDesktopProbeError(
            "The OneNote Desktop visible-GUI prerequisite could not be determined safely.",
            operation=operation,
            details={
                "failed_precondition": "onenote_gui_ready",
                "recovery": _readiness_recovery(
                    ui_control_enabled=ui_control_enabled
                ),
            },
        ) from exc
    if not state.ready:
        raise OneNoteDesktopNotRunningError(
            "The operation requires OneNote Desktop to be running with a visible GUI.",
            operation=operation,
            details={
                "failed_precondition": "onenote_gui_ready",
                "onenote_desktop": state.as_dict(),
                "required_action": "restore_onenote_gui_readiness_and_retry",
                "recovery": _readiness_recovery(
                    ui_control_enabled=ui_control_enabled
                ),
            },
        )
    return state


def _registered_executable(command: str) -> Path:
    """Parse one trusted LocalServer32 value without executing command text."""

    value = command.strip()
    if not value:
        raise ValueError("empty registration")
    if value.startswith('"'):
        closing = value.find('"', 1)
        if closing < 0:
            raise ValueError("unterminated quoted executable")
        executable = value[1:closing]
        remainder = value[closing + 1 :].strip()
        if remainder and remainder.casefold() not in {"/embedding", "-embedding"}:
            raise ValueError("unsupported registered arguments")
    else:
        if not value.casefold().endswith("onenote.exe"):
            raise ValueError("ambiguous unquoted executable")
        executable = value

    candidate = Path(executable)
    if not candidate.is_absolute() or str(candidate).startswith("\\\\"):
        raise ValueError("executable is not an absolute local path")
    candidate_attributes = getattr(os.lstat(candidate), "st_file_attributes", 0)
    if candidate_attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
        raise ValueError("registered target is a reparse point")
    resolved = candidate.resolve(strict=True)
    if resolved.name.casefold() != ONENOTE_PROCESS_NAME.casefold() or not resolved.is_file():
        raise ValueError("registered target is not ONENOTE.EXE")
    resolved_attributes = getattr(os.lstat(resolved), "st_file_attributes", 0)
    if resolved_attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
        raise ValueError("registered target is a reparse point")
    return resolved


def resolve_onenote_executable() -> Path:
    """Resolve a trusted registered ONENOTE.EXE without COM activation."""

    if sys.platform != "win32":
        raise OneNoteDesktopExecutableError(
            "OneNote Desktop executable resolution is only supported on Windows.",
            operation="launch_onenote_gui",
        )
    try:
        import winreg

        resolved = _resolve_registered_onenote_localserver(winreg)
        if resolved is not None:
            return resolved
    except OneNoteDesktopExecutableError:
        raise
    except Exception as exc:
        raise OneNoteDesktopExecutableError(
            "The registered OneNote Desktop executable could not be inspected safely.",
            operation="launch_onenote_gui",
        ) from exc
    raise OneNoteDesktopExecutableError(
        "A trusted registered OneNote Desktop executable was not found.",
        operation="launch_onenote_gui",
    )


def _resolve_registered_onenote_localserver(winreg: Any) -> Path | None:
    """Resolve the exact ProgID -> CLSID -> LocalServer32 registration chain."""

    access_modes = [winreg.KEY_READ]
    for view in (
        getattr(winreg, "KEY_WOW64_64KEY", 0),
        getattr(winreg, "KEY_WOW64_32KEY", 0),
    ):
        access = winreg.KEY_READ | view
        if view and access not in access_modes:
            access_modes.append(access)
    for access in access_modes:
        for application in ("OneNote.Application", "OneNote.Application.15"):
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CLASSES_ROOT,
                    f"{application}\\CLSID",
                    0,
                    access,
                ) as key:
                    clsid, clsid_type = winreg.QueryValueEx(key, None)
                if clsid_type != winreg.REG_SZ or re.fullmatch(
                    r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
                    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}",
                    str(clsid),
                ) is None:
                    continue
                with winreg.OpenKey(
                    winreg.HKEY_CLASSES_ROOT,
                    f"CLSID\\{clsid}\\LocalServer32",
                    0,
                    access,
                ) as key:
                    value, value_type = winreg.QueryValueEx(key, None)
                if value_type not in {winreg.REG_SZ, winreg.REG_EXPAND_SZ}:
                    continue
                if value_type == winreg.REG_EXPAND_SZ:
                    value = os.path.expandvars(str(value))
                return _registered_executable(str(value))
            except (FileNotFoundError, OSError, ValueError):
                continue
    return None


def _start_onenote_process(executable: Path) -> None:
    try:
        subprocess.Popen(
            [str(executable)],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception as exc:
        raise OneNoteDesktopLaunchError(
            "OneNote Desktop could not be started by the explicit launch request.",
            operation="launch_onenote_gui",
        ) from exc


def launch_onenote_gui(
    *,
    probe: Any = probe_onenote_desktop,
    resolver: Any = resolve_onenote_executable,
    process_launcher: Any = _start_onenote_process,
    clock: Any = time.monotonic,
    sleeper: Any = time.sleep,
    timeout_seconds: float = ONENOTE_GUI_LAUNCH_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Start OneNote once only when no process is running, then observe readiness."""

    initial = probe()
    if initial.ready:
        return {
            "status": "already_running",
            "launch_attempted": False,
            "launch_attempts": 0,
            "ready": True,
            "onenote_desktop": initial.as_dict(),
        }
    if initial.process_running:
        raise OneNoteDesktopWindowUnavailableError(
            "OneNote Desktop is running without a supported visible GUI window.",
            operation="launch_onenote_gui",
            details={"onenote_desktop": initial.as_dict(), "launch_attempts": 0},
        )

    executable = resolver()
    process_launcher(executable)
    deadline = clock() + max(0.1, float(timeout_seconds))
    last = initial
    while clock() < deadline:
        last = probe()
        if last.ready:
            return {
                "status": "started",
                "launch_attempted": True,
                "launch_attempts": 1,
                "ready": True,
                "onenote_desktop": last.as_dict(),
            }
        sleeper(min(ONENOTE_GUI_READINESS_POLL_SECONDS, max(0.0, deadline - clock())))
    raise OneNoteDesktopLaunchTimeoutError(
        "OneNote Desktop did not reach visible-GUI readiness within the launch budget.",
        operation="launch_onenote_gui",
        details={"onenote_desktop": last.as_dict(), "launch_attempts": 1},
    )


__all__ = [
    "ONENOTE_PROCESS_NAME",
    "ONENOTE_GUI_LAUNCH_TIMEOUT_SECONDS",
    "OneNoteDesktopState",
    "launch_onenote_gui",
    "probe_onenote_desktop",
    "require_onenote_desktop",
    "resolve_onenote_executable",
]
