"""OneNote Page content parsing, formatting, and XML construction."""

from .formatting import markdown_to_html, normalize_content
from .parser import (
    DELETABLE_PAGE_OBJECT_TYPES,
    collect_page_objects,
    text_from_page_xml,
    title_from_page_xml,
)
from .builder import build_image_page_update_xml, build_page_update_xml

__all__ = [
    "DELETABLE_PAGE_OBJECT_TYPES",
    "build_image_page_update_xml",
    "build_page_update_xml",
    "collect_page_objects",
    "markdown_to_html",
    "normalize_content",
    "text_from_page_xml",
    "title_from_page_xml",
]
