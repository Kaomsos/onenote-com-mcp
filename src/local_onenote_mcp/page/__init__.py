"""OneNote Page parsing, formatting, image support, and XML construction."""

from .formatting import markdown_to_html, normalize_content
from .images import (
    ImageDimensionError,
    image_dimensions,
    image_file_format,
    proportional_dimensions,
)
from .parser import (
    DELETABLE_PAGE_OBJECT_TYPES,
    RICH_HTML_FORMAT,
    collect_page_objects,
    rich_html_from_page_xml,
    text_from_page_xml,
    title_from_page_xml,
    truncate_rich_html,
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
    page_content_capability_projection,
    page_equivalence,
    copy_verification_tier,
    semantic_display_equation_comparison,
    semantic_mathml_comparison,
    semantic_mathml_projection,
    transform_page_for_copy,
)

__all__ = [
    "DELETABLE_PAGE_OBJECT_TYPES",
    "RICH_HTML_FORMAT",
    "ImageDimensionError",
    "build_image_page_update_xml",
    "build_page_update_xml",
    "tag_definitions_from_page_xml",
    "canonical_page_digest",
    "page_content_capability_projection",
    "collect_page_objects",
    "COPYABLE_CONTENT_ROOTS",
    "image_dimensions",
    "image_file_format",
    "markdown_to_html",
    "normalize_content",
    "proportional_dimensions",
    "rich_html_from_page_xml",
    "page_equivalence",
    "copy_verification_tier",
    "semantic_display_equation_comparison",
    "semantic_mathml_comparison",
    "semantic_mathml_projection",
    "text_from_page_xml",
    "title_from_page_xml",
    "truncate_rich_html",
    "transform_page_for_copy",
    "VALIDATED_COPY_CONTENT_TYPES",
]
