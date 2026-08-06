"""Shared local-artifact report generation."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ...test_utils import (
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
    scenario_spec = manifest.get("scenario_spec")
    if isinstance(scenario_spec, dict):
        fixture_profile = scenario_spec.get("fixture_profile", {})
        lines.extend(
            [
                "## Scenario contract",
                "",
                f"- Scenario: `{scenario_spec.get('scenario', '')}`",
                f"- Fixture profile: `{fixture_profile.get('name', '')}`",
                f"- MCP process maximum: `{manifest.get('mcp_process_contract', {}).get('maximum_starts', '')}`",
                f"- Tool allowlist: `{', '.join(scenario_spec.get('tool_allowlist', []))}`",
                "",
            ]
        )
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
    lifecycle_path = run_dir / "lifecycle.json"
    failure_path = run_dir / "run-failure.json"
    if lifecycle_path.exists():
        lifecycle = read_json(lifecycle_path)
        lines.extend(
            [
                "## Isolated run lifecycle",
                "",
                f"- Mode: `{lifecycle.get('mode', 'unknown')}`",
                f"- Status: `{lifecycle.get('status', 'unknown')}`",
                f"- Source closed: `{lifecycle.get('closed', False)}`",
                f"- Filesystem deleted: `{lifecycle.get('filesystem_deleted', False)}`",
                f"- Preserved paths: `{', '.join(lifecycle.get('preserved_paths', []))}`",
                "",
            ]
        )
    if failure_path.exists():
        failure = read_json(failure_path)
        lines.extend(
            [
                "## Isolated run failure",
                "",
                f"- Failed step: `{failure.get('failed_step', 'unknown')}`",
                f"- Finalization attempted: `{failure.get('finalization_attempted', False)}`",
                f"- Remaining state: {failure.get('remaining_state', '')}",
                "",
            ]
        )
    metrics_path = run_dir / "run-metrics.json"
    if metrics_path.exists():
        metrics = read_json(metrics_path)
        phases = metrics.get("phases_seconds", {})
        bridge_calls = metrics.get("observed_bridge_calls", {})
        lines.extend(
            [
                "## Process and timing evidence",
                "",
                f"- Architecture: `{metrics.get('architecture', '')}`",
                f"- MCP starts: `{metrics.get('observed_mcp_process_starts', '')}`",
                f"- MCP tool calls: `{metrics.get('observed_mcp_tool_calls', '')}`",
                f"- Bridge calls: `{bridge_calls.get('total', '')}` "
                f"(scenario `{bridge_calls.get('scenario_mcp', '')}`, "
                f"lifecycle `{bridge_calls.get('lifecycle_wrapper', '')}`)",
                f"- Legacy expected MCP starts: `{metrics.get('legacy_expected_mcp_process_starts', '')}`",
                f"- Scenario process seconds: `{phases.get('scenario_process', 'n/a')}`",
                f"- Total seconds: `{phases.get('total', phases.get('total_at_process_exit', 'n/a'))}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Safety boundary",
            "",
            "Each named scenario is a complete isolated run: the narrow lifecycle wrapper creates and lease-binds a fresh source Notebook, then exactly one scenario-scoped least-privilege MCP process creates the minimal fixture and performs mutation/evidence/restore work. The wrapper closes only the exact leased source after success unless keep-notebook was selected. Permanent OneNote delete and raw XML remain disabled. Local Notebook directories are never deleted.",
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
