"""OneNote Page parsing, formatting, image support, and XML construction."""

from .formatting import markdown_to_html, normalize_content
from .images import ImageDimensionError, image_dimensions, proportional_dimensions
from .parser import (
    DELETABLE_PAGE_OBJECT_TYPES,
    collect_page_objects,
    text_from_page_xml,
    title_from_page_xml,
)
from .builder import build_image_page_update_xml, build_page_update_xml

__all__ = [
    "DELETABLE_PAGE_OBJECT_TYPES",
    "ImageDimensionError",
    "build_image_page_update_xml",
    "build_page_update_xml",
    "collect_page_objects",
    "image_dimensions",
    "markdown_to_html",
    "normalize_content",
    "proportional_dimensions",
    "text_from_page_xml",
    "title_from_page_xml",
]
