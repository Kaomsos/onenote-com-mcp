"""Single-call experimental Copy and reconstructive Move tools."""

from __future__ import annotations

from typing import Any

from .responses import invoke


def invoke_mutation(operation: str, **arguments: Any):
    return invoke(operation, **arguments)


async def copy_page(
    page_id: str,
    destination_section_id: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    destination_title: str = "",
    include_descendants: bool = False,
) -> dict[str, Any]:
    """Copy a Page scope and report the root's observed final position, never a placement guarantee."""

    return invoke_mutation(
        "copy_page",
        page_id=page_id,
        destination_section_id=destination_section_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        destination_title=destination_title,
        include_descendants=include_descendants,
    )


async def copy_section(
    section_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
    destination_name: str = "",
) -> dict[str, Any]:
    """Copy a Section tree and report its observed final position, not a placement guarantee."""

    return invoke_mutation(
        "copy_section",
        section_id=section_id,
        destination_parent_id=destination_parent_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
        destination_name=destination_name,
    )


async def copy_section_group(
    section_group_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
    destination_name: str = "",
) -> dict[str, Any]:
    """Copy a SectionGroup tree and report its backend name-sorted observed position."""

    return invoke_mutation(
        "copy_section_group",
        section_group_id=section_group_id,
        destination_parent_id=destination_parent_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
        destination_name=destination_name,
    )


async def copy_notebook(
    notebook_id: str,
    expected_name: str,
    expected_modified: str | None = None,
    destination_name: str = "",
    destination_base_folder: str = "",
) -> dict[str, Any]:
    """Copy a Notebook; destination_position is explicitly not applicable."""

    return invoke_mutation(
        "copy_notebook",
        notebook_id=notebook_id,
        expected_name=expected_name,
        expected_modified=expected_modified,
        destination_name=destination_name,
        destination_base_folder=destination_base_folder,
    )


async def move_page(
    page_id: str,
    destination_section_id: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    destination_title: str = "",
    include_descendants: bool = False,
) -> dict[str, Any]:
    """Move a Page scope and report only the root's observed final position."""

    return invoke_mutation(
        "move_page",
        page_id=page_id,
        destination_section_id=destination_section_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        destination_title=destination_title,
        include_descendants=include_descendants,
    )


async def move_section(
    section_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
    destination_name: str = "",
) -> dict[str, Any]:
    """Move a Section and report its observed final position, not a placement guarantee."""

    return invoke_mutation(
        "move_section",
        section_id=section_id,
        destination_parent_id=destination_parent_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
        destination_name=destination_name,
    )


async def move_section_group(
    section_group_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
    destination_name: str = "",
) -> dict[str, Any]:
    """Move a SectionGroup tree and report its backend name-sorted observed position."""

    return invoke_mutation(
        "move_section_group",
        section_group_id=section_group_id,
        destination_parent_id=destination_parent_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
        destination_name=destination_name,
    )


TOOLS = [
    copy_page,
    copy_section,
    copy_section_group,
    copy_notebook,
    move_page,
    move_section,
    move_section_group,
]
