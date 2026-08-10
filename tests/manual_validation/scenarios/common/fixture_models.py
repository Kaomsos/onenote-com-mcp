"""Typed contracts shared by scenario-owned fixture recipes and runtime."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import platform
import sys
from typing import Any, Mapping, Protocol

from ...mcp_stdio_client import MCPStdioClient
from ...runtime import InvariantFailure, RuntimeOptions
from ...test_utils import installed_runner_version, manifest_path, stable_item, utc_now, write_json
from .config import AUTOMATED_COPY_CAPABILITIES
from .specs import FixtureProfile, ScenarioSpec


@dataclass(frozen=True)
class FixtureBuildResult:
    structure: Mapping[str, Mapping[str, Any]]
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FixtureContext:
    args: argparse.Namespace
    options: RuntimeOptions
    client: MCPStdioClient
    notebook: Mapping[str, Any]
    notebook_path: str
    spec: ScenarioSpec
    token: str
    recorder: "FixtureRecorder"

    @property
    def notebook_id(self) -> str:
        return str(self.notebook["id"])


@dataclass(frozen=True)
class FixtureValidationContext:
    args: argparse.Namespace
    snapshot: Mapping[str, Any]


class FixtureRecipe(Protocol):
    scenario_name: str
    profile: FixtureProfile
    manifest_keys: frozenset[str]

    def required_manifest_keys(self, args: argparse.Namespace) -> frozenset[str]: ...

    def validate_registration(self, spec: ScenarioSpec) -> None: ...

    async def build(self, context: FixtureContext) -> FixtureBuildResult: ...

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]: ...


class FixtureRecorder:
    """Record exact created IDs and persist a recoverable pending checkpoint."""

    def __init__(
        self,
        *,
        run_dir: Path,
        notebook: Mapping[str, Any],
        notebook_path: str,
        spec: ScenarioSpec,
        allowed_keys: frozenset[str],
    ) -> None:
        self.run_dir = run_dir
        self.notebook = dict(notebook)
        self.notebook_path = notebook_path
        self.spec = spec
        self.allowed_keys = allowed_keys
        self.structure: dict[str, dict[str, Any]] = {}
        self.evidence: dict[str, Any] = {}

    def record_structure(self, key: str, item: Mapping[str, Any]) -> dict[str, Any]:
        if key not in self.allowed_keys:
            raise InvariantFailure(f"Fixture recipe attempted undeclared manifest key: {key}")
        if key in self.structure:
            raise InvariantFailure(f"Fixture recipe attempted duplicate manifest key: {key}")
        value = dict(item)
        if not value.get("id"):
            raise InvariantFailure(f"Fixture structure.{key} has no exact object ID.")
        self.structure[key] = value
        self.persist("pending")
        return value

    def record_many(self, **items: Mapping[str, Any]) -> None:
        for key, item in items.items():
            self.record_structure(key, item)

    def refresh_structure(self, key: str, item: Mapping[str, Any]) -> dict[str, Any]:
        if key not in self.structure:
            raise InvariantFailure(f"Cannot refresh unrecorded fixture key: {key}")
        value = dict(item)
        if str(value.get("id", "")) != str(self.structure[key].get("id", "")):
            raise InvariantFailure(f"Fixture structure.{key} changed exact object ID during build.")
        self.structure[key] = value
        self.persist("pending")
        return value

    def record_evidence(self, key: str, value: Any) -> None:
        if key not in {"copy_fixture", "reparent_page_fixture"}:
            raise InvariantFailure(f"Unsupported fixture evidence key: {key}")
        if key in self.evidence:
            raise InvariantFailure(f"Duplicate fixture evidence key: {key}")
        self.evidence[key] = value
        self.persist("pending")

    def manifest(self, status: str, *, error: str | None = None) -> dict[str, Any]:
        disposable_targets = {
            "notebook_copy_root": str((self.run_dir / "notebook-copies").resolve()),
            "source_notebook_path": str(Path(self.notebook_path).resolve()),
        }
        manifest = {
            "schema_version": 1,
            "run_id": self.run_dir.name,
            "created_at": utc_now(),
            "runner": "tests/manual_validation/run.py",
            "local_onenote_mcp_version": installed_runner_version(),
            "python": sys.version,
            "platform": platform.platform(),
            "notebook": stable_item(self.notebook),
            "structure": {
                key: stable_item(value) for key, value in self.structure.items()
            },
            "disposable_targets": disposable_targets,
            "retry_policy": {
                "mutation_attempts": 1,
                "read_attempts": 2,
                "note": "Only transport failures on read-only calls are retried.",
            },
            "copy_scenario": {
                "supported": True,
                "real_backend_confirmed": True,
                "validated_content_types": sorted(AUTOMATED_COPY_CAPABILITIES),
            },
        }
        manifest["scenario_policies"] = {self.spec.name: self.spec.policy.as_dict()}
        manifest["scenario_spec"] = self.spec.as_dict()
        manifest["scenario_spec"]["fixture_profile"]["actual_manifest_keys"] = sorted(
            self.structure
        )
        manifest["mcp_process_contract"] = {
            "maximum_starts": 1,
            "fixture_and_scenario_share_process": True,
        }
        manifest["lifecycle_lease"] = str(
            (self.run_dir / "lifecycle-lease.json").resolve()
        )
        manifest.update(self.evidence)
        validation: dict[str, Any] = {"status": status}
        if error is not None:
            validation["error"] = error
        manifest["fixture_validation"] = validation
        return manifest

    def persist(self, status: str, *, error: str | None = None) -> dict[str, Any]:
        manifest = self.manifest(status, error=error)
        write_json(manifest_path(self.run_dir), manifest)
        return manifest


class ValidationCollector:
    def __init__(self) -> None:
        self.checks = ["all declared manifest keys resolve to active fresh IDs"]

    def require(self, condition: bool, message: str, check: str) -> None:
        if not condition:
            raise InvariantFailure(message)
        self.checks.append(check)


def resolve_active_structure(
    snapshot: Mapping[str, Any],
    structure: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], ValidationCollector]:
    by_id = {
        str(item["id"]): item
        for item in snapshot.get("items", [])
        if isinstance(item, dict) and item.get("id")
    }
    resolved: dict[str, dict[str, Any]] = {}
    for key, declared in structure.items():
        item = by_id.get(str(declared.get("id", "")))
        if item is None or item.get("is_in_recycle_bin") is True:
            raise InvariantFailure(f"Fixture structure.{key} is missing from the active snapshot.")
        resolved[key] = item
    return resolved, by_id, ValidationCollector()


__all__ = [
    "FixtureBuildResult",
    "FixtureContext",
    "FixtureRecipe",
    "FixtureRecorder",
    "FixtureValidationContext",
    "ValidationCollector",
    "resolve_active_structure",
]
