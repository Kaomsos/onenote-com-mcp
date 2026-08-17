"""Single-call experimental Copy and reconstructive Move tools."""

from __future__ import annotations

from typing import Any, Literal

from .responses import invoke


def invoke_mutation(operation: str, **arguments: Any):
    return invoke(operation, **arguments)


async def copy_page(
    page_id: str,
    destination_section_id: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    destination_title: str | None = None,
    page_scope: Literal["page_only", "indentation_subtree"] = "page_only",
) -> dict[str, Any]:
    """With Create and Writes, copy one exact Page scope using capability-aware fidelity checks; OneNote chooses the reported destination position."""

    return invoke_mutation(
        "copy_page",
        page_id=page_id,
        destination_section_id=destination_section_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        destination_title=destination_title,
        page_scope=page_scope,
    )


async def copy_section(
    section_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
    destination_name: str | None = None,
) -> dict[str, Any]:
    """With Create and Writes, recursively copy a Section; OneNote chooses the reported destination position."""

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
    destination_name: str | None = None,
) -> dict[str, Any]:
    """With Create and Writes, recursively copy a SectionGroup; OneNote chooses the reported destination position."""

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
    destination_name: str | None = None,
    destination_base_folder: str | None = None,
) -> dict[str, Any]:
    """With Create and Writes, recursively copy an exact Notebook; destination position is not applicable."""

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
    destination_title: str | None = None,
    page_scope: Literal["page_only", "indentation_subtree"] = "page_only",
) -> dict[str, Any]:
    """With Create, Writes, and Deletes, reconstructively move one Page scope only after capability-aware Copy verification; OneNote chooses the reported destination position."""

    return invoke_mutation(
        "move_page",
        page_id=page_id,
        destination_section_id=destination_section_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        destination_title=destination_title,
        page_scope=page_scope,
    )


async def move_section(
    section_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    expected_modified: str | None = None,
    destination_name: str | None = None,
) -> dict[str, Any]:
    """With Create, Writes, and Deletes, reconstructively move a Section after verified Copy; OneNote chooses the reported destination position."""

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
    destination_name: str | None = None,
) -> dict[str, Any]:
    """With Create, Writes, and Deletes, reconstructively move a SectionGroup after verified Copy; OneNote chooses the reported destination position."""

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
