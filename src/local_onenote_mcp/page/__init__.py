"""OneNote Page parsing, formatting, image support, and XML construction."""

from .formatting import markdown_to_html, normalize_content
from .images import ImageDimensionError, image_dimensions, proportional_dimensions
from .parser import (
    DELETABLE_PAGE_OBJECT_TYPES,
    collect_page_objects,
    text_from_page_xml,
    title_from_page_xml,
)
from .builder import (
    build_image_page_update_xml,
    build_page_update_xml,
    tag_definitions_from_page_xml,
)
from .copying import (
    COPYABLE_CONTENT_ROOTS,
    VALIDATED_COPY_CONTENT_TYPES,
    canonical_page_digest,
    page_equivalence,
    copy_verification_tier,
    transform_page_for_copy,
)

__all__ = [
    "DELETABLE_PAGE_OBJECT_TYPES",
    "ImageDimensionError",
    "build_image_page_update_xml",
    "build_page_update_xml",
    "tag_definitions_from_page_xml",
    "canonical_page_digest",
    "collect_page_objects",
    "COPYABLE_CONTENT_ROOTS",
    "image_dimensions",
    "markdown_to_html",
    "normalize_content",
    "proportional_dimensions",
    "page_equivalence",
    "copy_verification_tier",
    "text_from_page_xml",
    "title_from_page_xml",
    "transform_page_for_copy",
    "VALIDATED_COPY_CONTENT_TYPES",
]
