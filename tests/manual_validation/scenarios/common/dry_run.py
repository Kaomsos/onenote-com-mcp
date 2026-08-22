"""Pure dry-run plans and immutable registered-case declarations."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Mapping, TYPE_CHECKING

from ...bridge_adapter import VALIDATION_BRIDGE_ADAPTER
from ...onenote_exit_wait import dry_run_bounded_wait_projection
from ...page_stability import dry_run_page_stability_projection
from ...path_budget import fingerprint_disk_key, instance_location_from_id
from ...runtime import RunnerFailure, RuntimeOptions
from .datetime_drift_negative import (
    dry_run_datetime_drift_projection,
    dry_run_datetime_drift_steps,
)

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
    production_close_handoff: bool,
) -> dict[str, Any]:
    """Build a serializable plan without creating paths, clients, or lifecycle objects."""

    use_cache = bool(getattr(args, "use_cache", False))
    interactive_scenario = recipe.build_mode.value == "human_bootstrap_required"
    interactive_fresh = interactive_scenario and not use_cache
    interactive_cache = interactive_scenario and use_cache
    roles = [role.role for role in recipe.cache_identity.notebook_roles]
    multi_role = len(roles) > 1
    mutation_target = {
        "create": (
            "same-title Page allocation, normalized-duplicate batch preflight rejection, "
            "three typed Create batches, exact non-permanent cleanup, and restore proof"
        ),
        "rename": "fixed Page, Section, and SectionGroup Rename/read-back/restore cases",
        "reorder-page": (
            "Page Reorder, Page-parent direct-child Sort, child_type conflict preflight "
            "rejection, unchanged snapshot proof, and restore"
        ),
        "onenote-convergence": (
            "fixed Notebook Create/Close, effect, Page Replace/Append/Delete/Reorder, and source Close chain"
        ),
    }.get(
        args.scenario,
        str(
            getattr(recipe, "dry_run_scenario_target", "")
            or f"fixture profile {spec.fixture.name} and selected mutation"
        ),
    )
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
            mutation_target,
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
                "allowed_operations": [
                    *(
                        ["adopt_production_close"]
                        if production_close_handoff
                        else []
                    ),
                    "get_exact_notebook",
                    "close_exact_notebook",
                ],
                "target": (
                    "every exact role lifecycle lease Notebook ID/name/path"
                    if multi_role
                    else "exact lifecycle lease Notebook ID/name/path"
                ),
            }
        )
    run_dir = options.run_dir.resolve()
    interactive_bootstrap = interactive_fresh
    representation_discovery = bool(
        interactive_fresh and getattr(recipe, "representation_discovery_only", False)
    )
    discovery_cache_rejected = representation_discovery and use_cache
    recipe_fresh_only = use_cache and not getattr(recipe, "supports_cache", True)
    mode_fresh_only = use_cache and spec.execution_contract.get("fresh_only") is True
    fresh_only_cache_rejected = recipe_fresh_only or mode_fresh_only
    if (
        getattr(args, "scenario", "") == "search-all-open-notebooks"
        and not use_cache
    ):
        build_step = _step(
                args.scenario,
                spec.policy,
                set(spec.tool_allowlist),
                f"build fixture profile {spec.fixture.name}",
            )
        build_step["step"] = "prepare-search-fixture"
        steps[1:2] = [
            build_step,
            {
                "step": "activate-search-index-fixture",
                "trust_boundary": "exact lifecycle leases plus typed fixture observer",
                "allowed_operations": [
                    "CloseNotebook(force=false)",
                    "reopen exact working paths",
                    "typed relative-address ID rebind",
                    "two stable hierarchy observations",
                    "one full read per declared Page",
                ],
                "target": "fresh Search fixture bundle only",
            },
            _step(
                args.scenario,
                spec.policy,
                set(spec.tool_allowlist),
                "index-only Search against checkpointed rebound live IDs",
            ),
        ]
    if use_cache and not interactive_fresh:
        cache_operations = [
            "validated-hit materialization and live validation",
            "cache miss/invalid/ambiguous fail closed without fresh authoring fallback",
        ]
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
                (
                    "fixed Page, Section, and SectionGroup Rename cases against stable rebound live IDs"
                    if args.scenario == "rename"
                    else str(
                        getattr(recipe, "dry_run_scenario_target", "")
                        or "selected mutation against only stable rebound live IDs"
                    )
                ),
            ),
        ]
    if interactive_fresh:
        steps[1:2] = [
            _step(
                args.scenario,
                spec.policy,
                set(spec.tool_allowlist),
                str(
                    getattr(recipe, "dry_run_scaffold_target", "")
                    or f"programmatic scaffold for exact {getattr(recipe, 'capability', '')} Canvas"
                ),
            ),
            {
                "step": "interactive-checkpoint",
                "trust_boundary": "run-bound user confirmation with bounded timeout",
                "target": str(
                    getattr(recipe, "dry_run_checkpoint_target", "")
                    or "exact disposable role/Canvas or authoring zone"
                ),
                "stdin_read_performed": False,
            },
            {
                "step": (
                    "record-evidence-only-and-close"
                    if representation_discovery
                    else "close-stage-publish-materialize"
                ),
                "trust_boundary": (
                    "local content-free evidence"
                    if representation_discovery
                    else "managed local fixture cache"
                ),
                "target": (
                    "no template publication or mutation eligibility"
                    if representation_discovery
                    else "closed opaque template bundle for immutable publication"
                ),
                "templates_opened": False,
            },
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
                mutation_target,
            ),
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
                "trust_boundary": (
                    "static fresh-only mode contract"
                    if mode_fresh_only
                    else "static fresh-only Recipe contract"
                ),
                "allowed_operations": [],
                "target": "reject before lifecycle, MCP, cache, or mutation",
                "reason": (
                    spec.execution_contract.get("fresh_only_reason")
                    or recipe.fresh_only_reason
                ),
            }
        ]
    if getattr(args, "template_instance_id", None) and interactive_fresh:
        steps = [
            {
                "step": "preflight-fresh-rejects-template-instance-id",
                "trust_boundary": "static interactive fresh contract",
                "allowed_operations": [],
                "target": "reject before lifecycle, MCP, cache, stdin, or mutation",
                "reason": "Fresh interactive runs must not pass --template-instance-id",
            }
        ]
    if spec.execution_contract.get("human_gated_onenote_close"):
        close_step = {
            "step": "human-onenote-close-and-native-stopped-health",
            "trust_boundary": "run-bound user confirmation plus native health preflight",
            "target": (
                "bounded native fully-stopped wait before same-MCP launch and mutation"
            ),
            **dry_run_bounded_wait_projection(),
        }
        extra_before = [close_step]
        extra_after: list[dict[str, Any]] = []
        if spec.execution_contract.get("refresh_internal_validation_com"):
            extra_before.append(
                {
                    "step": "refresh-internal-validation-com-and-page-xml-probe",
                    "trust_boundary": (
                        "harness-owned validation COM independent of MCP child"
                    ),
                    "target": (
                        "refresh harness-owned internal COM then exact page XML "
                        "probe before mutation"
                    ),
                    "allowed_operations": ["get_page_xml"],
                    "stdin_read_performed": False,
                    "sleep_performed": False,
                    "gui_state_read": False,
                    "mcp_child_refresh_is_not_sufficient": True,
                }
            )
        if spec.execution_contract.get("refresh_lifecycle_validation_com"):
            extra_before.append(
                {
                    "step": "refresh-lifecycle-validation-com-and-exact-notebook-probe",
                    "trust_boundary": (
                        "run-scoped NotebookLifecycleWrapper COM independent of "
                        "MCP child and internal bridge"
                    ),
                    "target": (
                        "refresh harness-owned lifecycle COM then exact Notebook "
                        "probe before mutation"
                    ),
                    "allowed_operations": ["get_exact_notebook"],
                    "stdin_read_performed": False,
                    "sleep_performed": False,
                    "gui_state_read": False,
                    "mcp_child_refresh_is_not_sufficient": True,
                    "internal_bridge_refresh_is_not_sufficient": True,
                }
            )
        if spec.execution_contract.get("stabilize_target_page_baseline"):
            extra_before.append(
                {
                    "step": "stabilize-target-page-baseline-before-mutation",
                    "trust_boundary": (
                        "post-refresh owned Page identity before the unique rename"
                    ),
                    "target": (
                        "bounded expand_page title/id/parent/modified stability "
                        "before rename"
                    ),
                    **dry_run_page_stability_projection(phase="baseline"),
                }
            )
        if spec.execution_contract.get("observe_forward_rename_durability"):
            extra_after.append(
                {
                    "step": "observe-forward-rename-durability",
                    "trust_boundary": (
                        "post-rename owned Page identity; revert skips restore"
                    ),
                    "target": (
                        "bounded expand_page durability after the unique forward "
                        "rename; revert to original is forward_not_durable"
                    ),
                    **dry_run_page_stability_projection(phase="forward_durability"),
                }
            )
        if (
            spec.execution_contract.get("close_source_before_mcp_exit")
            and not _keep_source_notebook(args)
        ):
            extra_after.append(
                {
                    "step": "close-source-notebook-before-mcp-exit",
                    "trust_boundary": (
                        "run-scoped lifecycle COM while the scenario MCP is alive"
                    ),
                    "target": (
                        "exact Notebook close before MCP/internal COM teardown"
                    ),
                    "allowed_operations": ["close_exact_notebook"],
                    "stdin_read_performed": False,
                    "sleep_performed": False,
                    "gui_state_read": False,
                    "mcp_client_still_active": True,
                }
            )
        for index, step in enumerate(steps):
            if step.get("step") == args.scenario:
                for offset, extra in enumerate(extra_before):
                    steps.insert(index + offset, extra)
                after_index = index + len(extra_before) + 1
                for offset, extra in enumerate(extra_after):
                    steps.insert(after_index + offset, extra)
                break
    if (
        spec.execution_contract.get("close_source_before_mcp_exit")
        and not _keep_source_notebook(args)
    ):
        for step in steps:
            if step.get("step") in {"close-source-notebook", "close-notebook-bundle"}:
                step["allowed_operations"] = []
                step["preclosed_lease_only"] = True
                step["target"] = (
                    "accept durable pre-closed lease without a second COM close"
                )
    if spec.execution_contract.get("datetime_drift_negative") and not fresh_only_cache_rejected:
        extras = dry_run_datetime_drift_steps()
        for index, step in enumerate(steps):
            if step.get("step") == args.scenario:
                after = index + 1
                for offset, extra in enumerate(extras):
                    steps.insert(after + offset, extra)
                break
    fresh_names = getattr(args, "fresh_notebook_names", None)
    cached_names = getattr(args, "cached_notebook_names", None)
    if not isinstance(fresh_names, dict):
        fresh_names = {"source": args.notebook_name}
    if not isinstance(cached_names, dict):
        cached_names = {role: f"{role}-working-copy" for role in roles}
    effective_name = (
        str(cached_names["source"])
        if use_cache and not interactive_fresh and not fresh_only_cache_rejected
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
        "bridge_adapter": VALIDATION_BRIDGE_ADAPTER,
        "copy_budget": dict(copy_budget),
        "search_budget": dict(spec.search_budget),
        "batch_mutation_budget": dict(spec.batch_mutation_budget),
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
            if discovery_cache_rejected
            or fresh_only_cache_rejected
            or (getattr(args, "template_instance_id", None) and interactive_fresh)
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
            "fresh_only"
            if fresh_only_cache_rejected
            else "representation_discovery"
            if representation_discovery
            else "interactive_fresh"
            if interactive_fresh
            else "use_cache"
            if use_cache
            else "fresh"
        ),
        "enabled": (
            (use_cache or interactive_fresh)
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
            "rejected_fresh_only"
            if fresh_only_cache_rejected
            else "rejected_cache_for_representation_discovery"
            if discovery_cache_rejected
            else "rejected_fresh_template_instance_id"
            if getattr(args, "template_instance_id", None) and interactive_fresh
            else "evidence_only_no_publish"
            if representation_discovery
            else "bootstrap_plan_not_executed"
            if interactive_fresh
            else "runtime_lookup_not_performed_in_dry_run"
            if use_cache
            else "fresh"
        ),
        "planned_branches": (
            ["fail closed before lifecycle: fresh-only Recipe forbids --use-cache"]
            if fresh_only_cache_rejected
            else ["fail closed before lifecycle: representation discovery forbids --use-cache"]
            if discovery_cache_rejected
            else ["fail closed before lifecycle: fresh interactive forbids --template-instance-id"]
            if getattr(args, "template_instance_id", None) and interactive_fresh
            else ["fail closed before lifecycle: evidence-only discovery never publishes cache"]
            if representation_discovery
            else ["bootstrap phase planned but not executed in dry-run"]
            if interactive_fresh
            else ["runtime cache lookup and materialization are not executed in dry-run"]
            if use_cache
            else ["fresh run-scoped Notebook creation only"]
        ),
        "cache_access_performed": False,
        "templates_opened": False,
        "interactive_checkpoint": (
            {
                "scenario": args.scenario,
                "capability": getattr(recipe, "capability", ""),
                "authoring_instruction": getattr(recipe, "authoring_instruction", ""),
                "stdin_read_performed": False,
                "authoring_zones": [
                    zone.manifest_key for zone in getattr(recipe, "authoring_zones", ())
                ],
            }
            if interactive_fresh
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
    if spec.execution_contract.get("datetime_drift_negative") and not fresh_only_cache_rejected:
        result.update(dry_run_datetime_drift_projection(run_dir=run_dir))
    return result


__all__ = [
    "DryRunCase",
    "DryRunExpectations",
    "DryRunVariant",
    "FORBIDDEN_CASE_ARGUMENTS",
    "build_isolated_dry_run_plan",
]
