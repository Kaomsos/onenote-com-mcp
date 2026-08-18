"""The sole base contract for scenario-owned Notebook bundle recipes."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping, TypeAlias

from ...runtime import InvariantFailure
from ...path_budget import validate_role
from ..common.fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureValidationContext,
)
from ..common.specs import FixtureProfile, ScenarioSpec, get_scenario_spec

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class BuildMode(str, Enum):
    PROGRAMMATIC = "programmatic"
    HUMAN_BOOTSTRAP_REQUIRED = "human_bootstrap_required"


@dataclass(frozen=True)
class NotebookRoleSpec:
    role: str
    profile: FixtureProfile
    fixture_parameters: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            validate_role(self.role)
        except ValueError as exc:
            raise ValueError(f"Invalid Notebook role: {self.role!r}; {exc}") from exc
        object.__setattr__(
            self,
            "fixture_parameters",
            MappingProxyType(dict(self.fixture_parameters)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "profile": self.profile.as_dict(),
            "fixture_parameters": _canonical_value(self.fixture_parameters),
        }


@dataclass(frozen=True)
class FixtureCacheIdentity:
    schema_version: int
    recipe_name: str
    recipe_version: int
    notebook_roles: tuple[NotebookRoleSpec, ...]
    evidence_schema_version: int
    contract_compatibility_version: int
    bundle_invariants: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        roles = tuple(role.role for role in self.notebook_roles)
        if not roles or len(set(roles)) != len(roles):
            raise ValueError("A fixture cache identity requires unique Notebook roles.")
        if roles != tuple(sorted(roles)):
            raise ValueError("Notebook roles must use canonical lexical ordering.")
        if not self.recipe_name or min(
            self.schema_version,
            self.recipe_version,
            self.evidence_schema_version,
            self.contract_compatibility_version,
        ) < 1:
            raise ValueError("Fixture cache identity names and versions must be positive.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "recipe_name": self.recipe_name,
            "recipe_version": self.recipe_version,
            "notebook_roles": [role.as_dict() for role in self.notebook_roles],
            "evidence_schema_version": self.evidence_schema_version,
            "contract_compatibility_version": self.contract_compatibility_version,
            "bundle_invariants": list(self.bundle_invariants),
        }


@dataclass(frozen=True)
class FixtureBundleBuildReceipt:
    roles: Mapping[str, FixtureBuildResult]
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FixtureRoleObservation:
    role: str
    args: argparse.Namespace
    notebook: Mapping[str, Any]
    notebook_path: str
    snapshot: Mapping[str, Any]
    build: FixtureBuildResult


@dataclass(frozen=True)
class FixtureBundleObservation:
    roles: Mapping[str, FixtureRoleObservation]


@dataclass(frozen=True)
class FixtureValidationReport:
    passed: bool
    role_checks: Mapping[str, tuple[str, ...]]
    bundle_checks: tuple[str, ...]


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"Cache identity contains a non-JSON value: {type(value).__name__}")


def canonical_cache_fingerprint(identity: FixtureCacheIdentity) -> str:
    payload = json.dumps(
        _canonical_value(identity.as_dict()),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class RecipeBase(ABC):
    scenario_name: str
    manifest_keys: frozenset[str]
    recipe_version = 1
    evidence_schema_version = 1
    contract_compatibility_version = 1
    build_mode = BuildMode.PROGRAMMATIC
    invalidation_probe = False
    requires_instance_selection = False
    accepts_evidence_only = False
    consumer_scenario = False  # legacy flag; unified interactive scenarios no longer use this
    supports_cache = True
    fresh_only_reason = "this Recipe requires a new run-scoped fixture"
    bundle_invariants = ("all role Notebook IDs and resolved paths are unique",)

    def __init__(
        self,
        scenario_name: str,
        manifest_keys: frozenset[str] | None = None,
        *,
        notebook_roles: tuple[NotebookRoleSpec, ...] | None = None,
        cache_recipe_name: str | None = None,
    ) -> None:
        self.scenario_name = scenario_name
        self.profile: FixtureProfile = get_scenario_spec(scenario_name).fixture
        self.manifest_keys = manifest_keys or frozenset(self.profile.manifest_keys)
        self.notebook_roles = notebook_roles or (
            NotebookRoleSpec(
                role="source",
                profile=self.profile,
                fixture_parameters={"manifest_keys": sorted(self.manifest_keys)},
            ),
        )
        self.cache_identity = FixtureCacheIdentity(
            schema_version=2,
            recipe_name=cache_recipe_name or scenario_name,
            recipe_version=self.recipe_version,
            notebook_roles=tuple(sorted(self.notebook_roles, key=lambda item: item.role)),
            evidence_schema_version=self.evidence_schema_version,
            contract_compatibility_version=self.contract_compatibility_version,
            bundle_invariants=tuple(self.bundle_invariants),
        )
        self.cache_fingerprint = canonical_cache_fingerprint(self.cache_identity)

    @property
    def recipe_name(self) -> str:
        return self.cache_identity.recipe_name

    @property
    def default_template_instance_id(self) -> str:
        return f"programmatic-{self.cache_fingerprint[:16]}"

    def select_template_instance_id(
        self,
        args: argparse.Namespace,
        *,
        allow_unselected: bool = False,
        cache_store: Any | None = None,
    ) -> str:
        del cache_store
        return self.default_template_instance_id

    def required_manifest_keys(self, args: argparse.Namespace) -> frozenset[str]:
        return self.manifest_keys

    def manifest_keys_for_role(
        self,
        role: str,
        args: argparse.Namespace | None = None,
    ) -> frozenset[str]:
        if (
            args is not None
            and role == "source"
            and len(self.cache_identity.notebook_roles) == 1
        ):
            return self.required_manifest_keys(args)
        for declared in self.cache_identity.notebook_roles:
            if declared.role != role:
                continue
            keys = declared.fixture_parameters.get("manifest_keys")
            if isinstance(keys, (list, tuple)) and all(
                isinstance(value, str) for value in keys
            ):
                return frozenset(keys)
            if role == "source":
                return self.required_manifest_keys(args) if args is not None else self.manifest_keys
            raise InvariantFailure(
                f"Recipe role {role!r} has no declared manifest_keys fixture parameter."
            )
        raise InvariantFailure(f"Recipe has no declared Notebook role: {role}")

    def validate_registration(self, spec: ScenarioSpec) -> None:
        if self.scenario_name != spec.name or self.profile != spec.fixture:
            raise ValueError(f"Fixture recipe/profile mismatch: {self.scenario_name}")
        if not self.supports_cache and not self.fresh_only_reason.strip():
            raise ValueError(
                f"Fresh-only fixture recipe requires a rejection reason: {self.scenario_name}"
            )
        if self.manifest_keys != frozenset(spec.fixture.manifest_keys):
            raise ValueError(
                f"Fixture recipe manifest keys differ from profile: {self.scenario_name}"
            )
        if not self.notebook_roles or len({role.role for role in self.notebook_roles}) != len(
            self.notebook_roles
        ):
            raise ValueError(f"Fixture recipe roles are invalid: {self.scenario_name}")
        for role in self.notebook_roles:
            if not role.profile.creation_tools.issubset(spec.tool_allowlist):
                raise ValueError(
                    f"Fixture recipe role creation tools exceed allowlist: {self.scenario_name}/{role.role}"
                )

    @abstractmethod
    async def build(self, context: FixtureContext) -> FixtureBuildResult:
        """Build one role using the scenario's single statically allowlisted MCP client."""

    def validate(
        self,
        context: FixtureValidationContext,
        build: FixtureBuildResult,
    ) -> tuple[str, ...]:
        raise NotImplementedError

    def begin_snapshot_content_validation(self) -> None:
        """Reset optional process-local state used while Page XML is read once."""

    def snapshot_page_observer(
        self,
        role: str,
        build: FixtureBuildResult,
    ) -> Callable[[Mapping[str, Any], str], None] | None:
        """Return an optional non-persisting observer for the existing Page read."""

        return None

    def complete_snapshot_content_validation(self) -> None:
        """Validate optional process-local observations after every role was read."""

    def validate_live(self, observation: FixtureBundleObservation) -> FixtureValidationReport:
        expected_roles = tuple(role.role for role in self.cache_identity.notebook_roles)
        if tuple(sorted(observation.roles)) != expected_roles:
            raise InvariantFailure("Live fixture observation role set differs from recipe identity.")
        role_checks: dict[str, tuple[str, ...]] = {}
        notebook_ids: list[str] = []
        notebook_paths: list[str] = []
        for role in expected_roles:
            current = observation.roles[role]
            role_checks[role] = self.validate(
                FixtureValidationContext(
                    args=current.args,
                    snapshot=current.snapshot,
                    role=role,
                ),
                current.build,
            )
            notebook_ids.append(str(current.notebook.get("id", "")))
            notebook_paths.append(str(current.notebook_path))
        if any(not value for value in notebook_ids) or len(set(notebook_ids)) != len(notebook_ids):
            raise InvariantFailure("Notebook bundle roles do not have unique active IDs.")
        normalized_paths = [value.casefold() for value in notebook_paths]
        if any(not value for value in normalized_paths) or len(set(normalized_paths)) != len(
            normalized_paths
        ):
            raise InvariantFailure("Notebook bundle roles do not have unique resolved paths.")
        return FixtureValidationReport(
            passed=True,
            role_checks=role_checks,
            bundle_checks=tuple(self.bundle_invariants),
        )


def evidence(build: FixtureBuildResult, key: str) -> dict[str, Any] | None:
    value = build.evidence.get(key)
    return value if isinstance(value, dict) else None


__all__ = [
    "BuildMode",
    "FixtureBundleBuildReceipt",
    "FixtureBundleObservation",
    "FixtureCacheIdentity",
    "FixtureRoleObservation",
    "FixtureValidationReport",
    "JSONValue",
    "NotebookRoleSpec",
    "RecipeBase",
    "canonical_cache_fingerprint",
    "evidence",
]
