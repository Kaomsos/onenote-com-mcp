"""Canonical OneNote domain model package.

All object-model consumers import from this facade; no service or tool defines
an alternative hierarchy resource representation.
"""

from .notebook import Notebook
from .page import Page
from .page_content import PageContentObject, content_objects
from .resource import Resource
from .section import Section
from .section_group import SectionGroup

__all__ = [
    "Notebook",
    "Page",
    "PageContentObject",
    "Resource",
    "Section",
    "SectionGroup",
    "content_objects",
]
