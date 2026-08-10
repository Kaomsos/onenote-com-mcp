"""Run a strictly read-only smoke test through the MCP stdio transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


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
        help="Optional Notebook ID, exact path, or unique name to resolve and inspect read-only.",
    )
    return parser.parse_args()


def text_of(result: Any) -> str:
    return "\n".join(getattr(content, "text", str(content)) for content in result.content)


def parse_tool_result(result: Any) -> dict[str, Any]:
    text = text_of(result)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"Tool returned non-JSON text: {text[:500]}"}


async def call_tool(session: ClientSession, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return parse_tool_result(await session.call_tool(name, args))


async def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    params = StdioServerParameters(
        command=args.server_python,
        args=["-m", "local_onenote_mcp.server"],
        env={
            "LOCAL_ONENOTE_MCP_TIMEOUT": "90",
            "LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS": "60000",
            "LOCAL_ONENOTE_ENABLE_WRITES": "false",
            "LOCAL_ONENOTE_ENABLE_DELETES": "false",
            "LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES": "false",
            "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT": "false",
            "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION": "false",
            "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION_GROUP": "false",
            "LOCAL_ONENOTE_ENABLE_RAW_XML": "false",
        },
    )
    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            required_tools = {
                "health_check",
                "resolve_identifier",
                "list_notebooks",
                "list_sections",
                "get_tree",
                "query_hierarchy",
                "reparent_page",
                "reparent_section",
                "reparent_section_group",
            }
            forbidden_default_tools = {
                "update_page_xml",
                "update_hierarchy_xml",
                "delete_hierarchy",
                "merge_sections",
            }
            missing_tools = sorted(required_tools - tool_names)
            unexpectedly_exposed = sorted(forbidden_default_tools & tool_names)
            tools_ok = not missing_tools and not unexpectedly_exposed
            checks.append(
                {
                    "name": "list_tools",
                    "ok": tools_ok,
                    "tool_count": len(tool_names),
                    "missing": missing_tools,
                    "unexpectedly_exposed": unexpectedly_exposed,
                }
            )
            if not tools_ok:
                failures.append("Default tool profile did not match the typed safe surface.")

            health = await call_tool(session, "health_check", {})
            policy = health.get("mutation_policy", {})
            health_ok = health.get("ok") and not any(policy.values())
            checks.append({"name": "health_check", "ok": health_ok, "result": health})
            if not health_ok:
                failures.append("health_check failed or a mutation profile was enabled.")

            notebooks = await call_tool(session, "list_notebooks", {})
            checks.append({"name": "list_notebooks", "ok": notebooks.get("ok"), "count": notebooks.get("count")})
            if not notebooks.get("ok"):
                failures.append(f"list_notebooks failed: {notebooks.get('error')}")

            if args.notebook:
                resolved = await call_tool(
                    session,
                    "resolve_identifier",
                    {"identifier": args.notebook, "item_type": "notebook"},
                )
                notebook_id = (resolved.get("item") or {}).get("id", "")
                checks.append({"name": "resolve_identifier:notebook", "ok": resolved.get("ok"), "id": notebook_id})
                if not resolved.get("ok"):
                    failures.append(f"resolve_identifier notebook failed: {resolved.get('error')}")
                else:
                    sections = await call_tool(session, "list_sections", {"parent_id": notebook_id})
                    tree = await call_tool(session, "get_tree", {"root_id": notebook_id})
                    checks.append({"name": "list_sections", "ok": sections.get("ok"), "count": sections.get("count")})
                    checks.append({"name": "get_tree", "ok": tree.get("ok")})
                    if not sections.get("ok") or not tree.get("ok"):
                        failures.append("Notebook hierarchy inspection failed.")

    return {"ok": not failures, "mode": "read_only", "checks": checks, "failures": failures}


def main() -> int:
    result = asyncio.run(run_smoke(parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
