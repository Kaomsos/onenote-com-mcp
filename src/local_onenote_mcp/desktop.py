"""Read-only Windows preflight for an already running OneNote Desktop GUI."""

from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Any

from .onenote_errors import OneNoteDesktopNotRunningError, OneNoteDesktopProbeError


ONENOTE_PROCESS_NAME = "ONENOTE.EXE"


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


def require_onenote_desktop() -> OneNoteDesktopState:
    """Fail closed unless an existing OneNote Desktop GUI is ready."""

    state = probe_onenote_desktop()
    if not state.ready:
        raise OneNoteDesktopNotRunningError(
            "OneNote Desktop is not running with a visible GUI. Start OneNote and retry.",
            operation="health_preflight",
            details={
                "onenote_desktop": state.as_dict(),
                "required_action": "start_onenote_desktop_and_retry",
            },
        )
    return state


__all__ = [
    "ONENOTE_PROCESS_NAME",
    "OneNoteDesktopState",
    "probe_onenote_desktop",
    "require_onenote_desktop",
]
