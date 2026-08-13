"""Shared lifecycle orchestration for self-contained scenario suites."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping

from ...mcp_stdio_client import (
    COPY_BUDGET_ENV,
    MCPStdioClient,
)
from ...lifecycle import NotebookLifecycleWrapper
from ...runtime import (
    EXIT_RESTORE,
    PathBudgetFailure,
    RestoreFailure,
    RunnerFailure,
    RuntimeOptions,
)
from ...test_utils import (
    load_manifest,
    read_json,
    resolve_manifest_item,
    scenario_dir,
    utc_now,
    write_json,
)
from .fixture_runtime import (
    bundle_cache_artifacts,
    prepare_fixture_bundle,
    prepare_materialized_fixture_bundle,
)
from .fixture_cache import (
    CACHE_SCHEMA_VERSION,
    MANAGED_MARKER,
    BundleCacheStore,
    CacheHit,
    MaterializedBundle,
    legacy_empty_cache_activation_evidence,
)
from ..fixture_recipes.recipe_base import BuildMode
from .dry_run import build_isolated_dry_run_plan
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


def _keep_source_notebook(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "keep_notebook", False)
        or getattr(args, "keep_worksite", False)
    )


def _validate_notebook_name(name: str) -> None:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned or cleaned in {".", ".."} or cleaned != name:
        raise RunnerFailure(
            "--notebook-name must be a non-empty Windows-safe leaf name without normalization."
        )


def _cached_working_names(args: argparse.Namespace, scenario) -> dict[str, str] | None:
    names = getattr(args, "cached_notebook_names", None)
    if not isinstance(names, dict):
        return None
    roles = {
        role.role for role in scenario.fixture_recipe.cache_identity.notebook_roles
    }
    if set(names) != roles:
        raise RunnerFailure("Canonical cached Notebook names do not cover every Recipe role.")
    return {role: str(name) for role, name in names.items()}


def _materialize_with_budget_context(
    cache_store: BundleCacheStore,
    hit: CacheHit,
    run_dir: Path,
    *,
    working_names: Mapping[str, str] | None,
    cache_entry_published: bool,
) -> MaterializedBundle:
    try:
        return cache_store.materialize(
            hit,
            run_dir,
            working_names=working_names,
        )
    except PathBudgetFailure as exc:
        exc.cache_entry_published = cache_entry_published
        raise


def _assert_fresh_run_dir(run_dir: Path) -> None:
    if run_dir.exists() and not run_dir.is_dir():
        raise RunnerFailure("--run-dir must identify a directory, not a file.")
    if run_dir.exists() and any(run_dir.iterdir()):
        raise RunnerFailure(
            "--run-dir must be absent or empty so evidence and disposable targets cannot be mixed."
        )


def _assert_no_legacy_validation_payload(
    run_dir: Path,
    cache_root: Path | None,
) -> None:
    run_parent = run_dir.resolve().parent
    cache_parent = cache_root.resolve().parent if cache_root is not None else None
    validation_root = (
        cache_parent
        if cache_parent is not None and cache_parent.name == ".local-validation"
        else run_parent
    )
    if validation_root.name != ".local-validation" or not validation_root.exists():
        return
    for candidate in validation_root.glob("run-*"):
        if candidate.resolve() == run_dir.resolve() or not candidate.is_dir():
            continue
        state_path = candidate / "run-state.json"
        if not state_path.is_file():
            raise RunnerFailure(
                "Legacy or unknown run metadata remains. Return to the pre-upgrade version "
                "and complete its human-gated clear all workflow."
            )
        state = read_json(state_path)
        if (
            state.get("schema_version") != 2
            or state.get("human_only") is not True
            or state.get("agent_execution_prohibited") is not True
        ):
            raise RunnerFailure(
                "Legacy or unowned run metadata remains. Return to the pre-upgrade version and "
                "complete its human-gated clear all workflow."
            )
    selected_cache = (cache_root or (validation_root / "fixture-cache")).resolve()
    if not selected_cache.exists():
        return
    marker_path = selected_cache / MANAGED_MARKER
    if not marker_path.is_file() or read_json(marker_path).get(
        "schema_version"
    ) != CACHE_SCHEMA_VERSION:
        if legacy_empty_cache_activation_evidence(selected_cache) is not None:
            return
        raise RunnerFailure(
            "Legacy fixture cache metadata remains. Return to the pre-upgrade version "
            "and complete its human-gated clear all workflow."
        )


def isolated_dry_run(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    scenario = SCENARIO_REGISTRY.get(args.scenario)
    spec = scenario.runtime_spec(args)
    return build_isolated_dry_run_plan(
        args,
        options,
        spec=spec,
        capability_assessment=scenario.capability_assessment,
        copy_budget={
            field: value for field, (_env_name, value) in COPY_BUDGET_ENV.items()
        },
        worksite_action=scenario.worksite_dry_run_action,
        recipe=scenario.fixture_recipe,
    )


def _initial_state(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    scenario = SCENARIO_REGISTRY.get(args.scenario)
    multi_role = len(scenario.fixture_recipe.cache_identity.notebook_roles) > 1
    state = {
        "schema_version": 2,
        "command": args.scenario,
        "scenario": args.scenario,
        "status": "running",
        "human_only": True,
        "agent_execution_prohibited": True,
        "started_at": utc_now(),
        "notebook_name": args.notebook_name,
        "run_dir": str(options.run_dir.resolve()),
        "lifecycle": "keep" if _keep_source_notebook(args) else "close",
        "keep_worksite": bool(getattr(args, "keep_worksite", False)),
        "completed_steps": [],
        "current_step": "create-notebook-bundle" if multi_role else "create-source-notebook",
        "finalization_started": False,
    }
    run_identity = getattr(args, "run_identity", None)
    if hasattr(run_identity, "as_dict"):
        state["run_identity"] = run_identity.as_dict()
    fresh_names = getattr(args, "fresh_notebook_names", None)
    cached_names = getattr(args, "cached_notebook_names", None)
    if isinstance(fresh_names, dict) and isinstance(cached_names, dict):
        state["notebook_names"] = {
            "fresh": dict(fresh_names),
            "cached": dict(cached_names),
        }
    if scenario.capability_assessment is not None:
        state["capability_assessment"] = dict(scenario.capability_assessment)
    return state


def _preserved_notebook_paths(run_dir: Path, manifest: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    disposable = manifest.get("disposable_targets", {})
    if isinstance(disposable, dict):
        paths.extend(
            str(value)
            for key, value in sorted(disposable.items())
            if key.endswith("_notebook_path") and value
        )
    copy_dir = scenario_dir(run_dir, "copy-notebook")
    for evidence_name in ("worksite.json", "restored.json"):
        copy_path = copy_dir / evidence_name
        if not copy_path.exists():
            continue
        target_path = read_json(copy_path).get("target_path")
        if target_path:
            paths.append(str(target_path))
        break
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


def _record_materialized_failure(
    cache_store: BundleCacheStore,
    scenario,
    hit: CacheHit,
    options: RuntimeOptions,
    exc: Exception,
    *,
    phase: str,
    quarantine: bool = True,
) -> None:
    reason = f"{phase} failed: {type(exc).__name__}: {exc}"
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "phase": phase,
        "fingerprint": hit.fingerprint,
        "template_instance_id": hit.template_instance_id,
        "reason": reason,
        "cache_entry_matchable": not quarantine,
        "template_deleted": False,
        "failed_at": utc_now(),
    }
    if quarantine:
        try:
            with cache_store.lock(hit.fingerprint, run_id=options.run_dir.name):
                evidence["quarantine"] = cache_store.quarantine_exact(
                    scenario.fixture_recipe,
                    hit.template_instance_id,
                    reason=reason,
                    run_id=options.run_dir.name,
                )
        except Exception as quarantine_exc:
            evidence["cache_entry_matchable"] = None
            evidence["quarantine_error"] = (
                f"{type(quarantine_exc).__name__}: {quarantine_exc}"
            )
    else:
        evidence["quarantine"] = None
        evidence["retryable_after_working_notebook_close"] = True
    write_json(options.run_dir / "cache-live-validation-failure.json", evidence)


def _resolve_exact_cache_entry(
    cache_store: BundleCacheStore,
    recipe,
    instance_id: str,
    *,
    run_id: str,
    open_state_probe: Callable[[Mapping[str, Any]], bool],
    allow_open_failure_recovery: bool,
) -> tuple[CacheHit | None, str | None, bool]:
    """Resolve one exact entry without collapsing an invalid entry into a miss."""

    state = cache_store.exact_entry_state(recipe, instance_id)
    if state is None:
        return None, None, False
    if state == "cleanup_failed":
        raise RunnerFailure(
            "Exact fixture cache cleanup previously failed; rebuild remains blocked."
        )
    if state == "invalid":
        if allow_open_failure_recovery:
            recovered = cache_store.recover_retryable_open_failure(
                recipe,
                instance_id,
                run_id=run_id,
            )
            if recovered is not None:
                return recovered, "recovered_retryable_open_failure", False
        cache_store.invalidate_exact(
            recipe,
            instance_id,
            reason="selected exact cache entry is invalid and requires rebuild",
            open_state_probe=open_state_probe,
        )
        return None, "invalidated_rebuild", True
    try:
        hit = cache_store.lookup(recipe, instance_id)
    except Exception as exc:
        cache_store.invalidate_exact(
            recipe,
            instance_id,
            reason=f"lookup validation failed: {type(exc).__name__}",
            open_state_probe=open_state_probe,
        )
        return None, "invalidated_rebuild", True
    if hit is None:
        raise RunnerFailure(
            "Matchable exact fixture cache entry disappeared during locked lookup."
        )
    return hit, None, False


def _record_failed_materialized_open(
    cache_store: BundleCacheStore,
    wrapper: NotebookLifecycleWrapper,
    materialized: MaterializedBundle,
    options: RuntimeOptions,
) -> None:
    """Record the run-local live identity when child hierarchy activation fails."""

    if not wrapper.lease_path.exists():
        return
    lifecycle_lease = read_json(wrapper.lease_path)
    notebook_id = str(lifecycle_lease.get("notebook_id", ""))
    actual_path = Path(str(lifecycle_lease.get("actual_local_path", ""))).resolve()
    if not notebook_id or actual_path != materialized.working_paths["source"]:
        return
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "notebook_id": notebook_id,
        "actual_local_path": str(actual_path),
        "hierarchy_open_status": lifecycle_lease.get("hierarchy_open_status"),
        "bound_at": utc_now(),
    }
    try:
        cache_store.record_opened_working_role(
            materialized,
            role="source",
            notebook_id=notebook_id,
            actual_path=actual_path,
        )
        evidence["bound"] = True
    except Exception as bind_exc:
        evidence["bound"] = False
        evidence["binding_error"] = f"{type(bind_exc).__name__}: {bind_exc}"
    write_json(options.run_dir / "cache-failed-open-live-id.json", evidence)


async def finalize_notebook(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    wrapper: NotebookLifecycleWrapper | None = None,
    role: str = "source",
) -> dict[str, Any]:
    wrapper = wrapper or NotebookLifecycleWrapper(
        options.run_dir,
        timeout_seconds=options.timeout,
        **({} if role == "source" else {"role": role}),
    )
    notebook = manifest.get("notebooks", {}).get(role, manifest["notebook"])
    notebook_id = str(notebook["id"])
    lease = read_json(wrapper.lease_path)
    source_path = manifest.get("disposable_targets", {}).get(f"{role}_notebook_path")
    if notebook_id != str(lease.get("notebook_id")):
        raise RestoreFailure("Manifest Notebook ID does not match the lifecycle lease.")
    if source_path and Path(str(source_path)).resolve() != Path(
        str(lease.get("expected_local_path", ""))
    ).resolve():
        raise RestoreFailure("Manifest Notebook path does not match the lifecycle lease.")
    lifecycle_path = options.run_dir / (
        "lifecycle.json" if role == "source" else f"lifecycle-{role}.json"
    )
    lifecycle: dict[str, Any] = {
        "started_at": utc_now(),
        "mode": "keep" if _keep_source_notebook(args) else "close",
        "source_notebook_id": notebook_id,
        "role": role,
        "closed": False,
        "preserved_paths": _preserved_notebook_paths(options.run_dir, manifest),
        "filesystem_deleted": False,
        "status": "running",
    }
    write_json(lifecycle_path, lifecycle)
    if _keep_source_notebook(args):
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


async def finalize_bundle(
    args: argparse.Namespace,
    options: RuntimeOptions,
    manifest: dict[str, Any],
    *,
    wrappers: Mapping[str, NotebookLifecycleWrapper],
    roles: tuple[str, ...],
) -> dict[str, Any]:
    evidence_path = options.run_dir / "lifecycle-bundle.json"
    role_results: dict[str, dict[str, Any]] = {}
    result: dict[str, Any] = {
        "status": "running",
        "mode": "keep" if _keep_source_notebook(args) else "close",
        "closed": False,
        "roles": role_results,
        "filesystem_deleted": False,
        "started_at": utc_now(),
    }
    write_json(evidence_path, result)
    try:
        for role in roles:
            role_results[role] = await finalize_notebook(
                args,
                options,
                manifest,
                wrapper=wrappers[role],
                role=role,
            )
            write_json(evidence_path, result)
    except Exception as exc:
        result.update(
            status="failed_preserved_bundle",
            error=f"{type(exc).__name__}: {exc}",
            failed_at=utc_now(),
            completed_roles=list(role_results),
        )
        write_json(evidence_path, result)
        raise
    result.update(
        status=(
            "preserved_open"
            if _keep_source_notebook(args)
            else "closed_preserved"
        ),
        closed=all(value.get("closed") is True for value in role_results.values()),
        completed_at=utc_now(),
    )
    write_json(evidence_path, result)
    return result


async def run_validate(args: argparse.Namespace, options: RuntimeOptions) -> dict[str, Any]:
    _validate_notebook_name(args.notebook_name)
    if args.scenario == "reorder-page" and args.page_level < 1:
        raise RunnerFailure("--page-level must be at least 1.")
    if options.dry_run:
        return isolated_dry_run(args, options)
    scenario = SCENARIO_REGISTRY.get(args.scenario)
    if options.use_cache and not getattr(scenario.fixture_recipe, "supports_cache", True):
        raise RunnerFailure(
            "This Scenario is fresh-only because it generates in-memory search probes; "
            "remove --use-cache."
        )
    if (
        options.use_cache
        and getattr(scenario.fixture_recipe, "representation_discovery_only", False)
    ):
        raise RunnerFailure(
            "Representation discovery never reads or publishes fixture cache; "
            "remove --use-cache."
        )
    _assert_fresh_run_dir(options.run_dir)
    _assert_no_legacy_validation_payload(options.run_dir, options.cache_root)
    progress = options.progress
    progress.run_started(args.scenario, options.run_dir)
    state = _initial_state(args, options)
    state_path = options.run_dir / "run-state.json"
    write_json(state_path, state)
    if scenario.fixture_recipe.consumer_scenario and not options.use_cache:
        raise RunnerFailure(
            "Interactive fixture consumers require --use-cache and never build a fresh authored fixture."
        )
    spec = scenario.runtime_spec(args)
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

    roles = tuple(
        role.role for role in scenario.fixture_recipe.cache_identity.notebook_roles
    )
    progress.phase_started("notebook", 1, 5)
    wrappers = _role_wrappers(
        options.run_dir,
        options.timeout,
        roles,
        progress=options.progress,
    )
    wrapper = wrappers["source"]
    cache_store: BundleCacheStore | None = None
    cache_hit: CacheHit | None = None
    materialized: MaterializedBundle | None = None
    cache_decision = "fresh"
    invalidation_performed = False
    interactive_bootstrap = (
        scenario.fixture_recipe.build_mode == BuildMode.HUMAN_BOOTSTRAP_REQUIRED
        and getattr(scenario.fixture_recipe, "bootstrap_scenario_name", None) == scenario.name
    )
    selected_instance_id: str | None = None
    if options.use_cache and not interactive_bootstrap:
        selected_instance_id = scenario.fixture_recipe.select_template_instance_id(args)
    phase_started = time.perf_counter()
    if options.use_cache:
        cache_store = BundleCacheStore(
            options.cache_root or (options.run_dir.parent / "fixture-cache")
        )
        cache_store.initialize()
    if options.use_cache and not interactive_bootstrap:
        recipe = scenario.fixture_recipe
        instance_id = str(selected_instance_id)
        with cache_store.lock(recipe.cache_fingerprint, run_id=options.run_dir.name):
            cache_hit, resolution, resolved_invalidation = _resolve_exact_cache_entry(
                cache_store,
                recipe,
                instance_id,
                run_id=options.run_dir.name,
                open_state_probe=wrapper.any_cache_template_open,
                allow_open_failure_recovery=True,
            )
            if resolution is not None:
                cache_decision = resolution
            invalidation_performed = (
                invalidation_performed or resolved_invalidation
            )
            if cache_hit is not None and recipe.invalidation_probe:
                cache_store.invalidate_exact(
                    recipe,
                    instance_id,
                    reason="fixed cache-invalidation Scenario probe",
                    open_state_probe=wrapper.any_cache_template_open,
                )
                cache_hit = None
                cache_decision = "invalidated_rebuild"
                invalidation_performed = True
            if cache_hit is not None:
                if cache_hit.entry.get("state") == "evidence_only" and not recipe.accepts_evidence_only:
                    raise RunnerFailure(
                        "Selected template instance is evidence_only and cannot enter this Scenario."
                    )
                materialized = _materialize_with_budget_context(
                    cache_store,
                    cache_hit,
                    options.run_dir,
                    working_names=_cached_working_names(args, scenario),
                    cache_entry_published=False,
                )
                if cache_decision != "recovered_retryable_open_failure":
                    cache_decision = "validated_hit"
        if cache_hit is None and recipe.build_mode == BuildMode.HUMAN_BOOTSTRAP_REQUIRED:
            bootstrap = getattr(recipe, "bootstrap_scenario_name", "")
            raise RunnerFailure(
                f"interactive_bootstrap_required: run the named scenario {bootstrap!r}."
            )
    if cache_hit is not None and materialized is not None and cache_store is not None:
        try:
            notebooks, leases = _open_materialized_bundle(
                cache_store,
                materialized,
                wrappers,
                roles,
            )
        except Exception as exc:
            if roles == ("source",):
                _record_failed_materialized_open(
                    cache_store,
                    wrapper,
                    materialized,
                    options,
                )
            _record_materialized_failure(
                cache_store,
                scenario,
                cache_hit,
                options,
                exc,
                phase="materialized-open",
                quarantine=False,
            )
            raise
        notebook, lease = notebooks["source"], leases["source"]
        args.notebook_name = str(notebook.get("name", args.notebook_name))
    else:
        notebooks, leases = _create_fresh_bundle(args, wrappers, roles)
        notebook, lease = notebooks["source"], leases["source"]
    metrics["phases_seconds"]["lifecycle_create"] = round(
        time.perf_counter() - phase_started, 6
    )
    progress.phase_completed(
        "notebook",
        elapsed_seconds=metrics["phases_seconds"]["lifecycle_create"],
    )
    state["completed_steps"].append(
        {
            "step": (
                "create-source-notebook" if len(roles) == 1 else "create-notebook-bundle"
            ),
            "roles": {
                role: {
                    "notebook_id": leases[role]["notebook_id"],
                    "lease": str(wrappers[role].lease_path.resolve()),
                }
                for role in roles
            },
        }
    )
    state["current_step"] = args.scenario
    write_json(state_path, state)
    _refresh_call_metrics(metrics, options.run_dir)
    write_json(metrics_path, metrics)

    progress.phase_started("fixture", 2, 5)
    fixture_progress_started = time.perf_counter()
    phase_started = time.perf_counter()
    metrics["mcp_process_start_attempts"] = 1
    write_json(metrics_path, metrics)
    client_options = dict(
        policy=spec.policy,
        allowed_tools=set(spec.tool_allowlist),
        run_dir=options.run_dir / "scenario-mcp",
        timeout_seconds=options.timeout,
    )
    if spec.search_budget:
        client_options["search_budget"] = dict(spec.search_budget)
    client_handle = MCPStdioClient(**client_options)
    client_handle.progress = progress
    entered_client = False
    try:
        async with client_handle as client:
            entered_client = True
            metrics["observed_mcp_process_starts"] = 1
            write_json(metrics_path, metrics)
            progress.server_ready(
                enabled_policies=sorted(
                    name for name, enabled in spec.policy.as_dict().items() if enabled
                ),
                tool_count=len(spec.tool_allowlist),
            )
            if cache_hit is not None and materialized is not None:
                try:
                    manifest, fixture_result = await prepare_materialized_fixture_bundle(
                        scenario,
                        args,
                        options,
                        client,
                        notebooks,
                        {
                            role: str(leases[role]["expected_local_path"])
                            for role in roles
                        },
                        spec,
                        cache_hit,
                        materialized,
                    )
                except Exception as exc:
                    _record_materialized_failure(
                        cache_store,
                        scenario,
                        cache_hit,
                        options,
                        exc,
                        phase="materialized-live-validation",
                    )
                    raise
            else:
                manifest, fixture_result = await prepare_fixture_bundle(
                    scenario,
                    args,
                    options,
                    client,
                    notebooks,
                    {
                        role: str(leases[role]["expected_local_path"])
                        for role in roles
                    },
                    spec,
                )
                if options.use_cache and scenario.fixture_recipe.build_mode == BuildMode.PROGRAMMATIC:
                    if cache_store is None:
                        raise RunnerFailure("Fixture cache runtime was not initialized.")
                    _close_bundle(wrappers, roles)
                    recipe = scenario.fixture_recipe
                    instance_id = recipe.default_template_instance_id
                    artifacts = bundle_cache_artifacts(
                        options.run_dir, roles, manifest, fixture_result
                    )
                    source_paths = {
                        role: Path(str(leases[role]["expected_local_path"]))
                        for role in roles
                    }
                    with cache_store.lock(recipe.cache_fingerprint, run_id=options.run_dir.name):
                        cache_hit, resolution, resolved_invalidation = (
                            _resolve_exact_cache_entry(
                                cache_store,
                                recipe,
                                instance_id,
                                run_id=options.run_dir.name,
                                open_state_probe=wrapper.any_cache_template_open,
                                allow_open_failure_recovery=True,
                            )
                        )
                        if resolution is not None:
                            cache_decision = resolution
                        invalidation_performed = (
                            invalidation_performed or resolved_invalidation
                        )
                        if cache_hit is None:
                            cache_hit = cache_store.publish(
                                recipe,
                                instance_id,
                                source_paths=source_paths,
                                source_notebooks=notebooks,
                                closed_roles=set(roles),
                                validation=manifest["fixture_validation"],
                                artifacts=artifacts,
                            )
                        if recipe.invalidation_probe and not invalidation_performed:
                            cache_store.invalidate_exact(
                                recipe,
                                instance_id,
                                reason="fixed cache-invalidation Scenario cold-entry probe",
                                open_state_probe=wrapper.any_cache_template_open,
                            )
                            invalidation_performed = True
                            cache_decision = "invalidated_rebuild"
                            cache_hit = cache_store.publish(
                                recipe,
                                instance_id,
                                source_paths=source_paths,
                                source_notebooks=notebooks,
                                closed_roles=set(roles),
                                validation=manifest["fixture_validation"],
                                artifacts=artifacts,
                            )
                        materialized = _materialize_with_budget_context(
                            cache_store,
                            cache_hit,
                            options.run_dir,
                            working_names=_cached_working_names(args, scenario),
                            cache_entry_published=True,
                        )
                    try:
                        notebooks, leases = _open_materialized_bundle(
                            cache_store,
                            materialized,
                            wrappers,
                            roles,
                        )
                    except Exception as exc:
                        if roles == ("source",):
                            _record_failed_materialized_open(
                                cache_store,
                                wrapper,
                                materialized,
                                options,
                            )
                        _record_materialized_failure(
                            cache_store,
                            scenario,
                            cache_hit,
                            options,
                            exc,
                            phase="cold-materialized-open",
                            quarantine=False,
                        )
                        raise
                    notebook, lease = notebooks["source"], leases["source"]
                    args.notebook_name = str(notebook.get("name", args.notebook_name))
                    try:
                        manifest, fixture_result = await prepare_materialized_fixture_bundle(
                            scenario,
                            args,
                            options,
                            client,
                            notebooks,
                            {
                                role: str(leases[role]["expected_local_path"])
                                for role in roles
                            },
                            spec,
                            cache_hit,
                            materialized,
                        )
                    except Exception as exc:
                        _record_materialized_failure(
                            cache_store,
                            scenario,
                            cache_hit,
                            options,
                            exc,
                            phase="cold-materialized-live-validation",
                        )
                        raise
                    cache_decision = (
                        "invalidated_rebuild"
                        if cache_decision == "invalidated_rebuild"
                        else "cold_build"
                    )
                    manifest["fixture_cache"]["decision"] = cache_decision
                    write_json(options.run_dir / "manifest.json", manifest)
            state["completed_steps"].append(
                {"step": "prepare-fixture", "result": fixture_result}
            )
            write_json(state_path, state)
            progress.cache_decision(cache_decision, len(roles))
            progress.phase_completed(
                "fixture",
                elapsed_seconds=time.perf_counter() - fixture_progress_started,
            )
            progress.phase_started("scenario", 3, 5)
            scenario.prepare_arguments(args, manifest)
            scenario_result = await scenario.execute(
                args,
                options,
                manifest,
                client=client,
                fixture_result=fixture_result,
            )
            if interactive_bootstrap and scenario_result.get("interactive_bootstrap") is True:
                if bool(getattr(args, "keep_worksite", False)):
                    scenario_result["template_published"] = False
                    scenario_result["template_not_published_reason"] = "keep_worksite"
                    cache_decision = "template_not_published"
                else:
                    if cache_store is None:
                        cache_store = BundleCacheStore(
                            options.cache_root
                            or (options.run_dir.parent / "fixture-cache")
                        )
                        cache_store.initialize()
                    closed = wrapper.close_exact_notebook()
                    if closed.get("closed") is not True:
                        raise RestoreFailure(
                            "Interactive source did not close; template publication is blocked."
                        )
                    recipe = scenario.fixture_recipe
                    instance_id = str(scenario_result["template_instance_id"])
                    final_manifest = read_json(options.run_dir / "manifest.json")
                    final_snapshot = read_json(options.run_dir / "fixture-snapshot.json")
                    with cache_store.lock(recipe.cache_fingerprint, run_id=options.run_dir.name):
                        existing, _resolution, _resolved_invalidation = (
                            _resolve_exact_cache_entry(
                                cache_store,
                                recipe,
                                instance_id,
                                run_id=options.run_dir.name,
                                open_state_probe=wrapper.any_cache_template_open,
                                allow_open_failure_recovery=True,
                            )
                        )
                        if existing is not None:
                            cache_store.invalidate_exact(
                                recipe,
                                instance_id,
                                reason="explicit named interactive re-bootstrap",
                                open_state_probe=wrapper.any_cache_template_open,
                            )
                        cache_hit = cache_store.publish(
                            recipe,
                            instance_id,
                            source_paths={"source": Path(str(lease["expected_local_path"]))},
                            source_notebooks={"source": notebook},
                            closed_roles={"source"},
                            validation=final_manifest["fixture_validation"],
                            artifacts={
                                "source": {
                                    "manifest": final_manifest,
                                    "fixture_result": fixture_result,
                                    "snapshot": final_snapshot,
                                }
                            },
                            projection_digest=str(
                                scenario_result.get("template_instance", {}).get(
                                    "projection_digest", ""
                                )
                                or ""
                            )
                            or None,
                            state=str(scenario_result.get("template_state", "ready")),
                        )
                        materialized = _materialize_with_budget_context(
                            cache_store,
                            cache_hit,
                            options.run_dir,
                            working_names=_cached_working_names(args, scenario),
                            cache_entry_published=True,
                        )
                    try:
                        notebooks, leases = _open_materialized_bundle(
                            cache_store,
                            materialized,
                            wrappers,
                            roles,
                        )
                    except Exception as exc:
                        if roles == ("source",):
                            _record_failed_materialized_open(
                                cache_store,
                                wrapper,
                                materialized,
                                options,
                            )
                        _record_materialized_failure(
                            cache_store,
                            scenario,
                            cache_hit,
                            options,
                            exc,
                            phase="bootstrap-materialized-open",
                            quarantine=False,
                        )
                        raise
                    notebook, lease = notebooks["source"], leases["source"]
                    args.notebook_name = str(notebook.get("name", args.notebook_name))
                    try:
                        manifest, fixture_result = await prepare_materialized_fixture_bundle(
                            scenario,
                            args,
                            options,
                            client,
                            notebooks,
                            {
                                role: str(leases[role]["expected_local_path"])
                                for role in roles
                            },
                            spec,
                            cache_hit,
                            materialized,
                        )
                    except Exception as exc:
                        _record_materialized_failure(
                            cache_store,
                            scenario,
                            cache_hit,
                            options,
                            exc,
                            phase="bootstrap-materialized-live-validation",
                        )
                        raise
                    cache_decision = "bootstrap_published"
                    scenario_result["template_published"] = True
                    scenario_result["post_publish_materialization_validated"] = True
            progress.phase_completed("scenario")
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
        if cache_store is not None and materialized is not None:
            cache_store.verify_templates_unchanged(materialized)
    state["completed_steps"].append({"step": args.scenario, "result": scenario_result})
    write_json(metrics_path, metrics)
    state["current_step"] = "report"
    write_json(state_path, state)

    progress.phase_started("report", 4, 5)
    phase_started = time.perf_counter()
    report_path = render_report(options.run_dir)
    metrics["phases_seconds"]["report"] = round(time.perf_counter() - phase_started, 6)
    progress.phase_completed(
        "report",
        elapsed_seconds=metrics["phases_seconds"]["report"],
    )
    state["completed_steps"].append({"step": "report", "path": str(report_path.resolve())})
    state["current_step"] = (
        ("preserve-notebook-bundle" if len(roles) > 1 else "preserve-source-notebook")
        if _keep_source_notebook(args)
        else ("close-notebook-bundle" if len(roles) > 1 else "close-source-notebook")
    )
    state["finalization_started"] = True
    write_json(state_path, state)
    progress.phase_started("lifecycle", 5, 5)
    phase_started = time.perf_counter()
    lifecycle = await finalize_bundle(
        args,
        options,
        manifest,
        wrappers=wrappers,
        roles=roles,
    )
    metrics["phases_seconds"]["lifecycle_finalize"] = round(
        time.perf_counter() - phase_started, 6
    )
    progress.phase_completed(
        "lifecycle",
        elapsed_seconds=metrics["phases_seconds"]["lifecycle_finalize"],
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
        "notebooks": dict(manifest.get("notebooks", {"source": manifest["notebook"]})),
        "run_dir": str(options.run_dir.resolve()),
        "scenario_result": scenario_result,
        "lifecycle": lifecycle,
        "metrics": metrics,
        "filesystem_deleted": False,
        "cache": {
            "cache_mode": "use_cache" if options.use_cache else "fresh",
            "decision": cache_decision,
            "fingerprint": scenario.fixture_recipe.cache_fingerprint,
            "template_instance_id": (
                cache_hit.template_instance_id
                if cache_hit is not None
                else scenario.fixture_recipe.default_template_instance_id
            ),
            "opened_template": False,
        },
    }
    run_identity = getattr(args, "run_identity", None)
    if hasattr(run_identity, "as_dict"):
        result["run_identity"] = run_identity.as_dict()
    fresh_names = getattr(args, "fresh_notebook_names", None)
    cached_names = getattr(args, "cached_notebook_names", None)
    if isinstance(fresh_names, dict) and isinstance(cached_names, dict):
        result["notebook_names"] = {
            "fresh": dict(fresh_names),
            "cached": dict(cached_names),
        }
    result["ordered_steps"] = [
        "create-source-notebook" if len(roles) == 1 else "create-notebook-bundle",
        args.scenario,
    ]
    result["ordered_steps"].extend(
        [
            "report",
            ("preserve-notebook-bundle" if len(roles) > 1 else "preserve-source-notebook")
            if _keep_source_notebook(args)
            else ("close-notebook-bundle" if len(roles) > 1 else "close-source-notebook"),
        ]
    )
    write_json(options.run_dir / "run-result.json", result)
    render_report(options.run_dir)
    return result


def _record_run_failure(
    args: argparse.Namespace,
    error: str | RunnerFailure,
    exit_code: int,
) -> bool:
    run_dir = Path(args.run_dir)
    state_path = run_dir / "run-state.json"
    if not state_path.exists():
        return False
    message = str(error)
    state = read_json(state_path)
    if isinstance(error, PathBudgetFailure):
        error.filesystem_changes_started = True
        error.onenote_opened = any(run_dir.glob("lifecycle-lease*.json"))
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
    if isinstance(error, PathBudgetFailure):
        structured_error = error.as_error_dict()
        structured_error["failure_evidence_written"] = True
        failure["structured_error"] = structured_error
    if isinstance(state.get("run_identity"), dict):
        failure["run_identity"] = dict(state["run_identity"])
    if isinstance(state.get("notebook_names"), dict):
        failure["notebook_names"] = dict(state["notebook_names"])
    write_json(run_dir / "run-failure.json", failure)
    if isinstance(error, PathBudgetFailure):
        error.with_failure_evidence(True)
    state.update(
        status=failure_status,
        failed_step=failed_step,
        error=message,
        exit_code=exit_code,
        failed_at=failure["failed_at"],
    )
    write_json(state_path, state)
    return True


def record_failure(
    args: argparse.Namespace,
    error: str | RunnerFailure,
    exit_code: int,
) -> None:
    """Persist run-level and scenario-level failure handoffs."""

    try:
        if (
            getattr(args, "command", None) not in PUBLIC_SCENARIOS
            or not getattr(args, "run_dir", None)
        ):
            return
        _record_run_failure(args, error, exit_code)
        message = str(error)
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
            "source_partially_removed",
            "source_partially_recycled",
            "source_recycle_unverified",
            "source_delete_failed",
        }
        manifest = load_manifest(run_dir)
        target_keys = {
            "rename": getattr(args, "target", "content_section"),
            "reorder-page": "sibling_page",
            "reorder-section": "root_section_c",
            "reorder-section-group": "root_group_c",
            "reparent-section": "notebook_to_group_section",
            "reparent-page": "reparent_page",
            "reparent-section-group": "notebook_to_group_target",
            "copy-page": "parent_page",
            "copy-section": "source_section",
            "copy-section-group": "group_a",
            "copy-notebook": None,
            "move-page": "disposable_page",
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


def _role_wrappers(
    run_dir: Path,
    timeout_seconds: int,
    roles: tuple[str, ...],
    *,
    progress,
) -> dict[str, NotebookLifecycleWrapper]:
    wrappers: dict[str, NotebookLifecycleWrapper] = {}
    for role in roles:
        wrapper = NotebookLifecycleWrapper(
            run_dir,
            timeout_seconds=timeout_seconds,
            **({} if role == "source" else {"role": role}),
        )
        wrapper.progress = progress
        wrappers[role] = wrapper
    return wrappers


def _create_fresh_bundle(
    args: argparse.Namespace,
    wrappers: Mapping[str, NotebookLifecycleWrapper],
    roles: tuple[str, ...],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    names = getattr(args, "fresh_notebook_names", None)
    if not isinstance(names, dict):
        names = {
            role: (
                str(args.notebook_name)
                if role == "source"
                else f"{args.notebook_name}-{role}"
            )
            for role in roles
        }
    if set(names) != set(roles):
        raise RunnerFailure("Canonical fresh Notebook names do not cover every Recipe role.")
    notebooks: dict[str, dict[str, Any]] = {}
    leases: dict[str, dict[str, Any]] = {}
    for role in roles:
        notebooks[role], leases[role] = wrappers[role].create_fresh_notebook(
            str(names[role])
        )
    return notebooks, leases


def _open_materialized_bundle(
    cache_store: BundleCacheStore,
    materialized: MaterializedBundle,
    wrappers: Mapping[str, NotebookLifecycleWrapper],
    roles: tuple[str, ...],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    notebooks: dict[str, dict[str, Any]] = {}
    leases: dict[str, dict[str, Any]] = {}
    with wrappers["source"].working_notebook_open_lock():
        before_open = wrappers["source"].snapshot_open_notebooks()
        wrappers["source"].assert_no_active_working_conflict(
            notebook_ids=None,
            working_paths=materialized.working_paths,
            open_notebooks=before_open,
        )
        for role in roles:
            kwargs = {} if role == "source" else {"role": role}
            notebooks[role], leases[role] = wrappers[role].open_working_notebook(
                materialized.working_paths[role].name,
                materialized.working_paths[role],
                template_paths=tuple(materialized.template_paths.values()),
                **kwargs,
            )
        live_ids = {role: str(notebooks[role]["id"]) for role in roles}
        after_open = wrappers["source"].snapshot_open_notebooks()
        wrappers["source"].assert_no_active_working_conflict(
            notebook_ids=live_ids,
            working_paths=materialized.working_paths,
            open_notebooks=after_open,
        )
        for role in roles:
            cache_store.record_opened_working_role(
                materialized,
                role=role,
                notebook_id=str(notebooks[role]["id"]),
                actual_path=Path(str(leases[role]["actual_local_path"])),
            )
    return notebooks, leases


def _close_bundle(
    wrappers: Mapping[str, NotebookLifecycleWrapper],
    roles: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    closed: dict[str, dict[str, Any]] = {}
    for role in roles:
        result = wrappers[role].close_exact_notebook()
        if result.get("closed") is not True:
            raise RestoreFailure(f"Notebook role {role} did not close precisely.")
        closed[role] = result
    return closed


__all__ = [
    "PUBLIC_SCENARIOS",
    "SCENARIO_POLICIES",
    "finalize_notebook",
    "isolated_dry_run",
    "record_failure",
    "run_validate",
]
