"""Read-only exact-name discovery and tree inspection scenario."""

from __future__ import annotations

import argparse
from typing import Any

from ..mcp_stdio_client import MCPStdioClient, READ_ONLY_POLICY
from ..runner import RuntimeOptions, dry_run_result, stable_item, write_json
from ._common import resolve_notebook
from ._config import READ_TOOLS

async def run_inspect(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    if options.dry_run:
        return dry_run_result("inspect", READ_ONLY_POLICY, READ_TOOLS, args.notebook_name, options)
    async with MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools=READ_TOOLS,
        run_dir=options.run_dir,
        timeout_seconds=options.timeout,
    ) as client:
        notebook = await resolve_notebook(client, notebook_name=args.notebook_name)
        tree = await client.call_tool("get_tree", {"root_id": notebook["id"], "max_depth": 8})
        result = {
            "command": "inspect",
            "notebook": stable_item(notebook),
            "tree": tree["tree"],
            "mutation_policy": READ_ONLY_POLICY.as_dict(),
            "run_dir": str(options.run_dir.resolve()),
        }
        write_json(options.run_dir / "inspect.json", result)
        return result
