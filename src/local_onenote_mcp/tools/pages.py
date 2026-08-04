"""Page content and Search MCP tools."""

from __future__ import annotations

from typing import Any

from ..settings import MAX_TEXT_CHARS
from .context import get_services
from .responses import invoke


async def get_page(page_id: str) -> dict[str, Any]:
    """Get Page metadata only."""

    return invoke(lambda: get_services().pages.get(page_id))


async def get_page_xml(page_id: str, page_info: str = "basic") -> dict[str, Any]:
    """Return raw OneNote XML for one Page."""

    return invoke(lambda: get_services().pages.get_xml(page_id, page_info))


async def get_page_text(page_id: str, max_chars: int = MAX_TEXT_CHARS) -> dict[str, Any]:
    """Return visible text extracted from one Page."""

    return invoke(lambda: get_services().pages.get_text(page_id, max_chars))


async def get_page_objects(page_id: str) -> dict[str, Any]:
    """List typed PageContentObjects."""

    return invoke(lambda: get_services().pages.get_objects(page_id))


async def get_binary_content(page_id: str, callback_id: str) -> dict[str, Any]:
    """Read validated binary Page content."""

    return invoke(lambda: get_services().pages.get_binary(page_id, callback_id))


async def search_pages(
    query: str,
    scope_type: str,
    scope_id: str,
    backend: str = "local_scan",
    max_results: int = 20,
    include_snippets: bool = True,
    include_recycle_bin: bool = False,
) -> dict[str, Any]:
    """Search Page text in an explicit typed scope."""

    return invoke(
        lambda: get_services().search.search(
            query,
            scope_type,
            scope_id,
            backend,
            max_results,
            include_snippets,
            include_recycle_bin,
        )
    )


TOOLS = [get_page, get_page_xml, get_page_text, get_page_objects, get_binary_content, search_pages]
