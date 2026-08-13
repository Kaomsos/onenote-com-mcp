"""Typed, content-free errors for the OneNote COM boundary."""

from __future__ import annotations

from typing import Any


MODAL_UI_BLOCKED_HRESULT = 0x80042030
NOT_YET_SYNCHRONIZED_HRESULT = 0x8004201D
OPERATION_TIMEOUT_HRESULT = 0x80042023
FILE_UNAVAILABLE_HRESULTS = frozenset({0x80042006})
OBJECT_UNAVAILABLE_HRESULTS = frozenset(
    {
        0x80042004,  # hrSectionDoesNotExist
        0x80042005,  # hrPageDoesNotExist
        0x8004200E,  # hrPageObjectDoesNotExist
        0x8004200F,  # hrBinaryObjectDoesNotExist
        0x80042011,  # hrGroupDoesNotExist
        0x80042012,  # hrPageDoesNotExistInGroup
        0x80042014,  # hrObjectDoesNotExist
        0x80042015,  # hrNotebookDoesNotExist
        0x80042018,  # hrFolderDoesNotExist
    }
)


def unsigned_hresult(value: int | None) -> int | None:
    return None if value is None else int(value) & 0xFFFFFFFF


def signed_hresult(value: int | None) -> int | None:
    if value is None:
        return None
    normalized = unsigned_hresult(value)
    assert normalized is not None
    return normalized - 0x100000000 if normalized >= 0x80000000 else normalized


class OneNoteError(RuntimeError):
    """Base error whose public fields are safe to project into tool responses."""

    code = "onenote_backend_error"
    retryability = "unknown"

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        hresult: int | None = None,
        category: str | None = None,
        wrapper_hresult: int | None = None,
        exception_depth: int | None = None,
        leaf_exception_type: str | None = None,
        retryability: str | None = None,
        partial: bool = False,
        reconciliation: str = "indeterminate",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.hresult_unsigned = unsigned_hresult(hresult)
        self.hresult_signed = signed_hresult(hresult)
        self.wrapper_hresult_unsigned = unsigned_hresult(wrapper_hresult)
        self.wrapper_hresult_signed = signed_hresult(wrapper_hresult)
        self.exception_depth = (
            int(exception_depth) if exception_depth is not None else None
        )
        self.leaf_exception_type = leaf_exception_type
        self.category = category
        self.retryability = retryability or type(self).retryability
        self.partial = bool(partial)
        self.reconciliation = reconciliation
        self.details = dict(details or {})

    @property
    def hresult(self) -> str | None:
        value = self.hresult_unsigned
        return None if value is None else f"0x{value:08X}"

    @property
    def wrapper_hresult(self) -> str | None:
        value = self.wrapper_hresult_unsigned
        return None if value is None else f"0x{value:08X}"

    def public_details(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error_type": type(self).__name__,
            "retryability": self.retryability,
            "partial": self.partial,
            "reconciliation": self.reconciliation,
        }
        if self.operation:
            result["operation"] = self.operation
        if self.hresult is not None:
            result["hresult"] = self.hresult
            result["hresult_signed"] = self.hresult_signed
        if self.category:
            result["backend_category"] = self.category
        result.update(self.details)
        return result


class OneNoteBridgeError(OneNoteError):
    """Unknown or unclassified local bridge/COM failure."""


class OneNoteModalUIBlockedError(OneNoteBridgeError):
    code = "onenote_modal_ui_blocked"
    retryability = "after_user_action"


class OneNoteNotYetSynchronizedError(OneNoteBridgeError):
    code = "onenote_not_yet_synchronized"
    retryability = "read_after_delay"


class OneNoteOperationTimeoutError(OneNoteBridgeError):
    code = "onenote_operation_timeout"
    retryability = "reconcile_before_retry"


class OneNoteObjectUnavailableError(OneNoteBridgeError):
    code = "onenote_object_unavailable"
    retryability = "read_after_delay"


class OneNoteFileUnavailableError(OneNoteBridgeError):
    code = "onenote_file_unavailable"
    retryability = "read_after_delay"


class OneNoteConvergenceTimeoutError(OneNoteError):
    code = "onenote_convergence_timeout"
    retryability = "reconcile_before_retry"


class OneNoteCoordinationTimeoutError(OneNoteError):
    code = "onenote_coordination_timeout"
    retryability = "safe_to_retry"


def bridge_error(
    message: str,
    *,
    operation: str,
    hresult: int | None = None,
    category: str | None = None,
    wrapper_hresult: int | None = None,
    exception_depth: int | None = None,
    leaf_exception_type: str | None = None,
    timed_out: bool = False,
) -> OneNoteBridgeError:
    """Classify a bridge failure only from structured bridge evidence."""

    normalized = unsigned_hresult(hresult)
    error_type: type[OneNoteBridgeError]
    if normalized == MODAL_UI_BLOCKED_HRESULT:
        error_type = OneNoteModalUIBlockedError
        message = "OneNote is blocked by a modal dialog. Close the dialog in OneNote and retry."
    elif normalized == NOT_YET_SYNCHRONIZED_HRESULT:
        error_type = OneNoteNotYetSynchronizedError
        message = "OneNote content is not yet synchronized. Retry the read after a delay."
    elif normalized == OPERATION_TIMEOUT_HRESULT or timed_out:
        error_type = OneNoteOperationTimeoutError
        message = "OneNote COM operation timed out before a stable result was returned."
    elif normalized in OBJECT_UNAVAILABLE_HRESULTS:
        error_type = OneNoteObjectUnavailableError
        message = "The requested OneNote object is not currently available."
    elif normalized in FILE_UNAVAILABLE_HRESULTS:
        error_type = OneNoteFileUnavailableError
        message = "The requested OneNote file is not currently available."
    else:
        error_type = OneNoteBridgeError
        message = "OneNote COM operation failed."
    return error_type(
        message,
        operation=operation,
        hresult=hresult,
        category=category,
        wrapper_hresult=wrapper_hresult,
        exception_depth=exception_depth,
        leaf_exception_type=leaf_exception_type,
    )


def transient_read_error(exc: Exception) -> bool:
    """Return True only for typed read failures that may converge without user action."""

    return isinstance(
        exc,
        (
            OneNoteNotYetSynchronizedError,
            OneNoteOperationTimeoutError,
            OneNoteObjectUnavailableError,
            OneNoteFileUnavailableError,
        ),
    )


def idempotent_retry_allowed(exc: Exception) -> bool:
    """Allow replay only for typed timeout/synchronization evidence, never unknown/modal errors."""

    return isinstance(
        exc,
        (OneNoteNotYetSynchronizedError, OneNoteOperationTimeoutError),
    )
