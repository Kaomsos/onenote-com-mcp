"""Pure dry-run plans and immutable registered-case declarations."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Mapping, TYPE_CHECKING

from ...path_budget import fingerprint_disk_key, instance_location_from_id
from ...runtime import RunnerFailure, RuntimeOptions

if TYPE_CHECKING:
    from .specs import ScenarioSpec
    from ..fixture_recipes.recipe_base import RecipeBase


FORBIDDEN_CASE_ARGUMENTS = frozenset(
    {
        "--dry-run",
        "--json",
        "--run-dir",
        "--timeout",
        "--notebook-label",
        "--notebook-name",
        "--keep-notebook",
    }
)

@dataclass(frozen=True)
class DryRunExpectations:
    lifecycle: str = "close"
    server_started: bool = False
    expected_mcp_process_starts: int = 1


@dataclass(frozen=True)
class DryRunVariant:
    case_suffix: str
    scenario_args: tuple[str, ...]
    expectations: DryRunExpectations = field(default_factory=DryRunExpectations)
    documentation_key: str | None = None


@dataclass(frozen=True)
class DryRunCase:
    case_id: str
    scenario_name: str
    scenario_args: tuple[str, ...] = ()
    expected: DryRunExpectations = field(default_factory=DryRunExpectations)
    documentation_key: str | None = None

    def __post_init__(self) -> None:
        if not self.case_id or not self.scenario_name:
            raise ValueError("Dry-run case ID and scenario name must be non-empty.")
        if re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", self.case_id) is None:
            raise ValueError(f"Dry-run case ID is not stable: {self.case_id}")
        if self.documentation_key is not None and re.fullmatch(
            r"[a-z0-9]+(?:[.-][a-z0-9]+)*", self.documentation_key
        ) is None:
            raise ValueError(f"Invalid dry-run documentation key: {self.documentation_key}")
        if any(token in FORBIDDEN_CASE_ARGUMENTS for token in self.scenario_args):
            raise ValueError(f"Dry-run case {self.case_id} contains a harness-owned argument.")
        if any(token in {self.scenario_name, "all"} for token in self.scenario_args):
            raise ValueError(f"Dry-run case {self.case_id} contains a command token.")

    def argv(self, run_dir: Path | None = None) -> list[str]:
        values = [self.scenario_name, *self.scenario_args]
        if self.scenario_name != "all":
            if run_dir is None:
                raise ValueError("Named dry-run cases require a harness-controlled run directory.")
            values.extend(["--run-dir", str(run_dir)])
        values.extend(["--dry-run", "--json"])
        return values

    def documented_command(self) -> str:
        arguments = " ".join((self.scenario_name, *self.scenario_args, "--dry-run", "--json"))
        return f".venv\\Scripts\\python.exe tests\\manual_validation\\run.py {arguments}"


def _keep_source_notebook(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "keep_notebook", False) or getattr(args, "keep_worksite", False))


def _step(name: str, policy: Any, tools: set[str], target: str) -> dict[str, Any]:
    return {
        "step": name,
        "target": target,
        "mutation_policy": policy.as_dict(),
        "tool_allowlist": sorted(tools),
    }


def build_isolated_dry_run_plan(
    args: argparse.Namespace,
    options: RuntimeOptions,
    *,
    spec: "ScenarioSpec",
    capability_assessment: dict[str, str] | None,
    copy_budget: Mapping[str, int],
    worksite_action: str,
    recipe: "RecipeBase",
) -> dict[str, Any]:
    """Build a serializable plan without creating paths, clients, or lifecycle objects."""

    use_cache = bool(getattr(args, "use_cache", False))
    consumer_cache_required = bool(recipe.consumer_scenario and not use_cache)
    roles = [role.role for role in recipe.cache_identity.notebook_roles]
    multi_role = len(roles) > 1
    steps: list[dict[str, Any]] = [
        {
            "step": "create-notebook-bundle" if multi_role else "create-source-notebook",
            "trust_boundary": "narrow lifecycle wrapper",
            "allowed_operations": ["create_fresh_notebook"],
            "target": (
                f"new exact-name Notebook for every role {roles} under run-dir/notebooks"
                if multi_role
                else "new exact-name Notebook under run-dir/notebooks"
            ),
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
    if not _keep_source_notebook(args):
        steps.append(
            {
                "step": "close-notebook-bundle" if multi_role else "close-source-notebook",
                "trust_boundary": "narrow lifecycle wrapper",
                "allowed_operations": ["get_exact_notebook", "close_exact_notebook"],
                "target": (
                    "every exact role lifecycle lease Notebook ID/name/path"
                    if multi_role
                    else "exact lifecycle lease Notebook ID/name/path"
                ),
            }
        )
    run_dir = options.run_dir.resolve()
    interactive_bootstrap = (
        recipe.build_mode.value == "human_bootstrap_required"
        and getattr(recipe, "bootstrap_scenario_name", None) == args.scenario
    )
    representation_discovery = bool(
        interactive_bootstrap
        and getattr(recipe, "representation_discovery_only", False)
    )
    discovery_cache_rejected = representation_discovery and use_cache
    fresh_only_cache_rejected = use_cache and not getattr(recipe, "supports_cache", True)
    if use_cache and not interactive_bootstrap:
        cache_operations = (
            [
                "validated-hit materialization and live validation",
                "cache miss/invalid fail closed with named bootstrap guidance",
            ]
            if recipe.consumer_scenario
            else [
                "validated-hit materialization",
                "programmatic cold build, close, publish, then materialize",
                "exact invalid-entry cleanup before rebuild",
            ]
        )
        steps[0] = {
            "step": "resolve-fixture-bundle",
            "trust_boundary": "managed immutable fixture cache",
            "allowed_operations": cache_operations,
            "target": "new run-scoped working Notebook path; never a template path",
        }
        steps[1:2] = [
            {
                "step": "prepare-materialized-fixture",
                "trust_boundary": "typed fixture observer and validator",
                "allowed_operations": [
                    "batch OpenHierarchy(exact parent)",
                    "typed relative-address ID rebind",
                    "two stable hierarchy observations",
                    "one full read per declared Page",
                ],
                "target": "the first exact live identity for every materialized role",
            },
            _step(
                args.scenario,
                spec.policy,
                set(spec.tool_allowlist),
                "selected mutation against only stable rebound live IDs",
            ),
        ]
    if interactive_bootstrap:
        steps[1:2] = [
            _step(
                args.scenario,
                spec.policy,
                set(spec.tool_allowlist),
                f"programmatic scaffold for exact {getattr(recipe, 'capability', '')} Canvas",
            ),
            {
                "step": "interactive-checkpoint",
                "trust_boundary": "run-bound user confirmation with bounded timeout",
                "target": "exact disposable role/Canvas or authoring zone",
            },
            {
                "step": (
                    "record-evidence-only-and-close"
                    if representation_discovery
                    else "close-stage-publish-materialize-live-validate"
                ),
                "trust_boundary": (
                    "local content-free evidence"
                    if representation_discovery
                    else "managed local fixture cache"
                ),
                "target": (
                    "no template publication or mutation eligibility"
                    if representation_discovery
                    else "closed opaque template bundle then a second working bundle"
                ),
                "templates_opened": False,
            },
        ]
    if discovery_cache_rejected:
        steps = [
            {
                "step": "preflight-discovery-rejects-cache",
                "trust_boundary": "static representation-discovery contract",
                "allowed_operations": [],
                "target": "reject before lifecycle, MCP, cache, stdin, or mutation",
                "reason": "UI representation discovery never reads or publishes fixture cache",
            }
        ]
    if fresh_only_cache_rejected:
        steps = [
            {
                "step": "preflight-fresh-only-rejects-cache",
                "trust_boundary": "static fresh-only Recipe contract",
                "allowed_operations": [],
                "target": "reject before lifecycle, MCP, cache, or mutation",
                "reason": "this Recipe generates fresh in-memory search probes",
            }
        ]
    if consumer_cache_required:
        steps = [
            {
                "step": "preflight-cache-required",
                "trust_boundary": "static consumer contract",
                "allowed_operations": [],
                "target": "reject before lifecycle, MCP, cache, stdin, or mutation",
                "reason": "interactive fixture consumers require --use-cache",
            }
        ]
    fresh_names = getattr(args, "fresh_notebook_names", None)
    cached_names = getattr(args, "cached_notebook_names", None)
    if not isinstance(fresh_names, dict):
        fresh_names = {"source": args.notebook_name}
    if not isinstance(cached_names, dict):
        cached_names = {role: f"{role}-working-copy" for role in roles}
    effective_name = (
        str(cached_names["source"])
        if use_cache and not interactive_bootstrap and not fresh_only_cache_rejected
        else str(fresh_names["source"])
    )
    result = {
        "command": args.scenario,
        "scenario": args.scenario,
        "dry_run": True,
        "dry_run_contract": True,
        "human_only": True,
        "agent_execution_prohibited": True,
        "notebook_name": effective_name,
        "notebook_names": {
            "fresh": dict(fresh_names),
            "cached": dict(cached_names),
        },
        "run_dir": str(run_dir),
        "notebook_base_folder": str((run_dir / "notebooks").resolve()),
        "fixture_profile": spec.fixture.as_dict(),
        "scenario_spec": spec.as_dict(),
        "timeout_seconds": options.timeout,
        "copy_budget": dict(copy_budget),
        "search_budget": dict(spec.search_budget),
        "lifecycle": "keep" if _keep_source_notebook(args) else "close",
        "lifecycle_lease": str((run_dir / "lifecycle-lease.json")),
        "lifecycle_leases": {
            role: str(
                run_dir
                / ("lifecycle-lease.json" if role == "source" else f"lifecycle-lease-{role}.json")
            )
            for role in roles
        },
        "expected_mcp_process_starts": (
            0
            if consumer_cache_required or discovery_cache_rejected or fresh_only_cache_rejected
            else 1
        ),
        "server_started": False,
        "ordered_steps": steps,
        "filesystem_cleanup": {
            "enabled": False,
            "result": "source and Copy Notebook directories are always preserved",
        },
    }
    run_identity = getattr(args, "run_identity", None)
    if hasattr(run_identity, "as_dict"):
        result["run_identity"] = run_identity.as_dict()
    cache_root = (
        options.cache_root or (Path(".local-validation") / "fixture-cache")
    ).resolve()
    template_instance_id = recipe.select_template_instance_id(
        args,
        allow_unselected=True,
    )
    try:
        instance_location = instance_location_from_id(template_instance_id)
    except RunnerFailure:
        if template_instance_id != "required-explicit-template-instance":
            raise
        instance_location = None
    result["cache"] = {
        "cache_mode": (
            "cache_required"
            if consumer_cache_required
            else "fresh_only"
            if fresh_only_cache_rejected
            else "representation_discovery"
            if representation_discovery
            else "interactive_bootstrap"
            if interactive_bootstrap
            else "use_cache"
            if use_cache
            else "fresh"
        ),
        "enabled": (
            (use_cache or interactive_bootstrap)
            and not representation_discovery
            and not fresh_only_cache_rejected
        ),
        "cache_root": str(cache_root),
        "fingerprint": recipe.cache_fingerprint,
        "fingerprint_disk_key": fingerprint_disk_key(recipe.cache_fingerprint),
        "template_instance_id": template_instance_id,
        "instance_location": (
            instance_location.as_dict() if instance_location is not None else None
        ),
        "roles": {
            role: {
                "template_path": str(
                    cache_root
                    / fingerprint_disk_key(recipe.cache_fingerprint)
                    / "instances"
                    / Path(*(instance_location.parts if instance_location else ("explicit-required",)))
                    / "notebooks"
                    / role
                    / "template-notebook"
                ),
                "working_name": str(cached_names[role]),
                "working_path": str(run_dir / "notebooks" / str(cached_names[role])),
            }
            for role in roles
        },
        "decision": (
            "rejected_missing_use_cache"
            if consumer_cache_required
            else "rejected_fresh_only"
            if fresh_only_cache_rejected
            else "rejected_cache_for_representation_discovery"
            if discovery_cache_rejected
            else "evidence_only_no_publish"
            if representation_discovery
            else "bootstrap_plan_not_executed"
            if interactive_bootstrap
            else "runtime_lookup_not_performed_in_dry_run"
            if use_cache
            else "fresh"
        ),
        "planned_branches": (
            ["fail closed before lifecycle: rerun with --use-cache and an exact instance"]
            if consumer_cache_required
            else ["fail closed before lifecycle: fresh-only Recipe forbids --use-cache"]
            if fresh_only_cache_rejected
            else ["fail closed before lifecycle: representation discovery forbids --use-cache"]
            if discovery_cache_rejected
            else [
                "fresh scaffold and run-bound UI confirmation",
                "record content-free kind/capability delta as evidence_only",
                "never publish a template or enable Copy/Move",
            ]
            if representation_discovery
            else [
                "validated-hit: materialize, batch import, close, exact-path reopen, then rebind and validate",
                "working activation failure: preserve run/lease; validated template remains retryable after close",
                f"miss/invalid: interactive_bootstrap_required {recipe.bootstrap_scenario_name}",
            ]
            if recipe.consumer_scenario and use_cache
            else
            [
                "validated-hit: lock, inventory, materialize, import-close-reopen, live validate",
                "cold-miss: build fresh, live validate, close all, stage, inventory, publish, materialize, import-close-reopen",
                (
                    "invalid: exact safe cleanup then interactive bootstrap"
                    if interactive_bootstrap
                    else "invalid: exact safe cleanup then programmatic rebuild"
                ),
            ]
            if use_cache or interactive_bootstrap
            else ["fresh build and live validation; zero cache access"]
        ),
        "cache_access_performed": False,
        "templates_opened": False,
        "interactive_checkpoint": (
            {
                "bootstrap_scenario": recipe.bootstrap_scenario_name,
                "capability": getattr(recipe, "capability", ""),
                "authoring_instruction": getattr(
                    recipe, "authoring_instruction", ""
                ),
                "stdin_read_performed": False,
                "authoring_zones": [
                    zone.manifest_key for zone in getattr(recipe, "authoring_zones", ())
                ],
            }
            if interactive_bootstrap
            else None
        ),
        "fixed_invalidation_probe": bool(recipe.invalidation_probe),
    }
    if capability_assessment is not None:
        result["capability_assessment"] = dict(capability_assessment)
    if hasattr(args, "keep_worksite"):
        result["worksite"] = {
            "preserved": bool(args.keep_worksite),
            "target_cleanup": worksite_action if args.keep_worksite else "default-scenario-finalization",
        }
    return result


__all__ = [
    "DryRunCase",
    "DryRunExpectations",
    "DryRunVariant",
    "FORBIDDEN_CASE_ARGUMENTS",
    "build_isolated_dry_run_plan",
]
