"""Experimental Copy planning, execution, and Page Move tools."""

from __future__ import annotations

from typing import Any

from .context import get_services
from .responses import invoke


async def plan_copy(
    source_id: str,
    destination_parent_id: str = "",
    destination_name: str = "",
    destination_base_folder: str = "",
) -> dict[str, Any]:
    """Build a read-only, content-aware Copy plan and deterministic digest."""

    return invoke(
        lambda: get_services().copying.plan_copy(
            source_id,
            destination_parent_id,
            destination_name,
            destination_base_folder,
        )
    )


async def copy_page(
    page_id: str,
    destination_section_id: str,
    expected_title: str,
    expected_section_id: str,
    plan_digest: str,
    expected_modified: str | None = None,
    destination_title: str = "",
) -> dict[str, Any]:
    """Copy a complete Page indentation subtree to a Section."""

    return invoke(
        lambda: get_services().copying.copy_resource(
            page_id,
            "page",
            destination_section_id,
            destination_title,
            "",
            expected_title,
            expected_section_id,
            expected_modified,
            plan_digest,
        )
    )


async def copy_section(
    section_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    plan_digest: str,
    expected_modified: str | None = None,
    destination_name: str = "",
) -> dict[str, Any]:
    """Recursively copy a Section and all of its Pages."""

    return invoke(
        lambda: get_services().copying.copy_resource(
            section_id,
            "section",
            destination_parent_id,
            destination_name,
            "",
            expected_name,
            expected_parent_id,
            expected_modified,
            plan_digest,
        )
    )


async def copy_section_group(
    section_group_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    plan_digest: str,
    expected_modified: str | None = None,
    destination_name: str = "",
) -> dict[str, Any]:
    """Recursively copy a SectionGroup tree."""

    return invoke(
        lambda: get_services().copying.copy_resource(
            section_group_id,
            "section_group",
            destination_parent_id,
            destination_name,
            "",
            expected_name,
            expected_parent_id,
            expected_modified,
            plan_digest,
        )
    )


async def copy_notebook(
    notebook_id: str,
    expected_name: str,
    plan_digest: str,
    expected_modified: str | None = None,
    destination_name: str = "",
    destination_base_folder: str = "",
) -> dict[str, Any]:
    """Recursively copy a Notebook into a distinct local target folder."""

    return invoke(
        lambda: get_services().copying.copy_resource(
            notebook_id,
            "notebook",
            "",
            destination_name,
            destination_base_folder,
            expected_name,
            None,
            expected_modified,
            plan_digest,
        )
    )


async def plan_move_page(
    page_id: str,
    destination_section_id: str,
    destination_title: str = "",
) -> dict[str, Any]:
    """Plan a Page-subtree Copy followed by non-permanent source deletion."""

    return invoke(
        lambda: get_services().copying.plan_move_page(
            page_id,
            destination_section_id,
            destination_title,
        )
    )


async def move_page(
    page_id: str,
    destination_section_id: str,
    expected_title: str,
    expected_section_id: str,
    plan_digest: str,
    expected_modified: str | None = None,
    destination_title: str = "",
) -> dict[str, Any]:
    """Copy a Page subtree and recycle the source only after lossless verification."""

    return invoke(
        lambda: get_services().copying.move_page(
            page_id,
            destination_section_id,
            expected_title,
            expected_section_id,
            plan_digest,
            expected_modified,
            destination_title,
        )
    )


TOOLS = [
    plan_copy,
    copy_page,
    copy_section,
    copy_section_group,
    copy_notebook,
    plan_move_page,
    move_page,
]
