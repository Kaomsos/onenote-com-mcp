"""Typed mutation MCP tools.

The functions in this module are intentionally thin: policy checks, bridge calls,
and read-back verification belong to :mod:`local_onenote_mcp.services`.
"""

from __future__ import annotations

from typing import Any

from .context import get_services
from .responses import invoke


async def create_notebook(name_or_path: str, base_folder: str = "") -> dict[str, Any]:
    """Create a notebook and verify it through the typed hierarchy model."""

    return invoke(lambda: get_services().mutations.create_notebook(name_or_path, base_folder))


async def create_section(parent_id: str, section_name: str) -> dict[str, Any]:
    """Create a section below a notebook or section group."""

    return invoke(lambda: get_services().mutations.create_section(parent_id, section_name))


async def create_section_group(parent_id: str, group_name: str) -> dict[str, Any]:
    """Create a section group below a notebook or section group."""

    return invoke(lambda: get_services().mutations.create_section_group(parent_id, group_name))


async def create_page(
    section_id: str,
    title: str,
    content: str = "",
    content_format: str = "plain",
    new_page_style: str = "blank_with_title",
) -> dict[str, Any]:
    """Create a page and verify its typed hierarchy identity."""

    return invoke(
        lambda: get_services().mutations.create_page(
            section_id, title, content, content_format, new_page_style
        )
    )


async def update_page_title(
    page_id: str,
    title: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Update a page title after optimistic confirmation."""

    return invoke(
        lambda: get_services().mutations.update_page_title(
            page_id, title, expected_title, expected_section_id, expected_modified
        )
    )


async def rename_section_group(
    section_group_id: str,
    new_name: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Rename a section group after optimistic confirmation."""

    return invoke(
        lambda: get_services().mutations.rename_resource(
            section_group_id,
            "section_group",
            new_name,
            expected_name,
            expected_parent_id,
            expected_modified,
        )
    )


async def rename_section(
    section_id: str,
    new_name: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Rename a section after optimistic confirmation."""

    return invoke(
        lambda: get_services().mutations.rename_resource(
            section_id, "section", new_name, expected_name, expected_parent_id, expected_modified
        )
    )


async def reorder_page(
    page_id: str,
    expected_title: str,
    expected_section_id: str,
    after_page_id: str = "",
    page_level: int = 0,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Reorder a page within its section and verify the resulting order."""

    return invoke(
        lambda: get_services().mutations.reorder_page(
            page_id,
            expected_title,
            expected_section_id,
            after_page_id,
            page_level,
            expected_modified,
        )
    )


async def reorder_section(
    section_id: str,
    expected_name: str,
    expected_parent_id: str,
    after_section_id: str = "",
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Experimentally reorder a section among same-parent Section siblings."""

    return invoke(
        lambda: get_services().mutations.reorder_section(
            section_id,
            expected_name,
            expected_parent_id,
            after_section_id,
            expected_modified,
        )
    )


async def reorder_section_group(
    section_group_id: str,
    expected_name: str,
    expected_parent_id: str,
    after_section_group_id: str = "",
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Experimentally reorder a SectionGroup among same-parent Group siblings."""

    return invoke(
        lambda: get_services().mutations.reorder_section_group(
            section_group_id,
            expected_name,
            expected_parent_id,
            after_section_group_id,
            expected_modified,
        )
    )


async def reparent_section(
    section_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Experimentally reparent a Section within one Notebook."""

    return invoke(
        lambda: get_services().mutations.reparent_section(
            section_id,
            destination_parent_id,
            expected_name,
            expected_parent_id,
            expected_modified,
        )
    )


async def reparent_page(
    page_id: str,
    destination_section_id: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Experimentally reparent a Page within one Notebook."""

    return invoke(
        lambda: get_services().mutations.reparent_page(
            page_id,
            destination_section_id,
            expected_title,
            expected_section_id,
            expected_modified,
        )
    )


async def reparent_section_group(
    section_group_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Experimentally reparent a SectionGroup within one Notebook."""

    return invoke(
        lambda: get_services().mutations.reparent_section_group(
            section_group_id,
            destination_parent_id,
            expected_name,
            expected_parent_id,
            expected_modified,
        )
    )


async def append_to_page(
    page_id: str,
    content: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    content_format: str = "plain",
    x: float | None = None,
    y: float | None = None,
) -> dict[str, Any]:
    """Append content to a confirmed page."""

    return invoke(
        lambda: get_services().mutations.append_to_page(
            page_id,
            content,
            expected_title,
            expected_section_id,
            expected_modified,
            content_format,
            x,
            y,
        )
    )


async def add_image_to_page(
    page_id: str,
    image_path: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    image_format: str = "",
    x: float = 36.0,
    y: float = 120.0,
    width: float | None = None,
    height: float | None = None,
) -> dict[str, Any]:
    """Add a local image to a confirmed page."""

    return invoke(
        lambda: get_services().mutations.add_image_to_page(
            page_id,
            image_path,
            expected_title,
            expected_section_id,
            expected_modified,
            image_format,
            x,
            y,
            width,
            height,
        )
    )


async def replace_page_body(
    page_id: str,
    content: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    title: str | None = None,
    content_format: str = "plain",
) -> dict[str, Any]:
    """Replace supported page body objects and report partial failures."""

    return invoke(
        lambda: get_services().mutations.replace_page_body(
            page_id,
            content,
            expected_title,
            expected_section_id,
            expected_modified,
            title,
            content_format,
        )
    )


async def delete_page_content(
    page_id: str,
    object_id: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Delete one verified deletable page content object."""

    return invoke(
        lambda: get_services().mutations.delete_page_content(
            page_id, object_id, expected_title, expected_section_id, expected_modified
        )
    )


async def delete_section_group(
    section_group_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
    permanently: bool = False,
) -> dict[str, Any]:
    """Delete a confirmed section group under the active deletion policy."""

    return invoke(
        lambda: get_services().mutations.delete_resource(
            section_group_id,
            "section_group",
            expected_name,
            expected_parent_id,
            expected_modified,
            permanently,
        )
    )


async def delete_section(
    section_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
    permanently: bool = False,
) -> dict[str, Any]:
    """Delete a confirmed section under the active deletion policy."""

    return invoke(
        lambda: get_services().mutations.delete_resource(
            section_id,
            "section",
            expected_name,
            expected_parent_id,
            expected_modified,
            permanently,
        )
    )


async def delete_page(
    page_id: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    permanently: bool = False,
) -> dict[str, Any]:
    """Delete a confirmed page under the active deletion policy."""

    return invoke(
        lambda: get_services().mutations.delete_page(
            page_id, expected_title, expected_section_id, expected_modified, permanently
        )
    )


TOOLS = [
    create_notebook,
    create_section,
    create_section_group,
    create_page,
    update_page_title,
    rename_section_group,
    rename_section,
    reorder_page,
    reorder_section,
    reorder_section_group,
    reparent_page,
    reparent_section,
    reparent_section_group,
    append_to_page,
    add_image_to_page,
    replace_page_body,
    delete_page_content,
    delete_section_group,
    delete_section,
    delete_page,
]
