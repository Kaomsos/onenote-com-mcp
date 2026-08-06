"""Read-only hierarchy, Page hash, and optional onepkg baseline scenario."""

from __future__ import annotations

import argparse
from pathlib import Path
import platform
import sys
from typing import Any

from ..mcp_stdio_client import MCPStdioClient, READ_ONLY_POLICY
from ..runner import (
    RunnerFailure,
    RuntimeOptions,
    capture_snapshot,
    dry_run_result,
    installed_runner_version,
    manifest_path,
    stable_item,
    utc_now,
    write_json,
)
from ._common import resolve_notebook
from ._config import BASELINE_TOOLS
from .report import render_report

async def run_read(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    if options.dry_run:
        target = args.notebook_name or args.notebook_id
        result = dry_run_result("read", READ_ONLY_POLICY, BASELINE_TOOLS, target, options)
        result["export_onepkg"] = bool(args.export_onepkg)
        return result
    async with MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools=BASELINE_TOOLS,
        run_dir=options.run_dir,
        timeout_seconds=options.timeout,
    ) as client:
        notebook = await resolve_notebook(
            client,
            notebook_name=args.notebook_name,
            notebook_id=args.notebook_id,
        )
        snapshot = await capture_snapshot(client, notebook["id"])
        write_json(options.run_dir / "before.json", snapshot)
        write_json(options.run_dir / "page-hashes.json", snapshot["page_hashes"])
        onepkg_path: Path | None = None
        if args.export_onepkg:
            onepkg_path = (options.run_dir / "baseline.onepkg").resolve()
            if onepkg_path.exists():
                raise RunnerFailure(f"Refusing to overwrite existing baseline export: {onepkg_path}")
            await client.call_tool(
                "publish_object",
                {
                    "object_id": notebook["id"],
                    "target_path": str(onepkg_path),
                    "format": "onepkg",
                    "overwrite": False,
                },
            )
        existing = manifest_path(options.run_dir)
        if not existing.exists():
            write_json(
                existing,
                {
                    "schema_version": 1,
                    "run_id": options.run_dir.name,
                    "created_at": utc_now(),
                    "runner": "tests/manual_validation/run.py",
                    "local_onenote_mcp_version": installed_runner_version(),
                    "python": sys.version,
                    "platform": platform.platform(),
                    "notebook": stable_item(notebook),
                    "structure": {},
                },
            )
        render_report(options.run_dir)
        return {
            "command": "read",
            "notebook": stable_item(notebook),
            "pages_hashed": len(snapshot["page_hashes"]),
            "baseline_onepkg": str(onepkg_path) if onepkg_path else None,
            "run_dir": str(options.run_dir.resolve()),
        }
