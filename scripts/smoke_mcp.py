"""Run a strictly read-only smoke test through the MCP stdio transport."""

from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import json
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from local_onenote_mcp.tool_surface import USER_TOOL_NAMES, USER_TOOL_NAME_SET


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only smoke test for the local OneNote MCP server.")
    parser.add_argument(
        "--server-python",
        default=sys.executable,
        help="Python executable used to run -m local_onenote_mcp.server.",
    )
    parser.add_argument(
        "--notebook",
        default="",
        help="Optional exact open Notebook COM ID to inspect read-only.",
    )
    parser.add_argument(
        "--tools-only",
        action="store_true",
        help=(
            "Stop after the MCP tools/list transport check; this does not probe or "
            "connect to OneNote Desktop."
        ),
    )
    parser.add_argument(
        "--include-tool-snapshot",
        action="store_true",
        help="Include the exact names, descriptions, input schemas, and output schemas in JSON output.",
    )
    return parser.parse_args()


def text_of(result: Any) -> str:
    return "\n".join(getattr(content, "text", str(content)) for content in result.content)


def parse_tool_result(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if set(structured) == {"result"} and isinstance(structured["result"], dict):
            return structured["result"]
        return structured
    text = text_of(result)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"Tool returned non-JSON text: {text[:500]}"}


async def call_tool(session: ClientSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return parse_tool_result(await session.call_tool(name, args))


def project_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """Project the public contract fields returned by MCP tools/list."""

    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema,
            "output_schema": getattr(tool, "outputSchema", None),
        }
        for tool in tools
    ]


def snapshot_digest(snapshot: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


async def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    params = StdioServerParameters(
        command=args.server_python,
        args=["-m", "local_onenote_mcp.server"],
        env={
            "LOCAL_ONENOTE_MCP_TIMEOUT": "90",
            "LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS": "60000",
            "LOCAL_ONENOTE_ENABLE_WRITES": "false",
            "LOCAL_ONENOTE_ENABLE_DELETES": "false",
            "LOCAL_ONENOTE_ENABLE_ORGANIZE": "false",
            "LOCAL_ONENOTE_ENABLE_COPY": "false",
            "LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO": "false",
            "LOCAL_ONENOTE_ENABLE_UI_CONTROL": "false",
            "LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE": "false",
        },
    )
    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            snapshot = project_tools(tools.tools)
            ordered_tool_names = tuple(item["name"] for item in snapshot)
            tool_names = set(ordered_tool_names)
            missing_tools = sorted(USER_TOOL_NAME_SET - tool_names)
            unexpectedly_exposed = sorted(tool_names - USER_TOOL_NAME_SET)
            duplicate_names = sorted(
                name for name in tool_names if ordered_tool_names.count(name) > 1
            )
            incomplete_contracts = sorted(
                item["name"]
                for item in snapshot
                if not item["description"]
                or not isinstance(item["input_schema"], dict)
                or item["input_schema"].get("type") != "object"
                or not isinstance(item["output_schema"], dict)
                or item["output_schema"].get("type") != "object"
            )
            tools_ok = (
                ordered_tool_names == USER_TOOL_NAMES
                and not duplicate_names
                and not incomplete_contracts
            )
            checks.append(
                {
                    "name": "list_tools",
                    "ok": tools_ok,
                    "tool_count": len(ordered_tool_names),
                    "ordered_names": list(ordered_tool_names),
                    "missing": missing_tools,
                    "unexpectedly_exposed": unexpectedly_exposed,
                    "duplicate_names": duplicate_names,
                    "incomplete_contracts": incomplete_contracts,
                    "snapshot_sha256": snapshot_digest(snapshot),
                }
            )
            if not tools_ok:
                failures.append(
                    "MCP tools/list did not match the exact ordered public contract surface."
                )

            if args.tools_only:
                result = {
                    "ok": not failures,
                    "mode": "transport_tools_only",
                    "onenote_accessed": False,
                    "checks": checks,
                    "failures": failures,
                }
                if args.include_tool_snapshot:
                    result["tool_snapshot"] = snapshot
                return result

            health = await call_tool(session, "health_check", {})
            health_result = health.get("result", {})
            policy = health_result.get("mutation_policy", {})
            health_ok = health.get("ok") is True and not any(policy.values())
            checks.append({"name": "health_check", "ok": health_ok, "result": health})
            if not health_ok:
                failures.append("health_check failed or a mutation profile was enabled.")

            notebooks = await call_tool(session, "list_notebooks", {})
            notebooks_result = notebooks.get("result", {})
            checks.append({"name": "list_notebooks", "ok": notebooks.get("ok"), "count": notebooks_result.get("count")})
            if not notebooks.get("ok"):
                failures.append(f"list_notebooks failed: {notebooks.get('error')}")

            if args.notebook:
                typed_tree = await call_tool(
                    session, "expand_notebook", {"notebook_id": args.notebook}
                )
                depth_tree = await call_tool(
                    session, "expand_hierarchy", {"root_id": args.notebook}
                )
                checks.append({"name": "expand_notebook", "ok": typed_tree.get("ok")})
                checks.append({"name": "expand_hierarchy", "ok": depth_tree.get("ok")})
                if not typed_tree.get("ok") or not depth_tree.get("ok"):
                    failures.append("Notebook hierarchy inspection failed.")

    result = {
        "ok": not failures,
        "mode": "read_only",
        "checks": checks,
        "failures": failures,
    }
    if args.include_tool_snapshot:
        result["tool_snapshot"] = snapshot
    return result


def main() -> int:
    result = asyncio.run(run_smoke(parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
