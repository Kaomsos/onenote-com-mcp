"""Pure dry-run plans and immutable registered-case declarations."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Mapping, TYPE_CHECKING

from ...runtime import RuntimeOptions

if TYPE_CHECKING:
    from .specs import ScenarioSpec


FORBIDDEN_CASE_ARGUMENTS = frozenset(
    {
        "--dry-run",
        "--json",
        "--run-dir",
        "--timeout",
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
) -> dict[str, Any]:
    """Build a serializable plan without creating paths, clients, or lifecycle objects."""

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
    if not _keep_source_notebook(args):
        steps.append(
            {
                "step": "close-source-notebook",
                "trust_boundary": "narrow lifecycle wrapper",
                "allowed_operations": ["get_exact_notebook", "close_exact_notebook"],
                "target": "exact lifecycle lease Notebook ID/name/path",
            }
        )
    run_dir = options.run_dir.resolve()
    result = {
        "command": args.scenario,
        "scenario": args.scenario,
        "dry_run": True,
        "dry_run_contract": True,
        "human_only": True,
        "agent_execution_prohibited": True,
        "notebook_name": args.notebook_name,
        "run_dir": str(run_dir),
        "notebook_base_folder": str((run_dir / "notebooks").resolve()),
        "fixture_profile": spec.fixture.as_dict(),
        "scenario_spec": spec.as_dict(),
        "timeout_seconds": options.timeout,
        "copy_budget": dict(copy_budget),
        "lifecycle": "keep" if _keep_source_notebook(args) else "close",
        "lifecycle_lease": str((run_dir / "lifecycle-lease.json")),
        "expected_mcp_process_starts": 1,
        "server_started": False,
        "ordered_steps": steps,
        "filesystem_cleanup": {
            "enabled": False,
            "result": "source and Copy Notebook directories are always preserved",
        },
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
