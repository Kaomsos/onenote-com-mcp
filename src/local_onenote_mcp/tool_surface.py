"""Frozen user-facing MCP tool surface and non-public capability catalog."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


USER_TOOL_CATEGORIES: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
    {
        "session": ("health_check", "launch_onenote_gui"),
        "hierarchy_browse": (
            "list_notebooks",
            "get_hierarchy_path",
            "expand_notebook",
            "expand_section_group",
            "expand_section",
            "expand_page",
            "expand_hierarchy",
        ),
        "metadata_get": (
            "get_notebook_metadata",
            "get_section_group_metadata",
            "get_section_metadata",
            "get_page_metadata",
        ),
        "query_and_search": (
            "query_notebook",
            "query_section_group",
            "query_section",
            "query_page",
            "search_pages",
        ),
        "page_content_read": (
            "get_page_text",
            "get_page_content_objects",
            "get_page_content_object_binary",
        ),
        "hyperlink": ("get_hyperlink",),
        "create": (
            "create_notebook",
            "create_section_group",
            "create_section",
            "create_page",
        ),
        "rename": (
            "rename_page",
            "rename_section_group",
            "rename_section",
        ),
        "reorder": ("reorder_page", "reorder_section", "sort_children"),
        "organize": (
            "reparent_page",
            "reparent_section",
            "reparent_section_group",
        ),
        "page_content_mutation": (
            "append_page_content",
            "add_page_image_from_file",
            "replace_page_body",
            "delete_page_content_object",
        ),
        "recoverable_delete": (
            "delete_page",
            "delete_section",
            "delete_section_group",
        ),
        "copy": ("copy_page", "copy_section", "copy_section_group", "copy_notebook"),
        "reconstructive_move": ("move_page", "move_section", "move_section_group"),
        "export": ("export_object_to_pdf",),
        "ui_navigation": ("navigate_to",),
        "notebook_lifecycle": ("request_notebook_sync", "close_notebook"),
    }
)

USER_TOOL_NAMES = tuple(
    name for names in USER_TOOL_CATEGORIES.values() for name in names
)
USER_TOOL_NAME_SET = frozenset(USER_TOOL_NAMES)
TOOL_CATEGORY_BY_NAME = MappingProxyType(
    {
        name: category
        for category, names in USER_TOOL_CATEGORIES.items()
        for name in names
    }
)


@dataclass(frozen=True)
class InternalCapability:
    name: str
    state: Literal["incubating", "internal_helper"]
    reason: str
    internal_callers: tuple[str, ...]
    promotion_requirements: tuple[str, ...]


_PROMOTION = (
    "named user task",
    "typed schema and bounded behavior",
    "independent exposure and authorization review",
    "automated contract coverage",
)

INTERNAL_CAPABILITIES = (
    InternalCapability(
        "resolve_identifier",
        "incubating",
        "Generic name/path resolution creates an ambiguous public selection path.",
        ("HierarchyService.resolve", "manual validation fixture rebinding"),
        _PROMOTION,
    ),
    InternalCapability(
        "get_page_xml",
        "incubating",
        "Raw Page XML is a low-level representation rather than a normal read contract.",
        ("PageService.xml", "typed Page mutation and Copy verification"),
        _PROMOTION + ("raw representation safety review",),
    ),
    InternalCapability(
        "navigate_to_url",
        "incubating",
        "A generic URL target is broader and less typed than object navigation.",
        ("OperationsService.navigate_url",),
        _PROMOTION + ("URL scheme and target policy",),
    ),
    InternalCapability(
        "get_special_locations",
        "internal_helper",
        "Special folders support internal lifecycle and diagnostics, not a standalone user task.",
        ("OperationsService.special_locations",),
        _PROMOTION,
    ),
    InternalCapability(
        "get_parent",
        "internal_helper",
        "Parent relationships are projected by metadata, path, and hierarchy expansion.",
        ("OperationsService.parent", "HierarchyService.path"),
        _PROMOTION,
    ),
)
INTERNAL_CAPABILITY_NAMES = frozenset(item.name for item in INTERNAL_CAPABILITIES)

LEGACY_PUBLIC_NAMES = frozenset(
    {
        "get_notebook",
        "get_section_group",
        "get_section",
        "get_page",
        "get_path",
        "get_page_objects",
        "get_binary_content",
        "list_page_content_objects",
        "get_page_object_binary",
        "update_page_title",
        "append_to_page",
        "add_image_to_page",
        "delete_page_content",
        "publish_object",
        "sync_notebook",
        "start_onenote_app",
    }
)

FORBIDDEN_PRODUCTION_NAMES = frozenset(
    {
        "reorder_section_group",
        "delete_hierarchy",
        "update_hierarchy_xml",
        "find_meta",
        "open_hierarchy",
        "update_page_xml",
        "merge_sections",
        "set_filing_location",
        "plan_copy",
        "plan_move_page",
        "plan_move_section",
        "plan_move_section_group",
        "preview_copy",
        "preview_move",
    }
)


def category_for_tool(name: str) -> str:
    try:
        return TOOL_CATEGORY_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"No frozen user-tool category for {name!r}.") from exc


__all__ = [
    "FORBIDDEN_PRODUCTION_NAMES",
    "INTERNAL_CAPABILITIES",
    "INTERNAL_CAPABILITY_NAMES",
    "LEGACY_PUBLIC_NAMES",
    "TOOL_CATEGORY_BY_NAME",
    "USER_TOOL_CATEGORIES",
    "USER_TOOL_NAMES",
    "USER_TOOL_NAME_SET",
    "InternalCapability",
    "category_for_tool",
]
