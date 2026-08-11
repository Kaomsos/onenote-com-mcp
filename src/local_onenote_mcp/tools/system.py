"""System diagnostics and identifier-resolution MCP tools."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ..policy import CopyBudget, MutationPolicy, SearchBudget
from ..services import (
    IDENTIFIER_RESOLUTION_ORDER,
    RESOURCE_TYPES,
    SEARCH_BACKENDS,
    SEARCH_SCOPE_TYPES,
)
from ..settings import MCP_NAME
from .context import get_services
from .responses import invoke


async def health_check() -> dict[str, Any]:
    """Verify local OneNote COM access and return a small hierarchy summary."""

    def action() -> dict[str, Any]:
        services = get_services()
        items = services.hierarchy.resources(include_recycle_bin=False)
        policy = MutationPolicy.current()
        budget = SearchBudget.current()
        copy_budget = CopyBudget.current()
        return {
            "server": MCP_NAME,
            "transport": "stdio",
            "python_executable": sys.executable,
            "module_path": str(Path(__file__).resolve()),
            "process_cwd": str(Path.cwd()),
            "timeout_seconds": services.hierarchy.bridge.timeout_seconds,
            "max_text_chars": services.pages.max_text_chars,
            "identifier_resolution_order": IDENTIFIER_RESOLUTION_ORDER,
            "search_default_backend": "local_scan",
            "search_backends": list(SEARCH_BACKENDS),
            "search_scope_types": list(SEARCH_SCOPE_TYPES),
            "content_formats": ["plain", "html", "markdown"],
            "mutation_policy": {
                "writes_enabled": policy.writes_enabled,
                "deletes_enabled": policy.deletes_enabled,
                "permanent_deletes_enabled": policy.permanent_deletes_enabled,
                "experimental_reparent_enabled": policy.experimental_reparent_enabled,
                "experimental_reorder_section_enabled": policy.experimental_reorder_section_enabled,
                "experimental_reorder_section_group_enabled": (
                    policy.experimental_reorder_section_group_enabled
                ),
                "experimental_copy_enabled": policy.experimental_copy_enabled,
                "move_page_enabled": policy.move_page_enabled,
                "move_containers_enabled": policy.move_containers_enabled,
                "raw_xml_enabled": policy.raw_xml_enabled,
            },
            "search_budget": {
                "max_pages": budget.max_pages,
                "max_page_chars": budget.max_page_chars,
                "max_total_chars": budget.max_total_chars,
                "max_seconds": budget.max_seconds,
            },
            "copy_budget": {
                "max_resources": copy_budget.max_resources,
                "max_pages": copy_budget.max_pages,
                "max_content_objects": copy_budget.max_content_objects,
                "max_page_xml_bytes": copy_budget.max_page_xml_bytes,
                "max_total_xml_bytes": copy_budget.max_total_xml_bytes,
                "max_plan_seconds": copy_budget.max_plan_seconds,
                "max_execute_seconds": copy_budget.max_execute_seconds,
            },
            "notebooks": sum(item["resource_type"] == "notebook" for item in items),
            "sections": sum(item["resource_type"] == "section" for item in items),
            "write_backend": "OneNote desktop COM API",
        }

    return invoke(action)


async def resolve_identifier(identifier: str, item_type: str = "") -> dict[str, Any]:
    """Resolve a OneNote identifier to one live typed object for read-only interaction."""

    def action() -> dict[str, Any]:
        if not identifier:
            raise ValueError("identifier is required.")
        normalized_type = item_type.strip().casefold() or None
        if normalized_type and normalized_type not in RESOURCE_TYPES:
            allowed = ", ".join(sorted(RESOURCE_TYPES))
            raise ValueError(f"item_type must be empty or one of: {allowed}")
        return {
            "item": get_services().hierarchy.resolve(identifier, normalized_type),
            "identifier_resolution_order": IDENTIFIER_RESOLUTION_ORDER,
        }

    return invoke(action)


async def get_special_locations() -> dict[str, Any]:
    """Return OneNote's local special folders."""

    return invoke(get_services().operations.special_locations)


TOOLS = [health_check, resolve_identifier, get_special_locations]
