"""Shared lifecycle orchestration for self-contained scenario suites."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import time
from typing import Any

from ...mcp_stdio_client import (
    COPY_BUDGET_ENV,
    MCPStdioClient,
    ScenarioPolicy,
)
from ...lifecycle import NotebookLifecycleWrapper
from ...runtime import EXIT_RESTORE, RestoreFailure, RunnerFailure, RuntimeOptions
from ...test_utils import (
    load_manifest,
    read_json,
    resolve_manifest_item,
    scenario_dir,
    utc_now,
    write_json,
)
from .fixtures import prepare_scenario_fixture
from .report import render_report
from .specs import SCENARIO_SPECS
from .registry import SCENARIO_REGISTRY


PUBLIC_SCENARIOS = SCENARIO_REGISTRY.public_names

# Compatibility view for callers that inspect the static policy table.  Each
# entry is the complete fixture + mutation closure for one scenario process.
SCENARIO_POLICIES = {
    name: (spec.policy, set(spec.tool_allowlist), spec.fixture.name)
    for name, spec in SCENARIO_SPECS.items()
}


def _validate_notebook_name(name: str) -> None:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned or cleaned in {".", ".."} or cleaned != name:
        raise RunnerFailure(
            "--notebook-name must be a non-empty Windows-safe leaf name without normalization."
        )


def _assert_fresh_run_dir(run_dir: Path) -> None:
    if run_dir.exists() and not run_dir.is_dir():
        raise RunnerFailure("--run-dir must identify a directory, not a file.")
    if run_dir.exists() and any(run_dir.iterdir()):
        raise RunnerFailure(
            "--run-dir must be absent or empty so evidence and disposable targets cannot be mixed."
        )


def _step(
    name: str,
    policy: ScenarioPolicy,
    tools: set[str],
    target: str,
) -> dict[str, Any]:
    return {
        "step": name,
        "target": target,
        "mutation_policy": policy.as_dict(),
        "tool_allowlist": sorted(tools),
    }


def isolated_dry_run(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    spec = SCENARIO_REGISTRY.get(args.scenario).spec
    steps: list[dict[str, Any]] = [
        {
            "step": "create-source-notebook",
            "trust_boundary": "narrow lifecycle wrapper",
            "allowed_operations": ["create_fresh_notebook"],
            "target": "new exact-name Notebook under run-dir/notebooks",
        },
        _step(
            args.scenario,
            spec.policy,
            set(spec.tool_allowlist),
            f"fixture profile {spec.fixture.name} and selected mutation",
        ),
        {
            "step": "report",
            "trust_boundary": "local artifacts only",
            "tool_allowlist": [],
            "target": "run-dir evidence",
        },
    ]
    if not args.keep_notebook:
        steps.append(
            {
                "step": "close-source-notebook",
                "trust_boundary": "narrow lifecycle wrapper",
                "allowed_operations": ["get_exact_notebook", "close_exact_notebook"],
                "target": "exact lifecycle lease Notebook ID/name/path",
            }
        )
    return {
        "command": args.scenario,
        "scenario": args.scenario,
        "dry_run": True,
        "human_only": True,
        "agent_execution_prohibited": True,
        "notebook_name": args.notebook_name,
        "run_dir": str(options.run_dir.resolve()),
        "notebook_base_folder": str((options.run_dir.resolve() / "notebooks").resolve()),
        "fixture_profile": spec.fixture.as_dict(),
        "scenario_spec": spec.as_dict(),
        "timeout_seconds": options.timeout,
        "copy_budget": {
            field: value for field, (_env_name, value) in COPY_BUDGET_ENV.items()
        },
        "lifecycle": "keep" if args.keep_notebook else "close",
        "lifecycle_lease": str((options.run_dir.resolve() / "lifecycle-lease.json")),
        "expected_mcp_process_starts": 1,
        "server_started": False,
        "ordered_steps": steps,
        "filesystem_cleanup": {
            "enabled": False,
            "result": "source and Copy Notebook directories are always preserved",
        },
    }


def _initial_state(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "command": args.scenario,
        "scenario": args.scenario,
        "status": "running",
        "human_only": True,
        "agent_execution_prohibited": True,
        "started_at": utc_now(),
        "notebook_name": args.notebook_name,
        "run_dir": str(options.run_dir.resolve()),
        "lifecycle": "keep" if args.keep_notebook else "close",
        "completed_steps": [],
        "current_step": "create-source-notebook",
        "finalization_started": False,
    }


def _preserved_notebook_paths(run_dir: Path, manifest: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    source_path = manifest.get("disposable_targets", {}).get("source_notebook_path")
    if source_path:
        paths.append(str(source_path))
    copy_path = scenario_dir(run_dir, "copy-notebook") / "restored.json"
    if copy_path.exists():
        target_path = read_json(copy_path).get("target_path")
        if target_path:
            paths.append(str(target_path))
    return list(dict.fromkeys(paths))


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def _refresh_call_metrics(metrics: dict[str, Any], run_dir: Path) -> None:
    scenario_bridge = _line_count(run_dir / "scenario-mcp" / "bridge-calls.jsonl")
    lifecycle_bridge = _line_count(run_dir / "lifecycle-bridge-calls.jsonl")
    metrics["observed_bridge_calls"] = {
        "scenario_mcp": scenario_bridge,
        "lifecycle_wrapper": lifecycle_bridge,
        "total": scenario_bridge + lifecycle_bridge,
    }
    metrics["observed_mcp_tool_calls"] = _line_count(
        run_dir / "scenario-mcp" / "calls.jsonl"
    )


async def finalize_notebook(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    wrapper: NotebookLifecycleWrapper | None = None,
) -> dict[str, Any]:
    wrapper = wrapper or NotebookLifecycleWrapper(
        options.run_dir,
        timeout_seconds=options.timeout,
    )
    notebook = manifest["notebook"]
    notebook_id = str(notebook["id"])
    lease = read_json(wrapper.lease_path)
    source_path = manifest.get("disposable_targets", {}).get("source_notebook_path")
    if notebook_id != str(lease.get("notebook_id")):
        raise RestoreFailure("Manifest Notebook ID does not match the lifecycle lease.")
    if source_path and Path(str(source_path)).resolve() != Path(
        str(lease.get("expected_local_path", ""))
    ).resolve():
        raise RestoreFailure("Manifest Notebook path does not match the lifecycle lease.")
    lifecycle_path = options.run_dir / "lifecycle.json"
    lifecycle: dict[str, Any] = {
        "started_at": utc_now(),
        "mode": "keep" if args.keep_notebook else "close",
        "source_notebook_id": notebook_id,
        "closed": False,
        "preserved_paths": _preserved_notebook_paths(options.run_dir, manifest),
        "filesystem_deleted": False,
        "status": "running",
    }
    write_json(lifecycle_path, lifecycle)
    if args.keep_notebook:
        current = wrapper.get_exact_notebook(lease)
        preserved = {
            "closed": False,
            "source_notebook_id": str(current["id"]),
            "status": "preserved_open",
            "filesystem_deleted": False,
        }
        lifecycle.update(
            status="preserved_open",
            preserve_result=preserved,
            completed_at=utc_now(),
        )
        write_json(lifecycle_path, lifecycle)
        return lifecycle

    closed = wrapper.close_exact_notebook()
    if closed.get("closed") is not True:
        raise RestoreFailure("Source Notebook close did not return closed=true.")
    lifecycle["closed"] = True
    lifecycle["close_before"] = closed.get("close_before")
    lifecycle["close_result"] = closed
    write_json(lifecycle_path, lifecycle)

    lifecycle.update(status="closed_preserved", completed_at=utc_now())
    write_json(lifecycle_path, lifecycle)
    return lifecycle


async def run_validate(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    _validate_notebook_name(args.notebook_name)
    if args.scenario == "reorder" and args.page_level < 1:
        raise RunnerFailure("--page-level must be at least 1.")
    if options.dry_run:
        return isolated_dry_run(args, options)

    _assert_fresh_run_dir(options.run_dir)
    state = _initial_state(args, options)
    state_path = options.run_dir / "run-state.json"
    write_json(state_path, state)
    scenario = SCENARIO_REGISTRY.get(args.scenario)
    spec = scenario.spec
    metrics_path = options.run_dir / "run-metrics.json"
    metrics: dict[str, Any] = {
        "schema_version": 1,
        "scenario": args.scenario,
        "architecture": "scenario-scoped-single-mcp",
        "legacy_expected_mcp_process_starts": 2 if args.scenario == "create" else 3,
        "mcp_process_start_attempts": 0,
        "observed_mcp_process_starts": 0,
        "observed_bridge_calls": {
            "scenario_mcp": 0,
            "lifecycle_wrapper": 0,
            "total": 0,
        },
        "observed_mcp_tool_calls": 0,
        "phases_seconds": {},
        "started_at": utc_now(),
    }
    total_started = time.perf_counter()
    write_json(metrics_path, metrics)

    wrapper = NotebookLifecycleWrapper(options.run_dir, timeout_seconds=options.timeout)
    phase_started = time.perf_counter()
    notebook, lease = wrapper.create_fresh_notebook(args.notebook_name)
    metrics["phases_seconds"]["lifecycle_create"] = round(
        time.perf_counter() - phase_started, 6
    )
    state["completed_steps"].append(
        {
            "step": "create-source-notebook",
            "notebook_id": lease["notebook_id"],
            "lease": str(wrapper.lease_path.resolve()),
        }
    )
    state["current_step"] = args.scenario
    write_json(state_path, state)
    _refresh_call_metrics(metrics, options.run_dir)
    write_json(metrics_path, metrics)

    phase_started = time.perf_counter()
    metrics["mcp_process_start_attempts"] = 1
    write_json(metrics_path, metrics)
    client_handle = MCPStdioClient(
        policy=spec.policy,
        allowed_tools=set(spec.tool_allowlist),
        run_dir=options.run_dir / "scenario-mcp",
        timeout_seconds=options.timeout,
    )
    entered_client = False
    try:
        async with client_handle as client:
            entered_client = True
            metrics["observed_mcp_process_starts"] = 1
            write_json(metrics_path, metrics)
            manifest, fixture_result = await prepare_scenario_fixture(
                args,
                options,
                client,
                notebook,
                str(lease["expected_local_path"]),
                spec,
            )
            state["completed_steps"].append(
                {"step": "prepare-fixture", "result": fixture_result}
            )
            write_json(state_path, state)
            scenario.prepare_arguments(args, manifest)
            scenario_result = await scenario.execute(
                args,
                options,
                manifest,
                client=client,
                fixture_result=fixture_result,
            )
    finally:
        metrics["observed_mcp_process_starts"] = int(
            entered_client or getattr(client_handle, "process_started", False)
        )
        _refresh_call_metrics(metrics, options.run_dir)
        metrics["phases_seconds"]["scenario_process"] = round(
            time.perf_counter() - phase_started, 6
        )
        metrics["phases_seconds"]["total_at_process_exit"] = round(
            time.perf_counter() - total_started, 6
        )
        metrics["process_exited_at"] = utc_now()
        write_json(metrics_path, metrics)
    state["completed_steps"].append({"step": args.scenario, "result": scenario_result})
    write_json(metrics_path, metrics)
    state["current_step"] = "report"
    write_json(state_path, state)

    phase_started = time.perf_counter()
    report_path = render_report(options.run_dir)
    metrics["phases_seconds"]["report"] = round(time.perf_counter() - phase_started, 6)
    state["completed_steps"].append({"step": "report", "path": str(report_path.resolve())})
    state["current_step"] = (
        "preserve-source-notebook" if args.keep_notebook else "close-source-notebook"
    )
    state["finalization_started"] = True
    write_json(state_path, state)
    phase_started = time.perf_counter()
    lifecycle = await finalize_notebook(args, options, manifest, wrapper=wrapper)
    metrics["phases_seconds"]["lifecycle_finalize"] = round(
        time.perf_counter() - phase_started, 6
    )
    metrics["phases_seconds"]["total"] = round(time.perf_counter() - total_started, 6)
    _refresh_call_metrics(metrics, options.run_dir)
    metrics["completed_at"] = utc_now()
    write_json(metrics_path, metrics)

    state.update(
        status="passed",
        current_step=None,
        lifecycle_result=lifecycle,
        completed_at=utc_now(),
    )
    write_json(state_path, state)
    result = {
        "command": args.scenario,
        "scenario": args.scenario,
        "status": "passed",
        "human_only": True,
        "agent_execution_prohibited": True,
        "notebook_name": args.notebook_name,
        "notebook_id": manifest["notebook"]["id"],
        "run_dir": str(options.run_dir.resolve()),
        "scenario_result": scenario_result,
        "lifecycle": lifecycle,
        "metrics": metrics,
        "filesystem_deleted": False,
    }
    result["ordered_steps"] = ["create-source-notebook", args.scenario]
    result["ordered_steps"].extend(
        [
            "report",
            "preserve-source-notebook" if args.keep_notebook else "close-source-notebook",
        ]
    )
    write_json(options.run_dir / "run-result.json", result)
    render_report(options.run_dir)
    return result


def _record_run_failure(args: argparse.Namespace, message: str, exit_code: int) -> None:
    run_dir = Path(args.run_dir)
    state_path = run_dir / "run-state.json"
    if not state_path.exists():
        return
    state = read_json(state_path)
    lifecycle_path = run_dir / "lifecycle.json"
    lifecycle = read_json(lifecycle_path) if lifecycle_path.exists() else None
    failure_status = (
        "finalization_failed"
        if state.get("finalization_started")
        else "failed_preserved_open"
    )
    failed_step = state.get("current_step", "preflight")
    failure = {
        "command": args.scenario,
        "scenario": args.scenario,
        "status": failure_status,
        "exit_code": exit_code,
        "error": message,
        "failed_step": failed_step,
        "completed_steps": state.get("completed_steps", []),
        "finalization_attempted": bool(state.get("finalization_started")),
        "lifecycle_result": lifecycle,
        "notebook_name": getattr(args, "notebook_name", None),
        "filesystem_deleted": False,
        "failed_at": utc_now(),
        "remaining_state": (
            "Inspect lifecycle.json; all local Notebook paths remain preserved."
            if state.get("finalization_started")
            else "The fresh Notebook remains open and all evidence is preserved for inspection."
        ),
    }
    write_json(run_dir / "run-failure.json", failure)
    state.update(
        status=failure_status,
        failed_step=failed_step,
        error=message,
        exit_code=exit_code,
        failed_at=failure["failed_at"],
    )
    write_json(state_path, state)


def record_failure(args: argparse.Namespace, message: str, exit_code: int) -> None:
    """Persist run-level and scenario-level failure handoffs."""

    try:
        if (
            getattr(args, "command", None) not in PUBLIC_SCENARIOS
            or not getattr(args, "run_dir", None)
        ):
            return
        _record_run_failure(args, message, exit_code)
        run_dir = Path(args.run_dir)
        state_path = run_dir / "run-state.json"
        if state_path.exists() and read_json(state_path).get("current_step") != args.scenario:
            if (run_dir / "manifest.json").exists():
                render_report(run_dir)
            return
        if not (run_dir / "manifest.json").exists():
            return
        out = scenario_dir(run_dir, args.scenario)
        completed_artifacts = [
            name
            for name in ("before.json", "plan.json", "copy-result.json", "after.json", "restored.json")
            if (out / name).exists()
        ]
        mutation_result = (
            read_json(out / "copy-result.json")
            if "copy-result.json" in completed_artifacts
            else {}
        )
        created_ids = mutation_result.get("created_ids", [])
        needs_manual_cleanup = bool(created_ids) or mutation_result.get("outcome") in {
            "copy_only",
            "copy_unverified",
            "source_partially_recycled",
            "source_recycle_unverified",
            "source_delete_failed",
        }
        manifest = load_manifest(run_dir)
        target_keys = {
            "rename": getattr(args, "target", "move_source"),
            "reorder": "sibling_page",
            "move": "move_source",
            "copy-page": "parent_page",
            "copy-section": "move_source",
            "copy-section-group": "group_a",
            "copy-notebook": None,
            "reconstructive-move-page": "disposable_page",
        }
        if args.scenario == "delete":
            target_id = getattr(args, "delete_target_id", "")
        else:
            target_key = target_keys[args.scenario]
            target_id = (
                manifest.get("notebook", {}).get("id", "")
                if target_key is None
                else manifest.get("structure", {}).get(target_key, {}).get("id", "")
            )
        notebook_id = manifest.get("notebook", {}).get("id", "")
        last_step = "preflight"
        if "before.json" in completed_artifacts:
            last_step = "capture_before"
        if "copy-result.json" in completed_artifacts:
            last_step = "execute_mutation"
        if "after.json" in completed_artifacts:
            last_step = "capture_after"
        if "restored.json" in completed_artifacts:
            last_step = "capture_restored"
        failure = {
            "scenario": args.scenario,
            "status": (
                "needs_manual_cleanup"
                if needs_manual_cleanup
                else "needs_manual_restore" if exit_code == EXIT_RESTORE else "failed"
            ),
            "exit_code": exit_code,
            "error": message,
            "target_id": target_id,
            "last_successful_step": last_step,
            "completed_artifacts": completed_artifacts,
            "outcome": mutation_result.get("outcome"),
            "created_ids": created_ids,
            "id_map": (
                mutation_result.get("copy_report", {}).get("id_map")
                or mutation_result.get("id_map", {})
            ),
            "restored": True if "restored.json" in completed_artifacts and exit_code != EXIT_RESTORE else "unknown",
            "failed_at": utc_now(),
            "suggested_next_step": (
                f"Inspect Notebook ID {notebook_id!r} in OneNote and review this run's artifacts."
            ),
        }
        write_json(out / "failure.json", failure)
        render_report(run_dir)
    except Exception:
        pass


__all__ = [
    "PUBLIC_SCENARIOS",
    "SCENARIO_POLICIES",
    "finalize_notebook",
    "isolated_dry_run",
    "record_failure",
    "run_validate",
]
