"""Export, navigation, synchronization, and application MCP tools."""

from __future__ import annotations

from typing import Any

from .responses import invoke


async def get_hyperlink(
    object_id: str, page_content_object_id: str = "", web: bool = False
) -> dict[str, Any]:
    """Return a desktop or web hyperlink for a typed OneNote object."""

    return invoke(
        "get_hyperlink",
        object_id=object_id,
        page_content_object_id=page_content_object_id,
        web=web,
    )


async def get_parent(object_id: str) -> dict[str, Any]:
    """Return the typed parent of a OneNote object."""

    return invoke("get_parent", object_id=object_id)


async def publish_object(
    object_id: str, target_path: str, format: str = "pdf", overwrite: bool = False
) -> dict[str, Any]:
    """Write a local file and verify that filesystem effect; do not mutate OneNote."""

    return invoke(
        "publish_object",
        object_id=object_id,
        target_path=target_path,
        format=format,
        overwrite=overwrite,
    )


async def navigate_to(
    object_id: str, page_content_object_id: str = "", new_window: bool = False
) -> dict[str, Any]:
    """Ask the OneNote UI to navigate to an exact object; report action acceptance only."""

    return invoke(
        "navigate_to",
        object_id=object_id,
        page_content_object_id=page_content_object_id,
        new_window=new_window,
    )


async def navigate_to_url(url: str, new_window: bool = False) -> dict[str, Any]:
    """Ask the OneNote UI to navigate to a URL; report action acceptance only."""

    return invoke("navigate_to_url", url=url, new_window=new_window)


async def sync_notebook(notebook_id: str) -> dict[str, Any]:
    """Request synchronization; acceptance does not prove that synchronization completed."""

    return invoke("sync_notebook", notebook_id=notebook_id)


async def close_notebook(
    notebook_id: str, expected_name: str, expected_modified: str | None = None
) -> dict[str, Any]:
    """Close an exact confirmed notebook and converge its observable open state."""

    return invoke(
        "close_notebook",
        notebook_id=notebook_id,
        expected_name=expected_name,
        expected_modified=expected_modified,
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
