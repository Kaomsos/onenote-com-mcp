"""Typed mutation MCP tools.

The functions in this module are intentionally thin: policy checks, bridge calls,
and read-back verification belong to :mod:`local_onenote_mcp.services`.
"""

from __future__ import annotations

from typing import Any, Literal

from .responses import invoke as _invoke


def invoke(operation: str, **arguments: Any) -> dict[str, Any]:
    """Dispatch one typed mutation through the Operation Runtime."""

    return _invoke(operation, **arguments)


async def create_notebook(name: str, base_folder: str | None = None) -> dict[str, Any]:
    """With Writes, create a Notebook and verify it through the typed hierarchy model."""

    return invoke("create_notebook", name=name, base_folder=base_folder)


async def create_section(parent_id: str, name: str) -> dict[str, Any]:
    """With Writes, create a Section below an exact Notebook or SectionGroup ID."""

    return invoke("create_section", parent_id=parent_id, name=name)


async def create_section_group(parent_id: str, name: str) -> dict[str, Any]:
    """With Writes, create a SectionGroup below an exact Notebook or SectionGroup ID."""

    return invoke("create_section_group", parent_id=parent_id, name=name)


async def create_page(
    section_id: str,
    title: str,
    content: str = "",
    content_format: str = "plain",
) -> dict[str, Any]:
    """With Writes, create a Page below an exact Section ID and verify its allocated identity."""

    return invoke(
        "create_page",
        section_id=section_id,
        title=title,
        content=content,
        content_format=content_format,
    )


async def rename_page(
    page_id: str,
    title: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """With Writes, rename an exact Page after optimistic confirmation."""

    return invoke(
        "rename_page",
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
    """With Writes, rename an exact SectionGroup after optimistic confirmation."""

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
    """With Writes, rename an exact Section after optimistic confirmation."""

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
    after_page_id: str | None = None,
    page_level: int = 0,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """With Writes, reorder an exact Page within its Section and verify order and indentation."""

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
    after_section_id: str | None = None,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """With Writes, reorder an exact Section among same-parent Section siblings."""

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
    """With Writes and Organize, reparent an exact Section within one Notebook and report observed position."""

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
    page_scope: Literal["page_only", "indentation_subtree"] = "page_only",
) -> dict[str, Any]:
    """With Writes and Organize, reparent one exact Page scope within one Notebook.

    The selected Page becomes a root Page in the destination Section.  By
    default only that Page moves and excluded descendants remain in the source
    Section, promoted by one level.  Set page_scope="indentation_subtree" to move
    the complete indentation subtree.  The response reports only the destination
    root Page's observed final position; it does not request or guarantee placement.
    """

    return invoke(
        "reparent_page",
        page_id=page_id,
        destination_section_id=destination_section_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        page_scope=page_scope,
    )


async def reparent_section_group(
    section_group_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """With Writes and Organize, reparent a SectionGroup and report its observed destination position."""

    return invoke(
        "reparent_section_group",
        section_group_id=section_group_id,
        destination_parent_id=destination_parent_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
    )


async def append_page_content(
    page_id: str,
    content: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    content_format: str = "plain",
    x: float | None = None,
    y: float | None = None,
) -> dict[str, Any]:
    """With Writes, append content to an exact confirmed Page."""

    return invoke(
        "append_page_content",
        page_id=page_id,
        content=content,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        content_format=content_format,
        x=x,
        y=y,
    )


async def add_page_image_from_file(
    page_id: str,
    image_path: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    x: float = 36.0,
    y: float = 120.0,
    width: float | None = None,
    height: float | None = None,
) -> dict[str, Any]:
    """With Writes and Local File IO, add a validated local image to an exact confirmed Page."""

    return invoke(
        "add_page_image_from_file",
        page_id=page_id,
        image_path=image_path,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
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
    content_format: str = "plain",
) -> dict[str, Any]:
    """With Writes and Deletes, replace supported body objects on an exact Page and report partial failures."""

    return invoke(
        "replace_page_body",
        page_id=page_id,
        content=content,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        content_format=content_format,
    )


async def delete_page_content_object(
    page_id: str,
    page_content_object_id: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """With Deletes, remove one exact verified PageContentObject; never delete the Page."""

    return invoke(
        "delete_page_content_object",
        page_id=page_id,
        page_content_object_id=page_content_object_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
    )


async def delete_section_group(
    section_group_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """With Deletes, non-permanently delete an exact confirmed SectionGroup to recoverable state."""

    return invoke(
        "delete_section_group",
        section_group_id=section_group_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
    )


async def delete_section(
    section_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """With Deletes, non-permanently delete an exact confirmed Section to recoverable state."""

    return invoke(
        "delete_section",
        section_id=section_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
    )


async def delete_page(
    page_id: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """With Deletes, non-permanently delete an exact confirmed Page to recoverable state."""

    return invoke(
        "delete_page",
        page_id=page_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
    )


TOOLS = [
    create_notebook,
    create_section_group,
    create_section,
    create_page,
    rename_page,
    rename_section_group,
    rename_section,
    reorder_page,
    reorder_section,
    reparent_page,
    reparent_section,
    reparent_section_group,
    append_page_content,
    add_page_image_from_file,
    replace_page_body,
    delete_page_content_object,
    delete_page,
    delete_section,
    delete_section_group,
]
