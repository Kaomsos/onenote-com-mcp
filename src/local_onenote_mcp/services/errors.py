"""Application service errors with structured transport details."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..onenote_errors import OneNoteError


class PartialFailure(RuntimeError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class PageReadbackMismatch(PartialFailure):
    """Base partial failure for a typed, content-free Page mismatch."""

    readback_error_code = "page_content_readback_mismatch"

    def __init__(self, message: str, **details: Any) -> None:
        details.setdefault("readback_error_code", type(self).readback_error_code)
        super().__init__(message, **details)


class PageTitleReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_title_readback_mismatch"


class PageRichTextReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_rich_text_readback_mismatch"


class PageListReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_list_readback_mismatch"


class PageTagReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_tag_readback_mismatch"


class PageTableReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_table_readback_mismatch"


class PageOutlineReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_outline_readback_mismatch"


class PageImageReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_image_readback_mismatch"


class PageInsertedFileReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_inserted_file_readback_mismatch"


class PageFileAttachmentReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_file_attachment_readback_mismatch"


class PageMediaFileReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_media_file_readback_mismatch"


class PageDisplayEquationReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_display_equation_readback_mismatch"


class PageInkDrawingReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_ink_drawing_readback_mismatch"


class PageUIShapeReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_ui_shape_readback_mismatch"


class PageUnknownContentReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_unknown_content_readback_mismatch"


class PageMixedContentReadbackMismatch(PageReadbackMismatch):
    readback_error_code = "page_mixed_content_readback_mismatch"


_PAGE_READBACK_MISMATCH_TYPES: dict[str, type[PageReadbackMismatch]] = {
    "PageTitle": PageTitleReadbackMismatch,
    "RichText": PageRichTextReadbackMismatch,
    "List": PageListReadbackMismatch,
    "Tag": PageTagReadbackMismatch,
    "Table": PageTableReadbackMismatch,
    "Outline": PageOutlineReadbackMismatch,
    "Image": PageImageReadbackMismatch,
    "InsertedFile": PageInsertedFileReadbackMismatch,
    "FileAttachment": PageFileAttachmentReadbackMismatch,
    "MediaFile": PageMediaFileReadbackMismatch,
    "DisplayEquation": PageDisplayEquationReadbackMismatch,
    "InkDrawing": PageInkDrawingReadbackMismatch,
    "UIShape": PageUIShapeReadbackMismatch,
    "Unknown": PageUnknownContentReadbackMismatch,
}


def page_readback_mismatch_error(
    message: str,
    content_object_types: Iterable[str],
    **details: Any,
) -> PageReadbackMismatch:
    """Build the deterministic typed exception for Page equivalence failures."""

    categories = sorted(
        {
            str(value)
            for value in content_object_types
            if isinstance(value, str) and value
        }
    )
    if len(categories) == 1:
        error_type = _PAGE_READBACK_MISMATCH_TYPES.get(
            categories[0], PageUnknownContentReadbackMismatch
        )
        category = categories[0]
    elif categories:
        error_type = PageMixedContentReadbackMismatch
        category = "Mixed"
    else:
        error_type = PageUnknownContentReadbackMismatch
        categories = ["Unknown"]
        category = "Unknown"
    details.setdefault("failed_content_object_types", categories)
    details.setdefault("readback_content_category", category)
    details.setdefault("content_exposed", False)
    return error_type(message, **details)


class MutationFailure(RuntimeError):
    """Content-free controlled mutation failure with a stable response code."""

    def __init__(self, message: str, *, code: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


class MutationPreflightFailure(ValueError):
    """Validation-compatible failure before a mutation execute was attempted."""

    code = "validation_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


_ALLOWED_ERROR_TYPES = frozenset(
    {
        "MutationPreflightFailure",
        "MutationFailure",
        "PartialFailure",
        "PermissionError",
        "ValueError",
        "TimeoutError",
        "RuntimeError",
        "OneNoteError",
        "OneNoteBridgeError",
        "OneNoteModalUIBlockedError",
        "OneNoteNotYetSynchronizedError",
        "OneNoteOperationTimeoutError",
        "OneNoteObjectUnavailableError",
        "OneNoteFileUnavailableError",
        "OneNoteConvergenceTimeoutError",
        "OneNoteCoordinationTimeoutError",
        "OneNoteDesktopNotRunningError",
        "OneNoteDesktopProbeError",
        "OneNoteDesktopExecutableError",
        "OneNoteDesktopLaunchError",
        "OneNoteDesktopLaunchTimeoutError",
        "OneNoteDesktopWindowUnavailableError",
        "PageReadbackMismatch",
        "PageTitleReadbackMismatch",
        "PageRichTextReadbackMismatch",
        "PageListReadbackMismatch",
        "PageTagReadbackMismatch",
        "PageTableReadbackMismatch",
        "PageOutlineReadbackMismatch",
        "PageImageReadbackMismatch",
        "PageInsertedFileReadbackMismatch",
        "PageFileAttachmentReadbackMismatch",
        "PageMediaFileReadbackMismatch",
        "PageDisplayEquationReadbackMismatch",
        "PageInkDrawingReadbackMismatch",
        "PageUIShapeReadbackMismatch",
        "PageUnknownContentReadbackMismatch",
        "PageMixedContentReadbackMismatch",
    }
)


@dataclass(frozen=True)
class ErrorClassification:
    code: str
    error_type: str
    partial: bool = False
    indeterminate: bool = False
    retry_safe: bool = False


def _allowlisted_error_type(exc: Exception) -> str:
    name = type(exc).__name__
    if name in _ALLOWED_ERROR_TYPES:
        return name
    if isinstance(exc, PartialFailure):
        return "PartialFailure"
    if isinstance(exc, OneNoteError):
        return "OneNoteError"
    return "RuntimeError"


def _retry_safe_from_details(details: Mapping[str, Any]) -> bool:
    retryability = details.get("retryability") or details.get("retry_safety")
    if isinstance(retryability, str):
        return retryability in {"not_needed", "safe_to_retry", "after_user_action"}
    return False


def classify_error(exc: Exception) -> ErrorClassification:
    """Map an exception to a stable, content-free error classification."""

    if isinstance(exc, MutationPreflightFailure):
        details = exc.details if isinstance(exc.details, Mapping) else {}
        return ErrorClassification(
            code=exc.code,
            error_type=_allowlisted_error_type(exc),
            partial=bool(details.get("partial", False)),
            indeterminate=bool(details.get("indeterminate", False)),
            retry_safe=_retry_safe_from_details(details),
        )
    if isinstance(exc, MutationFailure):
        details = exc.details if isinstance(exc.details, Mapping) else {}
        return ErrorClassification(
            code=exc.code,
            error_type=_allowlisted_error_type(exc),
            partial=bool(details.get("partial", False)),
            indeterminate=bool(details.get("indeterminate", False)),
            retry_safe=_retry_safe_from_details(details),
        )
    if isinstance(exc, PartialFailure):
        details = exc.details if isinstance(exc.details, Mapping) else {}
        return ErrorClassification(
            code="partial_failure",
            error_type=_allowlisted_error_type(exc),
            partial=True,
            indeterminate=bool(details.get("indeterminate", False)),
            retry_safe=False,
        )
    if isinstance(exc, OneNoteError):
        details = exc.public_details()
        return ErrorClassification(
            code=exc.code,
            error_type=_allowlisted_error_type(exc),
            partial=bool(details.get("partial", False)),
            indeterminate=bool(details.get("indeterminate", False)),
            retry_safe=_retry_safe_from_details(details),
        )
    if isinstance(exc, PermissionError):
        return ErrorClassification(
            code="policy_disabled",
            error_type="PermissionError",
        )
    if isinstance(exc, ValueError):
        return ErrorClassification(
            code="validation_error",
            error_type="ValueError",
        )
    return ErrorClassification(
        code="backend_error",
        error_type=_allowlisted_error_type(exc),
    )
