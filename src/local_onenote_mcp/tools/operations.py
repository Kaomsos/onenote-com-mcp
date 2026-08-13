"""Export, navigation, synchronization, and application MCP tools."""

from __future__ import annotations

from typing import Any

from .context import get_services
from .responses import invoke


async def get_hyperlink(
    object_id: str, page_content_object_id: str = "", web: bool = False
) -> dict[str, Any]:
    """Return a desktop or web hyperlink for a typed OneNote object."""

    return invoke(lambda: get_services().operations.hyperlink(object_id, page_content_object_id, web))


async def get_parent(object_id: str) -> dict[str, Any]:
    """Return the typed parent of a OneNote object."""

    return invoke(lambda: get_services().operations.parent(object_id))


async def publish_object(
    object_id: str, target_path: str, format: str = "pdf", overwrite: bool = False
) -> dict[str, Any]:
    """Publish a notebook, section, or page to a local file."""

    return invoke(lambda: get_services().operations.publish(object_id, target_path, format, overwrite))


async def navigate_to(
    object_id: str, page_content_object_id: str = "", new_window: bool = False
) -> dict[str, Any]:
    """Navigate the OneNote desktop application to an object."""

    return invoke(lambda: get_services().operations.navigate(object_id, page_content_object_id, new_window))


async def navigate_to_url(url: str, new_window: bool = False) -> dict[str, Any]:
    """Navigate OneNote to a OneNote URL."""

    return invoke(lambda: get_services().operations.navigate_url(url, new_window))


async def sync_notebook(notebook_id: str) -> dict[str, Any]:
    """Request synchronization for a typed notebook."""

    return invoke(lambda: get_services().operations.sync_notebook(notebook_id), mutation=True)


async def close_notebook(
    notebook_id: str, expected_name: str, expected_modified: str | None = None
) -> dict[str, Any]:
    """Close a confirmed notebook and verify the resulting state."""

    return invoke(
        lambda: get_services().operations.close_notebook(notebook_id, expected_name, expected_modified),
        mutation=True,
    )


TOOLS = [
    get_hyperlink,
    get_parent,
    publish_object,
    navigate_to,
    navigate_to_url,
    sync_notebook,
    close_notebook,
]
