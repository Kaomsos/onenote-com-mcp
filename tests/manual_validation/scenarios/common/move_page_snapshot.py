"""Content-free cross-Notebook snapshots shared by Page Move scenarios."""

from __future__ import annotations

from typing import Any

from ...mcp_stdio_client import MCPStdioClient
from ...test_utils import capture_snapshot


async def capture_move_page_bundle(
    client: MCPStdioClient,
    notebooks: dict[str, Any],
) -> dict[str, Any]:
    """Merge exact source/destination snapshots with Page ``dateTime`` seconds."""

    from local_onenote_mcp.page.datetime_compare import page_root_datetime, utc_second

    collected: dict[str, str] = {}

    def observer(page: dict[str, Any], xml: str) -> None:
        page_id = str(page.get("id") or "")
        if not page_id:
            return
        normalized = utc_second(page_root_datetime(xml))
        if normalized is not None:
            collected[page_id] = normalized

    roles = {
        role: await capture_snapshot(
            client,
            str(notebooks[role]["id"]),
            page_xml_observer=observer,
        )
        for role in ("source", "destination")
    }
    merged: dict[str, Any] = {
        "notebook_id": str(notebooks["source"]["id"]),
        "notebook_ids": {role: str(notebooks[role]["id"]) for role in roles},
        "roles": roles,
        "items": [],
        "page_hashes": {},
    }
    for role in ("source", "destination"):
        merged["items"].extend(roles[role].get("items", []))
        merged["page_hashes"].update(roles[role].get("page_hashes", {}))
    if collected:
        merged["page_datetime_seconds"] = dict(collected)
    else:
        seconds: dict[str, str] = {}
        for role in ("source", "destination"):
            extra = roles[role].get("page_datetime_seconds")
            if isinstance(extra, dict):
                seconds.update(
                    {
                        str(key): value
                        for key, value in extra.items()
                        if isinstance(key, str) and isinstance(value, str) and value
                    }
                )
        merged["page_datetime_seconds"] = seconds
    return merged


__all__ = ["capture_move_page_bundle"]
