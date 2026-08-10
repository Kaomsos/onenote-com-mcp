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

    return invoke(lambda: get_services().mutations.open_hierarchy(path, relative_to_identifier, create_type))


async def delete_hierarchy(object_identifier: str, permanently: bool = False) -> dict[str, Any]:
    """Delete a hierarchy object using the opt-in generic advanced path."""

    return invoke(lambda: get_services().mutations.delete_hierarchy(object_identifier, permanently))


async def update_page_xml(xml: str) -> dict[str, Any]:
    """Send raw page XML when raw XML and write policies are enabled."""

    return invoke(lambda: get_services().mutations.update_page_xml(xml))


async def merge_sections(
    source_section_identifier: str, destination_section_identifier: str
) -> dict[str, Any]:
    """Merge two resolved sections when advanced writes are enabled."""

    return invoke(
        lambda: get_services().operations.merge_sections(
            source_section_identifier, destination_section_identifier
        )
    )


async def set_filing_location(
    filing_location: str,
    filing_location_type: str,
    section_or_page_identifier: str,
) -> dict[str, Any]:
    """Set a OneNote filing location using the advanced COM operation."""

    return invoke(
        lambda: get_services().operations.set_filing_location(
            filing_location, filing_location_type, section_or_page_identifier
        )
    )


TOOLS = [
    find_meta,
    open_hierarchy,
    delete_hierarchy,
    update_page_xml,
    merge_sections,
    set_filing_location,
]
