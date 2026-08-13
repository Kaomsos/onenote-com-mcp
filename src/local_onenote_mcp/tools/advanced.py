"""Opt-in advanced MCP tools hidden from the default profile."""

from __future__ import annotations

from typing import Any

from .context import get_services
from .responses import invoke


async def find_meta(start_identifier: str, name: str, include_unindexed: bool = True) -> dict[str, Any]:
    """Use OneNote's metadata search from a resolved hierarchy object."""

    return invoke(lambda: get_services().search.find_meta(start_identifier, name, include_unindexed))


async def open_hierarchy(
    path: str, relative_to_identifier: str = "", create_type: str = "none"
) -> dict[str, Any]:
    """Open an existing hierarchy path or create one when explicitly enabled."""

    return invoke(
        lambda: get_services().mutations.open_hierarchy(path, relative_to_identifier, create_type),
        mutation=True,
    )


async def update_page_xml(xml: str) -> dict[str, Any]:
    """Send raw page XML when raw XML and write policies are enabled."""

    return invoke(lambda: get_services().mutations.update_page_xml(xml), mutation=True)


async def merge_sections(
    source_section_id: str, destination_section_id: str
) -> dict[str, Any]:
    """Merge two exact Section IDs when advanced writes are enabled."""

    return invoke(
        lambda: get_services().operations.merge_sections(
            source_section_id, destination_section_id
        ),
        mutation=True,
    )


async def set_filing_location(
    filing_location: str,
    filing_location_type: str,
    section_or_page_id: str,
) -> dict[str, Any]:
    """Set a OneNote filing location using the advanced COM operation."""

    return invoke(
        lambda: get_services().operations.set_filing_location(
            filing_location, filing_location_type, section_or_page_id
        ),
        mutation=True,
    )


TOOLS = [
    find_meta,
    open_hierarchy,
    update_page_xml,
    merge_sections,
    set_filing_location,
]
