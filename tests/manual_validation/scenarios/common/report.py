"""Shared local-artifact report generation."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ...test_utils import display_name, manifest_path, read_json, utc_now

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
    reparent_page_fixture = manifest.get("reparent_page_fixture")
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
                "- Validated content types: `"
                + ", ".join(manifest.get("copy_scenario", {}).get("validated_content_types", []))
                + "`",
                f"- Manual content: `{', '.join(copy_fixture.get('manual_content', []))}`",
                f"- Observed object types: `{', '.join(copy_fixture.get('observed_object_types', []))}`",
                "",
            ]
        )
        semantic = copy_fixture.get("semantic_page")
        if isinstance(semantic, dict):
            lines.extend(
                [
                    f"- Semantic Page ID: `{semantic.get('page_id', '')}`",
                    "- Semantic capabilities: `"
                    + ", ".join(semantic.get("observed_capabilities", []))
                    + "`",
                    "- Semantic acceptance tier: `semantic_list_tag`",
                    "",
                ]
            )
    if isinstance(reparent_page_fixture, dict):
        list_tag = reparent_page_fixture.get("list_tag", {})
        lines.extend(
            [
                "## Reparent Page rich-content fixture",
                "",
                f"- Original Page ID: `{reparent_page_fixture.get('page_id', '')}`",
                "- Automated content: `"
                + ", ".join(reparent_page_fixture.get("automated_content", []))
                + "`",
                "- Observed object types: `"
                + ", ".join(reparent_page_fixture.get("observed_object_types", []))
                + "`",
                "- List/Tag capabilities: `"
                + ", ".join(list_tag.get("observed_capabilities", []))
                + "`",
                "- Acceptance: ID/Tag-index-normalized rich content and semantic object projection "
                "must survive an allowed old-ID → new-ID transition.",
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
                    f"- Target IDs: `{', '.join(result.get('target_ids', []))}`",
                    f"- Restored: `{result.get('restored', 'n/a')}`",
                    f"- Worksite preserved: `{result.get('worksite_preserved', False)}`",
                ]
            )
            lines.append("")
            evidence_fields = (
                ("Capability", "capability"),
                ("Machine comparator passed", "machine_comparator_passed"),
                ("Human verdict", "human_verdict"),
                ("Production verified", "production_verified"),
                ("Production lossless", "production_lossless"),
                ("Diagnostic partial admitted", "diagnostic_partial_admitted"),
                ("Verification tier", "verification_tier"),
                ("Source deleted", "source_deleted"),
                ("Representation status", "representation_status"),
                ("Interactive bootstrap", "interactive_bootstrap"),
                ("Template published", "template_published"),
                ("Mutation eligible", "mutation_eligible"),
                ("Move source deletion allowed", "move_source_deletion_allowed"),
            )
            for label, field in evidence_fields:
                if field in result:
                    lines.append(f"- {label}: `{result[field]}`")
            for label, field in (
                ("Candidate added kinds", "candidate_added_kinds"),
                ("Candidate added capabilities", "candidate_added_capabilities"),
            ):
                values = result.get(field)
                if isinstance(values, list):
                    lines.append(f"- {label}: `{', '.join(str(value) for value in values)}`")
            if any(field in result for _, field in evidence_fields) or any(
                field in result
                for field in ("candidate_added_kinds", "candidate_added_capabilities")
            ):
                lines.append("")
            remaining_state = result.get("remaining_state")
            if isinstance(remaining_state, dict) and remaining_state.get(
                "manual_cleanup_required"
            ):
                lines.extend(
                    [
                        f"- Manual cleanup required: `True`",
                        f"- Preserved target IDs: `{', '.join(remaining_state.get('target_ids', []))}`",
                        "",
                    ]
                )
            plan_paths = [scenario_path / "plan.json"]
            plan_paths.extend(sorted(scenario_path.glob("plan-*.json")))
            plan_paths = [
                path
                for path in plan_paths
                if path.exists() and not path.name.startswith("plan-attempts")
            ]
            for plan_path in plan_paths:
                planned = read_json(plan_path)
                case_label = (
                    plan_path.stem.removeprefix("plan-")
                    if plan_path.name != "plan.json"
                    else ""
                )
                label = (
                    f"Copy case `{case_label}` planned" if case_label else "Planned"
                )
                lines.extend(
                    [
                        f"- {label} content capabilities: `{', '.join(planned.get('content_capabilities', []))}`",
                        f"- {label} lossless candidate: `{planned.get('copyability', {}).get('lossless_candidate', 'n/a')}`",
                    ]
                )
            copy_result_paths = [scenario_path / "copy-result.json"]
            copy_result_paths.extend(sorted(scenario_path.glob("copy-result-*.json")))
            copy_result_paths = [path for path in copy_result_paths if path.exists()]
            for copy_result_path in copy_result_paths:
                copy_result = read_json(copy_result_path)
                copy_report = copy_result.get("copy_report", {})
                case_label = (
                    copy_result_path.stem.removeprefix("copy-result-")
                    if copy_result_path.name != "copy-result.json"
                    else ""
                )
                prefix = f"Copy case `{case_label}` " if case_label else "Copy "
                lines.extend(
                    [
                        f"- {prefix}verified: `{copy_report.get('verified', 'n/a')}`",
                        f"- {prefix}lossless: `{copy_report.get('lossless', 'n/a')}`",
                        f"- {prefix}outcome: `{copy_result.get('outcome', 'copy')}`",
                    ]
                )
                for page_result in copy_report.get("page_results", []):
                    equivalence = page_result.get("equivalence", {})
                    lines.append(
                        "- Page verification: "
                        f"source `{page_result.get('source_page_id', '')}` → "
                        f"target `{page_result.get('target_page_id', '')}`, "
                        f"tier `{equivalence.get('verification_tier', '')}`, "
                        f"equivalent `{equivalence.get('equivalent', False)}`"
                    )
            if plan_paths or copy_result_paths:
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
            "Each named scenario is a complete isolated run: the narrow lifecycle wrapper creates and lease-binds a fresh source Notebook, then exactly one scenario-scoped least-privilege MCP process creates the minimal fixture and performs mutation/evidence/restore work. The wrapper closes only the exact leased source after success unless keep-notebook or keep-worksite was selected. keep-worksite preserves the named action's verified post-mutation state and records exact IDs plus manual cleanup guidance; the special all batch command never forwards it. Permanent OneNote delete and raw XML remain disabled. Scenarios never delete local Notebook directories; only a separate user-confirmed clear maintenance action may remove exact managed validation payloads.",
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
    report_path = render_report(args.run_dir)
    return {"command": "report", "report": str(report_path.resolve())}
