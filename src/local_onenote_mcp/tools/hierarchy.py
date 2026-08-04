"""Typed hierarchy read/query MCP tools."""

from __future__ import annotations

from typing import Any

from .context import get_services
from .responses import invoke


async def list_hierarchy(
    start_identifier: str = "",
    scope: str = "pages",
    include_xml: bool = False,
    include_recycle_bin: bool = False,
) -> dict[str, Any]:
    """List live typed OneNote hierarchy objects."""

    return invoke(
        lambda: get_services().hierarchy.list_hierarchy(
            start_identifier, scope, include_xml, include_recycle_bin
        )
    )


async def list_notebooks(include_recycle_bin: bool = False) -> dict[str, Any]:
    """List live notebooks."""

    return invoke(lambda: get_services().hierarchy.list_notebooks(include_recycle_bin))


async def get_notebook(notebook_id: str) -> dict[str, Any]:
    """Get stable metadata for one Notebook by ID."""

    return invoke(lambda: {"item": get_services().hierarchy.resource(notebook_id, "notebook")})


async def list_section_groups(
    parent_id: str = "",
    recursive: bool = True,
    include_recycle_bin: bool = False,
) -> dict[str, Any]:
    """List SectionGroups, optionally below a typed parent ID."""

    return invoke(
        lambda: get_services().hierarchy.list_section_groups(parent_id, recursive, include_recycle_bin)
    )


async def get_section_group(section_group_id: str) -> dict[str, Any]:
    """Get stable metadata for one SectionGroup by ID."""

    return invoke(lambda: {"item": get_services().hierarchy.resource(section_group_id, "section_group")})


async def list_sections(
    parent_id: str = "",
    recursive: bool = True,
    include_recycle_bin: bool = False,
) -> dict[str, Any]:
    """List Sections, optionally below a typed parent ID."""

    return invoke(lambda: get_services().hierarchy.list_sections(parent_id, recursive, include_recycle_bin))


async def get_section(section_id: str) -> dict[str, Any]:
    """Get stable metadata for one Section by ID."""

    return invoke(lambda: {"item": get_services().hierarchy.resource(section_id, "section")})


async def list_pages(section_id: str, include_recycle_bin: bool = False) -> dict[str, Any]:
    """List Page metadata in one Section."""

    return invoke(lambda: get_services().hierarchy.list_pages(section_id, include_recycle_bin))


async def query_hierarchy(
    resource_type: str,
    name_equals: str = "",
    name_contains: str = "",
    parent_id: str = "",
    modified_after: str = "",
    modified_before: str = "",
    include_recycle_bin: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    """Query stable hierarchy metadata without reading Page content."""

    return invoke(
        lambda: get_services().hierarchy.query(
            resource_type,
            name_equals,
            name_contains,
            parent_id,
            modified_after,
            modified_before,
            include_recycle_bin,
            limit,
        )
    )


async def get_path(object_id: str) -> dict[str, Any]:
    """Get a display path and stable ancestor IDs."""

    return invoke(lambda: get_services().hierarchy.path(object_id))


async def get_tree(root_id: str, max_depth: int = 8, include_recycle_bin: bool = False) -> dict[str, Any]:
    """Get a typed hierarchy and Page indentation tree."""

    return invoke(lambda: get_services().hierarchy.tree(root_id, max_depth, include_recycle_bin))


TOOLS = [
    list_hierarchy,
    list_notebooks,
    get_notebook,
    list_section_groups,
    get_section_group,
    list_sections,
    get_section,
    list_pages,
    query_hierarchy,
    get_path,
    get_tree,
]
