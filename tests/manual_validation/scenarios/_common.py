"""Internal helpers shared by non-mutation scenarios."""

from __future__ import annotations

from typing import Any, Iterable

from ..mcp_stdio_client import MCPStdioClient
from ..runner import RunnerFailure, display_name

def exact_matches(items: Iterable[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    folded = name.casefold()
    return [item for item in items if display_name(item).casefold() == folded]

def exactly_one(items: Iterable[dict[str, Any]], name: str, label: str) -> dict[str, Any] | None:
    matches = exact_matches(items, name)
    if len(matches) > 1:
        paths = ", ".join(str(item.get("path")) for item in matches)
        raise RunnerFailure(f"Duplicate {label} named '{name}': {paths}")
    return matches[0] if matches else None

async def resolve_notebook(
    client: MCPStdioClient,
    *,
    notebook_name: str | None = None,
    notebook_id: str | None = None,
) -> dict[str, Any]:
    if bool(notebook_name) == bool(notebook_id):
        raise RunnerFailure("Specify exactly one of --notebook-name or --notebook-id.")
    if notebook_id:
        return (await client.call_tool("get_notebook", {"notebook_id": notebook_id}))["item"]
    listed = await client.call_tool("list_notebooks", {})
    notebook = exactly_one(listed.get("notebooks", []), str(notebook_name), "notebook")
    if notebook is None:
        raise RunnerFailure(f"No active notebook has the exact name '{notebook_name}'.")
    return notebook
