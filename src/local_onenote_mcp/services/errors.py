"""Application service errors with structured transport details."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


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
