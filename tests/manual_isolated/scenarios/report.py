"""Local artifact report generation scenario."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..runner import (
    load_manifest,
    manifest_path,
    read_json,
    display_name,
    utc_now,
    write_json,
)

def render_report(run_dir: Path) -> Path:
    manifest = read_json(manifest_path(run_dir))
    notebook = manifest.get("notebook", {})
    lines = [
        "# OneNote isolated manual smoke report",
        "",
        f"- Run ID: `{manifest.get('run_id', run_dir.name)}`",
        f"- Notebook: `{display_name(notebook)}`",
        f"- Notebook ID: `{notebook.get('id', '')}`",
        f"- Generated: `{utc_now()}`",
        f"- local-onenote-mcp: `{manifest.get('local_onenote_mcp_version', 'unknown')}`",
        "- Copy/Move fidelity is accepted only from explicit named scenario results below.",
        "- Retry policy: mutation calls are never retried; read-only transport failures are attempted at most twice.",
        "",
    ]
    copy_fixture = manifest.get("copy_fixture")
    if isinstance(copy_fixture, dict):
        lines.extend(
            [
                "## Copy fixture",
                "",
                f"- Page ID: `{copy_fixture.get('page_id', '')}`",
                f"- Automated content: `{', '.join(copy_fixture.get('automated_content', []))}`",
                f"- Manual content: `{', '.join(copy_fixture.get('manual_content', []))}`",
                f"- Observed object types: `{', '.join(copy_fixture.get('observed_object_types', []))}`",
                "",
            ]
        )
    lines.extend(["## Scenarios", ""])
    found = False
    scenarios_root = run_dir / "scenarios"
    if scenarios_root.exists():
        for scenario_path in sorted(path for path in scenarios_root.iterdir() if path.is_dir()):
            result_path = scenario_path / "result.json"
            failure_path = scenario_path / "failure.json"
            if result_path.exists():
                result = read_json(result_path)
            elif failure_path.exists():
                result = read_json(failure_path)
            else:
                continue
            found = True
            lines.extend(
                [
                    f"### {result.get('scenario', scenario_path.name)}",
                    "",
                    f"- Status: `{result.get('status', 'unknown')}`",
                    f"- Target ID: `{result.get('target_id', '')}`",
                    f"- Restored: `{result.get('restored', 'n/a')}`",
                    "",
                ]
            )
            plan_path = scenario_path / "plan.json"
            if plan_path.exists():
                planned = read_json(plan_path)
                lines.extend(
                    [
                        f"- Planned content capabilities: `{', '.join(planned.get('content_capabilities', []))}`",
                        f"- Planned lossless candidate: `{planned.get('copyability', {}).get('lossless_candidate', 'n/a')}`",
                    ]
                )
            copy_result_path = scenario_path / "copy-result.json"
            if copy_result_path.exists():
                copy_result = read_json(copy_result_path)
                copy_report = copy_result.get("copy_report", {})
                lines.extend(
                    [
                        f"- Copy verified: `{copy_report.get('verified', 'n/a')}`",
                        f"- Copy lossless: `{copy_report.get('lossless', 'n/a')}`",
                        f"- Copy outcome: `{copy_result.get('outcome', 'copy')}`",
                    ]
                )
            if plan_path.exists() or copy_result_path.exists():
                lines.append("")
            if result.get("error"):
                lines.extend([f"Error: {result['error']}", ""])
    if not found:
        lines.extend(["No mutation scenario has completed yet.", ""])
    lines.extend(
        [
            "## Safety boundary",
            "",
            "Each command started its own MCP process with a static minimal policy. Permanent delete and raw XML remained disabled. Delete fixtures are not automatically restored because the typed tool profile has no recycle-bin restore operation.",
            "",
        ]
    )
    validation_environment = manifest.get("validation_environment")
    if isinstance(validation_environment, dict):
        lines.extend(
            [
                "## Validated environment",
                "",
                f"- OneNote version: `{validation_environment.get('onenote_version', 'not recorded')}`",
                f"- Office channel: `{validation_environment.get('office_channel', 'not recorded')}`",
                f"- Recorded: `{validation_environment.get('recorded_at', '')}`",
                "",
            ]
        )
    path = run_dir / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path

async def run_report(args: argparse.Namespace) -> dict[str, Any]:
    if args.onenote_version or args.office_channel:
        manifest = load_manifest(args.run_dir)
        previous = manifest.get("validation_environment", {})
        manifest["validation_environment"] = {
            "onenote_version": args.onenote_version
            or previous.get("onenote_version", "not recorded"),
            "office_channel": args.office_channel
            or previous.get("office_channel", "not recorded"),
            "recorded_at": utc_now(),
        }
        write_json(manifest_path(args.run_dir), manifest)
    report_path = render_report(args.run_dir)
    return {"command": "report", "report": str(report_path.resolve())}
