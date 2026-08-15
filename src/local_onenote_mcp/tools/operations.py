"""Export, navigation, synchronization, and application MCP tools."""

from __future__ import annotations

from typing import Any, Literal

from .responses import invoke


async def get_hyperlink(
    object_id: str,
    page_content_object_id: str | None = None,
    link_type: Literal["desktop", "web"] = "desktop",
) -> dict[str, Any]:
    """Return a desktop or web hyperlink for a typed OneNote object."""

    return invoke(
        "get_hyperlink",
        object_id=object_id,
        page_content_object_id=page_content_object_id,
        link_type=link_type,
    )


async def export_object_to_pdf(object_id: str, target_path: str) -> dict[str, Any]:
    """With Local File IO, write one new PDF and verify that effect; never overwrite."""

    return invoke(
        "export_object_to_pdf",
        object_id=object_id,
        target_path=target_path,
    )


async def navigate_to(
    object_id: str,
    page_content_object_id: str | None = None,
    new_window: bool = False,
) -> dict[str, Any]:
    """With UI Control, navigate the OneNote GUI to an exact object and report action acceptance only."""

    return invoke(
        "navigate_to",
        object_id=object_id,
        page_content_object_id=page_content_object_id,
        new_window=new_window,
    )


async def request_notebook_sync(notebook_id: str) -> dict[str, Any]:
    """With Notebook Lifecycle, request sync for an exact Notebook; acceptance does not prove completion."""

    return invoke("request_notebook_sync", notebook_id=notebook_id)


async def close_notebook(
    notebook_id: str, expected_name: str, expected_modified: str | None = None
) -> dict[str, Any]:
    """With Notebook Lifecycle, close an exact confirmed Notebook and converge its observable open state."""

    return invoke(
        "close_notebook",
        notebook_id=notebook_id,
        expected_name=expected_name,
        expected_modified=expected_modified,
    )


TOOLS = [
    get_hyperlink,
    export_object_to_pdf,
    navigate_to,
    request_notebook_sync,
    close_notebook,
]
