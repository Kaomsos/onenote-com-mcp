"""Typed mutation MCP tools.

The functions in this module are intentionally thin: policy checks, bridge calls,
and read-back verification belong to :mod:`local_onenote_mcp.services`.
"""

from __future__ import annotations

from typing import Any

from .responses import invoke as _invoke


def invoke(operation: str, **arguments: Any) -> dict[str, Any]:
    """Dispatch one typed mutation through the Operation Runtime."""

    return _invoke(operation, **arguments)


async def create_notebook(name_or_path: str, base_folder: str = "") -> dict[str, Any]:
    """Create a notebook and verify it through the typed hierarchy model."""

    return invoke("create_notebook", name_or_path=name_or_path, base_folder=base_folder)


async def create_section(parent_id: str, section_name: str) -> dict[str, Any]:
    """Create a section below a notebook or section group."""

    return invoke("create_section", parent_id=parent_id, section_name=section_name)


async def create_section_group(parent_id: str, group_name: str) -> dict[str, Any]:
    """Create a section group below a notebook or section group."""

    return invoke("create_section_group", parent_id=parent_id, group_name=group_name)


async def create_page(
    section_id: str,
    title: str,
    content: str = "",
    content_format: str = "plain",
    new_page_style: str = "blank_with_title",
) -> dict[str, Any]:
    """Create a page and verify its typed hierarchy identity."""

    return invoke(
        "create_page",
        section_id=section_id,
        title=title,
        content=content,
        content_format=content_format,
        new_page_style=new_page_style,
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
        "update_page_title",
        page_id=page_id,
        title=title,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
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
        "rename_section_group",
        section_group_id=section_group_id,
        new_name=new_name,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
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
        "rename_section",
        section_id=section_id,
        new_name=new_name,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
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
        "reorder_page",
        page_id=page_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        after_page_id=after_page_id,
        page_level=page_level,
        expected_modified=expected_modified,
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
        "reorder_section",
        section_id=section_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        after_section_id=after_section_id,
        expected_modified=expected_modified,
    )


async def reparent_section(
    section_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Reparent a Section and report its observed position, not a placement guarantee."""

    return invoke(
        "reparent_section",
        section_id=section_id,
        destination_parent_id=destination_parent_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
    )


async def reparent_page(
    page_id: str,
    destination_section_id: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    include_descendants: bool = False,
) -> dict[str, Any]:
    """Reparent one Page or its indentation subtree within one Notebook.

    The selected Page becomes a root Page in the destination Section.  By
    default only that Page moves and excluded descendants remain in the source
    Section, promoted by one level.  Set include_descendants=true to move the
    complete indentation subtree.  The response reports only the destination
    root Page's observed final position; it does not request or guarantee placement.
    """

    return invoke(
        "reparent_page",
        page_id=page_id,
        destination_section_id=destination_section_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        include_descendants=include_descendants,
    )


async def reparent_section_group(
    section_group_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Reparent a SectionGroup and report its backend name-sorted observed position."""

    return invoke(
        "reparent_section_group",
        section_group_id=section_group_id,
        destination_parent_id=destination_parent_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
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
        "append_to_page",
        page_id=page_id,
        content=content,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        content_format=content_format,
        x=x,
        y=y,
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
        "add_image_to_page",
        page_id=page_id,
        image_path=image_path,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        image_format=image_format,
        x=x,
        y=y,
        width=width,
        height=height,
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
        "replace_page_body",
        page_id=page_id,
        content=content,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        title=title,
        content_format=content_format,
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
        "delete_page_content",
        page_id=page_id,
        object_id=object_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
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
        "delete_section_group",
        section_group_id=section_group_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
        permanently=permanently,
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
        "delete_section",
        section_id=section_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
        permanently=permanently,
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
        "delete_page",
        page_id=page_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        permanently=permanently,
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
