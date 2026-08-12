"""Experimental Copy planning and reconstructive Move tools."""

from __future__ import annotations

from typing import Any

from .context import get_services
from .responses import invoke


async def plan_copy(
    source_id: str,
    destination_parent_id: str = "",
    destination_name: str = "",
    destination_base_folder: str = "",
    include_descendants: bool = False,
) -> dict[str, Any]:
    """Build a read-only, content-aware Copy plan and deterministic digest."""

    return invoke(
        lambda: get_services().copying.plan_copy(
            source_id,
            destination_parent_id,
            destination_name,
            destination_base_folder,
            include_descendants,
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
    include_descendants: bool = False,
) -> dict[str, Any]:
    """Copy a Page scope and report the root's observed final position, never a placement guarantee."""

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
            include_descendants,
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
    """Copy a Section tree and report its observed final position, not a placement guarantee."""

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
    """Copy a SectionGroup tree and report its backend name-sorted observed position."""

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
    """Copy a Notebook; destination_position is explicitly not applicable."""

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
    include_descendants: bool = False,
) -> dict[str, Any]:
    """Plan a selected Page scope Copy followed by non-permanent source deletion."""

    return invoke(
        lambda: get_services().copying.plan_move_page(
            page_id,
            destination_section_id,
            destination_title,
            include_descendants,
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
    include_descendants: bool = False,
) -> dict[str, Any]:
    """Move a Page scope and report only the root's observed final position."""

    return invoke(
        lambda: get_services().copying.move_page(
            page_id,
            destination_section_id,
            expected_title,
            expected_section_id,
            plan_digest,
            expected_modified,
            destination_title,
            include_descendants,
        )
    )


async def plan_move_section(
    section_id: str,
    destination_parent_id: str,
    destination_name: str = "",
) -> dict[str, Any]:
    """Plan a cross-Notebook Section Copy followed by one non-permanent root deletion."""

    return invoke(
        lambda: get_services().copying.plan_move_section(
            section_id, destination_parent_id, destination_name
        )
    )


async def move_section(
    section_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    plan_digest: str,
    expected_modified: str | None = None,
    destination_name: str = "",
) -> dict[str, Any]:
    """Move a Section and report its observed final position, not a placement guarantee."""

    return invoke(
        lambda: get_services().copying.move_section(
            section_id,
            destination_parent_id,
            expected_name,
            expected_parent_id,
            plan_digest,
            expected_modified,
            destination_name,
        )
    )


async def plan_move_section_group(
    section_group_id: str,
    destination_parent_id: str,
    destination_name: str = "",
) -> dict[str, Any]:
    """Plan a cross-Notebook SectionGroup Copy followed by one non-permanent root deletion."""

    return invoke(
        lambda: get_services().copying.plan_move_section_group(
            section_group_id, destination_parent_id, destination_name
        )
    )


async def move_section_group(
    section_group_id: str,
    destination_parent_id: str,
    expected_name: str,
    expected_parent_id: str,
    plan_digest: str,
    expected_modified: str | None = None,
    destination_name: str = "",
) -> dict[str, Any]:
    """Move a SectionGroup tree and report its backend name-sorted observed position."""

    return invoke(
        lambda: get_services().copying.move_section_group(
            section_group_id,
            destination_parent_id,
            expected_name,
            expected_parent_id,
            plan_digest,
            expected_modified,
            destination_name,
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
    plan_move_section,
    move_section,
    plan_move_section_group,
    move_section_group,
]
