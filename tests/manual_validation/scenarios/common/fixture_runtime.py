"""Branch-free orchestration for scenario-owned fixture recipes."""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
import uuid

from ...mcp_stdio_client import MCPStdioClient
from ...runtime import InvariantFailure, RuntimeOptions
from ...test_utils import capture_snapshot, read_json, stable_item, write_json
from ..base import Scenario
from .fixture_models import (
    FixtureBuildResult,
    FixtureContext,
    FixtureRecorder,
    FixtureValidationContext,
)
from .specs import ScenarioSpec
from .fixture_cache import CacheHit, MaterializedBundle
from ..fixture_recipes.recipe_base import (
    BuildMode,
    FixtureBundleObservation,
    FixtureRoleObservation,
)
from ..fixture_recipes.interactive import UserAuthoredRecipe


MATERIALIZED_STRUCTURE_MAX_OBSERVATIONS = 16
MATERIALIZED_STRUCTURE_STABLE_OBSERVATIONS = 2
MATERIALIZED_STRUCTURE_OBSERVATION_DELAY_SECONDS = 0.75


def _assert_authored_cache_identity(hit: CacheHit, frozen: Any) -> None:
    location = hit.entry.get("instance_location")
    recorded_digest = (
        location.get("projection_digest") if isinstance(location, Mapping) else None
    )
    if (
        frozen.template_instance_id != hit.template_instance_id
        or recorded_digest != frozen.projection_digest
    ):
        raise InvariantFailure(
            "User-authored working bundle differs from the cache entry's full frozen identity."
        )


def _record_run_identity(manifest: dict[str, Any], args: argparse.Namespace) -> None:
    identity = getattr(args, "run_identity", None)
    if hasattr(identity, "as_dict"):
        manifest["run_identity"] = identity.as_dict()
    fresh_names = getattr(args, "fresh_notebook_names", None)
    cached_names = getattr(args, "cached_notebook_names", None)
    if isinstance(fresh_names, dict) and isinstance(cached_names, dict):
        manifest["notebook_names"] = {
            "fresh": dict(fresh_names),
            "cached": dict(cached_names),
        }


def _fixture_result(
    scenario_name: str,
    notebook: dict[str, Any],
    recorder: FixtureRecorder,
    *,
    passed: bool,
    checks: tuple[str, ...] = (),
    error: str | None = None,
) -> dict[str, Any]:
    validation: dict[str, Any] = {"passed": passed, "checks": list(checks)}
    if error is not None:
        validation["error"] = error
    return {
        "scenario": scenario_name,
        "notebook": stable_item(notebook),
        "structure_ids": {
            key: value["id"] for key, value in recorder.structure.items()
        },
        "fixture_profile": recorder.manifest("pending")["scenario_spec"][
            "fixture_profile"
        ],
        "validation": validation,
    }


def _persist_failure(
    scenario_name: str,
    notebook: dict[str, Any],
    recorder: FixtureRecorder,
    error: Exception,
) -> None:
    message = str(error)
    recorder.persist("failed", error=message)
    write_json(
        recorder.run_dir
        / (
            "fixture-result.json"
            if recorder.role == "source"
            else f"fixture-role-{recorder.role}-result.json"
        ),
        _fixture_result(
            scenario_name,
            notebook,
            recorder,
            passed=False,
            error=message,
        ),
    )


async def prepare_fixture(
    scenario: Scenario,
    args: argparse.Namespace,
    options: RuntimeOptions,
    client: MCPStdioClient,
    notebook: dict[str, Any],
    notebook_path: str,
    spec: ScenarioSpec,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build, snapshot, validate, and persist one selected Scenario recipe."""

    recipe = scenario.fixture_recipe
    if recipe.profile != spec.fixture or recipe.scenario_name != scenario.name:
        raise InvariantFailure("Scenario fixture recipe/profile ownership mismatch.")
    recorder = FixtureRecorder(
        run_dir=options.run_dir,
        notebook=notebook,
        notebook_path=notebook_path,
        spec=spec,
        allowed_keys=recipe.manifest_keys,
    )
    recorder.persist("pending")
    context = FixtureContext(
        args=args,
        options=options,
        client=client,
        notebook=notebook,
        notebook_path=notebook_path,
        spec=spec,
        token=str(uuid.uuid4()),
        recorder=recorder,
    )
    try:
        if (
            recipe.build_mode == BuildMode.HUMAN_BOOTSTRAP_REQUIRED
            and getattr(recipe, "bootstrap_scenario_name", None) == scenario.name
        ):
            build = await recipe.build_scaffold(context)
        else:
            build = await recipe.build(context)
        if dict(build.structure) != recorder.structure:
            raise InvariantFailure("Fixture recipe returned structure outside its recorder.")
        if dict(build.evidence) != recorder.evidence:
            raise InvariantFailure("Fixture recipe returned evidence outside its recorder.")
        required = recipe.required_manifest_keys(args)
        if set(recorder.structure) != set(required):
            missing = sorted(set(required) - set(recorder.structure))
            extra = sorted(set(recorder.structure) - set(required))
            raise InvariantFailure(
                f"Fixture manifest keys do not match recipe declaration; missing={missing}, extra={extra}."
            )
        snapshot = await capture_snapshot(client, str(notebook["id"]))
        write_json(options.run_dir / "prepared.json", snapshot)
        write_json(options.run_dir / "fixture-snapshot.json", snapshot)
        write_json(options.run_dir / "page-hashes.json", snapshot.get("page_hashes", {}))
        pending_result = _fixture_result(
            scenario.name,
            notebook,
            recorder,
            passed=False,
        )
        write_json(options.run_dir / "fixture-result.json", pending_result)
        checks = recipe.validate(
            FixtureValidationContext(args=args, snapshot=snapshot),
            FixtureBuildResult(recorder.structure, recorder.evidence),
        )
    except Exception as exc:
        _persist_failure(scenario.name, notebook, recorder, exc)
        raise

    manifest = recorder.persist("passed")
    _record_run_identity(manifest, args)
    manifest["fixture_validation"] = {"status": "passed", "checks": list(checks)}
    write_json(options.run_dir / "manifest.json", manifest)
    result = _fixture_result(
        scenario.name,
        notebook,
        recorder,
        passed=True,
        checks=checks,
    )
    write_json(options.run_dir / "fixture-result.json", result)
    return manifest, result


def _role_snapshot_path(run_dir: Path, role: str) -> Path:
    return run_dir / f"fixture-snapshot-{role}.json"


def bundle_cache_artifacts(
    run_dir: Path,
    roles: tuple[str, ...],
    manifest: Mapping[str, Any],
    fixture_result: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    role_structures = manifest.get("role_structures", {})
    role_results = fixture_result.get("roles", {})
    notebooks = manifest.get("notebooks", {})
    artifacts: dict[str, dict[str, Any]] = {}
    for role in roles:
        role_manifest = dict(manifest)
        role_manifest["notebook"] = dict(notebooks[role])
        role_manifest["notebook_role"] = role
        role_manifest["structure"] = dict(role_structures[role])
        artifacts[role] = {
            "manifest": role_manifest,
            "fixture_result": dict(role_results[role]),
            "snapshot": read_json(
                _role_snapshot_path(run_dir, role)
                if _role_snapshot_path(run_dir, role).exists()
                else run_dir / "fixture-snapshot.json"
            ),
        }
    return artifacts


async def prepare_fixture_bundle(
    scenario: Scenario,
    args: argparse.Namespace,
    options: RuntimeOptions,
    client: MCPStdioClient,
    notebooks: Mapping[str, dict[str, Any]],
    notebook_paths: Mapping[str, str],
    spec: ScenarioSpec,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build and validate every declared Notebook role as one atomic fixture bundle."""

    recipe = scenario.fixture_recipe
    roles = tuple(role.role for role in recipe.cache_identity.notebook_roles)
    if set(notebooks) != set(roles) or set(notebook_paths) != set(roles):
        raise InvariantFailure("Fixture bundle runtime did not receive every declared role.")
    recorders: dict[str, FixtureRecorder] = {}
    builds: dict[str, FixtureBuildResult] = {}
    snapshots: dict[str, Mapping[str, Any]] = {}
    observations: dict[str, FixtureRoleObservation] = {}
    token = str(uuid.uuid4())
    try:
        for role in roles:
            recorder = FixtureRecorder(
                run_dir=options.run_dir,
                notebook=notebooks[role],
                notebook_path=notebook_paths[role],
                spec=spec,
                allowed_keys=recipe.manifest_keys_for_role(role, args),
                role=role,
            )
            recorders[role] = recorder
            recorder.persist("pending")
            context = FixtureContext(
                args=args,
                options=options,
                client=client,
                notebook=notebooks[role],
                notebook_path=notebook_paths[role],
                spec=spec,
                token=token,
                recorder=recorder,
                role=role,
                notebooks=notebooks,
                notebook_paths=notebook_paths,
            )
            if (
                recipe.build_mode == BuildMode.HUMAN_BOOTSTRAP_REQUIRED
                and getattr(recipe, "bootstrap_scenario_name", None) == scenario.name
            ):
                build = await recipe.build_scaffold(context)
            else:
                build = await recipe.build(context)
            if dict(build.structure) != recorder.structure or dict(build.evidence) != recorder.evidence:
                raise InvariantFailure(f"Fixture role {role} returned data outside its recorder.")
            expected_keys = recipe.manifest_keys_for_role(role, args)
            if set(recorder.structure) != set(expected_keys):
                raise InvariantFailure(
                    f"Fixture role {role} manifest keys differ; "
                    f"missing={sorted(expected_keys - set(recorder.structure))}, "
                    f"extra={sorted(set(recorder.structure) - expected_keys)}."
                )
            snapshot = await capture_snapshot(client, str(notebooks[role]["id"]))
            snapshots[role] = snapshot
            builds[role] = build
            observations[role] = FixtureRoleObservation(
                role=role,
                args=args,
                notebook=notebooks[role],
                notebook_path=notebook_paths[role],
                snapshot=snapshot,
                build=build,
            )
            write_json(_role_snapshot_path(options.run_dir, role), snapshot)
        report = recipe.validate_live(FixtureBundleObservation(roles=observations))
    except Exception as exc:
        for role, recorder in recorders.items():
            _persist_failure(scenario.name, notebooks[role], recorder, exc)
        write_json(
            options.run_dir / "fixture-bundle-failure.json",
            {
                "schema_version": 1,
                "scenario": scenario.name,
                "error": f"{type(exc).__name__}: {exc}",
                "roles": {
                    role: {
                        "notebook": stable_item(notebooks[role]),
                        "notebook_path": str(Path(notebook_paths[role]).resolve()),
                        "structure_ids": {
                            key: str(value["id"])
                            for key, value in recorders[role].structure.items()
                        },
                    }
                    for role in roles
                    if role in recorders
                },
                "bundle_preserved": True,
                "filesystem_deleted": False,
            },
        )
        raise

    source_recorder = recorders["source"]
    manifest = source_recorder.manifest("passed")
    role_structures = {
        role: {key: stable_item(value) for key, value in builds[role].structure.items()}
        for role in roles
    }
    combined_structure = {
        key: value
        for role in roles
        for key, value in role_structures[role].items()
    }
    if len(combined_structure) != sum(len(value) for value in role_structures.values()):
        raise InvariantFailure("Fixture bundle roles declared duplicate manifest keys.")
    manifest.update(
        notebook=stable_item(notebooks["source"]),
        notebooks={role: stable_item(notebooks[role]) for role in roles},
        notebook_paths={role: str(Path(notebook_paths[role]).resolve()) for role in roles},
        lifecycle_leases={
            role: str(
                (
                    options.run_dir
                    / ("lifecycle-lease.json" if role == "source" else f"lifecycle-lease-{role}.json")
                ).resolve()
            )
            for role in roles
        },
        structure=combined_structure,
        role_structures=role_structures,
        fixture_validation={
            "status": "passed",
            "passed": report.passed,
            "role_checks": {
                role: list(report.role_checks[role]) for role in roles
            },
            "bundle_checks": list(report.bundle_checks),
        },
    )
    manifest["disposable_targets"].update(
        {
            f"{role}_notebook_path": str(Path(notebook_paths[role]).resolve())
            for role in roles
        }
    )
    manifest["scenario_spec"]["fixture_profile"]["actual_manifest_keys"] = sorted(
        combined_structure
    )
    _record_run_identity(manifest, args)
    result = {
        "scenario": scenario.name,
        "notebook": stable_item(notebooks["source"]),
        "notebooks": {role: stable_item(notebooks[role]) for role in roles},
        "structure_ids": {key: str(value["id"]) for key, value in combined_structure.items()},
        "roles": {
            role: {
                "scenario": scenario.name,
                "notebook": stable_item(notebooks[role]),
                "structure_ids": {
                    key: str(value["id"]) for key, value in role_structures[role].items()
                },
                "validation": {
                    "passed": True,
                    "checks": list(report.role_checks[role]),
                },
            }
            for role in roles
        },
        "validation": {
            "passed": True,
            "role_checks": {
                role: list(report.role_checks[role]) for role in roles
            },
            "bundle_checks": list(report.bundle_checks),
        },
    }
    write_json(options.run_dir / "prepared.json", snapshots["source"])
    write_json(options.run_dir / "fixture-snapshot.json", snapshots["source"])
    write_json(options.run_dir / "page-hashes.json", snapshots["source"].get("page_hashes", {}))
    write_json(options.run_dir / "manifest.json", manifest)
    write_json(options.run_dir / "fixture-result.json", result)
    return manifest, result


async def prepare_reopened_fixture_bundle(
    scenario: Scenario,
    args: argparse.Namespace,
    options: RuntimeOptions,
    client: MCPStdioClient,
    notebooks: Mapping[str, dict[str, Any]],
    notebook_paths: Mapping[str, str],
    prior_manifest: Mapping[str, Any],
    prior_result: Mapping[str, Any],
    close_results: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebind and validate a fixture after a real close/reopen persistence checkpoint."""

    recipe = scenario.fixture_recipe
    roles = tuple(role.role for role in recipe.cache_identity.notebook_roles)
    prior_notebooks = prior_manifest.get("notebooks")
    prior_structures = prior_manifest.get("role_structures")
    if (
        set(notebooks) != set(roles)
        or set(notebook_paths) != set(roles)
        or set(close_results) != set(roles)
        or not isinstance(prior_notebooks, Mapping)
        or not isinstance(prior_structures, Mapping)
    ):
        raise InvariantFailure(
            "Persistence checkpoint did not receive the complete declared fixture bundle."
        )

    observations: dict[str, FixtureRoleObservation] = {}
    role_structures: dict[str, dict[str, dict[str, Any]]] = {}
    role_evidence: dict[str, dict[str, Any]] = {}
    snapshots: dict[str, Mapping[str, Any]] = {}
    remaps: dict[str, Any] = {}
    for role in roles:
        source_notebook = prior_notebooks.get(role)
        source_structure = prior_structures.get(role)
        if not isinstance(source_notebook, Mapping) or not isinstance(
            source_structure, dict
        ):
            raise InvariantFailure(
                f"Persistence checkpoint source metadata is incomplete for role {role}."
            )
        try:
            snapshot = await capture_snapshot(client, str(notebooks[role]["id"]))
        except Exception as exc:
            write_json(
                options.run_dir / "fixture-persistence-checkpoint-failure.json",
                {
                    "schema_version": 1,
                    "status": "failed",
                    "phase": "post_reopen_snapshot",
                    "role": role,
                    "error": f"{type(exc).__name__}: {exc}",
                    "mutation_attempted": False,
                    "bundle_preserved": True,
                    "filesystem_deleted": False,
                },
            )
            raise
        rebound, remap = _rebind_materialized_structure(
            source_structure,
            source_notebook=source_notebook,
            working_notebook=notebooks[role],
            snapshot=snapshot,
        )
        evidence_source = (
            {
                key: prior_manifest[key]
                for key in ("copy_fixture", "reparent_page_fixture")
                if key in prior_manifest
            }
            if role == "source"
            else {}
        )
        evidence, evidence_remap = _rebind_materialized_evidence(
            source_structure,
            rebound,
            evidence_source,
        )
        remap["evidence_rebinding"] = evidence_remap
        remap["passed"] = (
            remap.get("passed") is True and evidence_remap.get("passed") is True
        )
        remaps[role] = remap
        if remap["passed"] is not True:
            write_json(
                options.run_dir / "fixture-persistence-remap.json",
                {
                    "schema_version": 1,
                    "status": "failed",
                    "passed": False,
                    "roles": remaps,
                    "mutation_attempted": False,
                    "filesystem_deleted": False,
                },
            )
            raise InvariantFailure(
                f"Persisted fixture role {role} could not be uniquely rebound to live IDs."
            )
        build = FixtureBuildResult(rebound, evidence)
        role_structures[role] = rebound
        role_evidence[role] = evidence
        snapshots[role] = snapshot
        observations[role] = FixtureRoleObservation(
            role=role,
            args=args,
            notebook=notebooks[role],
            notebook_path=notebook_paths[role],
            snapshot=snapshot,
            build=build,
        )
        write_json(_role_snapshot_path(options.run_dir, role), snapshot)

    write_json(
        options.run_dir / "fixture-persistence-remap.json",
        {
            "schema_version": 1,
            "status": "passed",
            "passed": True,
            "roles": remaps,
            "mutation_attempted": False,
            "filesystem_deleted": False,
        },
    )
    try:
        report = recipe.validate_live(FixtureBundleObservation(roles=observations))
    except Exception as exc:
        write_json(
            options.run_dir / "fixture-persistence-checkpoint-failure.json",
            {
                "schema_version": 1,
                "status": "failed",
                "phase": "post_reopen_live_validation",
                "error": f"{type(exc).__name__}: {exc}",
                "remap_evidence": str(
                    (options.run_dir / "fixture-persistence-remap.json").resolve()
                ),
                "mutation_attempted": False,
                "bundle_preserved": True,
                "filesystem_deleted": False,
            },
        )
        raise
    combined_structure = {
        key: stable_item(value)
        for role in roles
        for key, value in role_structures[role].items()
    }
    manifest = deepcopy(dict(prior_manifest))
    manifest.update(
        notebook=stable_item(notebooks["source"]),
        notebooks={role: stable_item(notebooks[role]) for role in roles},
        notebook_paths={
            role: str(Path(notebook_paths[role]).resolve()) for role in roles
        },
        structure=combined_structure,
        role_structures={
            role: {
                key: stable_item(value)
                for key, value in role_structures[role].items()
            }
            for role in roles
        },
        fixture_validation={
            "status": "passed",
            "passed": report.passed,
            "role_checks": {
                role: list(report.role_checks[role]) for role in roles
            },
            "bundle_checks": list(report.bundle_checks),
            "post_close_reopen_revalidation": True,
        },
        fixture_persistence_checkpoint={
            "schema_version": 1,
            "status": "passed",
            "close_force": False,
            "close_results": deepcopy(dict(close_results)),
            "reopened_notebook_ids": {
                role: str(notebooks[role]["id"]) for role in roles
            },
            "remap_evidence": str(
                (options.run_dir / "fixture-persistence-remap.json").resolve()
            ),
            "full_live_validation": True,
            "mutation_attempted": False,
            "filesystem_deleted": False,
        },
    )
    manifest.update(role_evidence.get("source", {}))
    manifest.setdefault("disposable_targets", {}).update(
        {
            f"{role}_notebook_path": str(Path(notebook_paths[role]).resolve())
            for role in roles
        }
    )
    _record_run_identity(manifest, args)

    previous_roles = prior_result.get("roles", {})
    result = deepcopy(dict(prior_result))
    result.update(
        notebook=stable_item(notebooks["source"]),
        notebooks={role: stable_item(notebooks[role]) for role in roles},
        structure_ids={
            key: str(value["id"]) for key, value in combined_structure.items()
        },
        roles={
            role: {
                **(
                    dict(previous_roles.get(role, {}))
                    if isinstance(previous_roles, Mapping)
                    and isinstance(previous_roles.get(role), Mapping)
                    else {}
                ),
                "notebook": stable_item(notebooks[role]),
                "structure_ids": {
                    key: str(value["id"])
                    for key, value in role_structures[role].items()
                },
                "validation": {
                    "passed": True,
                    "checks": list(report.role_checks[role]),
                    "post_close_reopen_revalidation": True,
                },
            }
            for role in roles
        },
        validation={
            "passed": True,
            "role_checks": {
                role: list(report.role_checks[role]) for role in roles
            },
            "bundle_checks": list(report.bundle_checks),
            "post_close_reopen_revalidation": True,
        },
    )
    write_json(options.run_dir / "prepared.json", snapshots["source"])
    write_json(options.run_dir / "fixture-snapshot.json", snapshots["source"])
    write_json(
        options.run_dir / "page-hashes.json",
        snapshots["source"].get("page_hashes", {}),
    )
    write_json(options.run_dir / "manifest.json", manifest)
    write_json(options.run_dir / "fixture-result.json", result)
    return manifest, result


async def prepare_materialized_fixture(
    scenario: Scenario,
    args: argparse.Namespace,
    options: RuntimeOptions,
    client: MCPStdioClient,
    notebook: dict[str, Any],
    notebook_path: str,
    spec: ScenarioSpec,
    hit: CacheHit,
    materialized: MaterializedBundle,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Re-observe and revalidate a materialized working copy before mutation."""

    if tuple(hit.entry.get("roles", ())) != ("source",):
        raise InvariantFailure("Single-Notebook scenario received a non-source cache bundle.")
    artifact_root = hit.entry_path / "notebooks" / "source"
    manifest = read_json(artifact_root / "template-manifest.json")
    cached_result = read_json(artifact_root / "template-fixture-result.json")
    structure = manifest.get("structure")
    if not isinstance(structure, dict):
        raise InvariantFailure("Cached fixture manifest has no typed structure.")
    cached_evidence = {
        key: manifest[key]
        for key in ("copy_fixture", "reparent_page_fixture")
        if key in manifest
    }
    source_structure = structure
    options.progress.unit_started("cache hierarchy", "source")
    (
        converged_snapshot,
        converged_structure,
        _converged_remap,
        convergence,
    ) = await _await_materialized_structure_convergence(
        client,
        role="source",
        structure=source_structure,
        source_notebook=manifest.get("notebook", {}),
        working_notebook=notebook,
    )
    convergence_path = options.run_dir / "cache-hierarchy-convergence.json"

    def persist_convergence() -> None:
        write_json(
            convergence_path,
            {
                "schema_version": 1,
                "passed": convergence.get("passed") is True
                and convergence.get("full_content_validation_completed") is True,
                "roles": {"source": convergence},
            },
        )

    persist_convergence()
    if convergence.get("passed") is not True or converged_snapshot is None:
        raise InvariantFailure(
            "Materialized fixture hierarchy convergence failed: "
            f"{convergence.get('error', 'declared hierarchy was not stable')}."
        )
    options.progress.unit_completed("cache hierarchy", "source")
    options.progress.unit_started("cache content", "source")
    convergence["full_content_validation_started"] = True
    persist_convergence()
    try:
        snapshot = await capture_snapshot(client, str(notebook["id"]))
    except Exception as exc:
        convergence.update(
            passed=False,
            phase="full_content_validation",
            full_content_validation_completed=False,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        persist_convergence()
        raise InvariantFailure(
            "Materialized fixture full content validation failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    structure, remap = _rebind_materialized_structure(
        source_structure,
        source_notebook=manifest.get("notebook", {}),
        working_notebook=notebook,
        snapshot=snapshot,
    )
    hierarchy_changed = (
        remap["passed"] is True
        and _materialized_structure_signature(structure)
        != _materialized_structure_signature(converged_structure)
    )
    if remap["passed"] is not True or hierarchy_changed:
        convergence.update(
            passed=False,
            phase="full_content_validation",
            full_content_validation_completed=True,
            error=(
                "declared hierarchy changed after convergence"
                if hierarchy_changed
                else "declared hierarchy was incomplete during full content validation"
            ),
        )
        persist_convergence()
        write_json(options.run_dir / "cache-structure-remap.json", remap)
        raise InvariantFailure(
            "Materialized fixture full content validation could not preserve and uniquely "
            "rebind the stable hierarchy: "
            + ", ".join(
                f"{value['manifest_key']}={value['reason']}"
                for value in remap["failures"]
            )
        )
    convergence.update(
        passed=True,
        phase="full_content_validation",
        full_content_validation_completed=True,
    )
    persist_convergence()
    options.progress.unit_completed("cache content", "source")
    evidence, evidence_remap = _rebind_materialized_evidence(
        manifest.get("structure", {}),
        structure,
        cached_evidence,
    )
    remap["evidence_rebinding"] = evidence_remap
    remap["passed"] = evidence_remap["passed"] is True
    write_json(options.run_dir / "cache-structure-remap.json", remap)
    if evidence_remap["passed"] is not True:
        raise InvariantFailure(
            "Materialized fixture evidence could not be safely rebound to live IDs: "
            + ", ".join(
                f"{value['field']}={value['reason']}"
                for value in evidence_remap["failures"]
            )
        )
    build = FixtureBuildResult(structure, evidence)
    checks = scenario.fixture_recipe.validate(
        FixtureValidationContext(args=args, snapshot=snapshot),
        build,
    )
    interactive_validation: dict[str, Any] | None = None
    if scenario.fixture_recipe.build_mode == BuildMode.HUMAN_BOOTSTRAP_REQUIRED:
        observation = FixtureBundleObservation(
            roles={
                "source": FixtureRoleObservation(
                    role="source",
                    args=args,
                    notebook=notebook,
                    notebook_path=notebook_path,
                    snapshot=snapshot,
                    build=build,
                )
            }
        )
        interactive_validation = scenario.fixture_recipe.validate_authored_content(observation)
        if isinstance(scenario.fixture_recipe, UserAuthoredRecipe):
            frozen = scenario.fixture_recipe.freeze_authored_instance(observation)
            _assert_authored_cache_identity(hit, frozen)
    manifest["run_id"] = options.run_dir.name
    manifest["notebook"] = stable_item(notebook)
    manifest["structure"] = {
        key: stable_item(value) for key, value in structure.items()
    }
    manifest.update(evidence)
    manifest["disposable_targets"]["source_notebook_path"] = str(
        Path(notebook_path).resolve()
    )
    manifest["lifecycle_lease"] = str(
        (options.run_dir / "lifecycle-lease.json").resolve()
    )
    manifest["fixture_validation"] = {
        "status": "passed",
        "checks": list(checks),
        "live_materialized_revalidation": True,
    }
    manifest["fixture_cache"] = {
        "cache_mode": "use_cache",
        "decision": "validated_hit",
        "fingerprint": hit.fingerprint,
        "template_instance_id": hit.template_instance_id,
        "role": "source",
        "template_path": str(materialized.template_paths["source"]),
        "working_path": str(materialized.working_paths["source"]),
        "opened_template": False,
    }
    _record_run_identity(manifest, args)
    if interactive_validation is not None:
        manifest["fixture_cache"]["interactive_live_validation"] = interactive_validation
    result = dict(cached_result)
    result["notebook"] = stable_item(notebook)
    result["structure_ids"] = {
        key: str(value["id"]) for key, value in structure.items()
    }
    result["validation"] = {
        "passed": True,
        "checks": list(checks),
        "live_materialized_revalidation": True,
    }
    write_json(options.run_dir / "prepared.json", snapshot)
    write_json(options.run_dir / "fixture-snapshot.json", snapshot)
    write_json(options.run_dir / "page-hashes.json", snapshot.get("page_hashes", {}))
    write_json(options.run_dir / "manifest.json", manifest)
    write_json(options.run_dir / "fixture-result.json", result)
    return manifest, result


def _materialized_structure_signature(
    structure: Mapping[str, Mapping[str, Any]],
) -> tuple[tuple[Any, ...], ...]:
    """Return the content-free hierarchy identity that must settle before Page reads."""

    fields = (
        "resource_type",
        "id",
        "path",
        "parent_id",
        "section_id",
        "page_level",
        "parent_page_id",
        "order",
    )
    return tuple(
        (key, *(item.get(field) for field in fields))
        for key, item in sorted(structure.items())
    )


async def _await_materialized_structure_convergence(
    client: MCPStdioClient,
    *,
    role: str,
    structure: Mapping[str, Any],
    source_notebook: Mapping[str, Any],
    working_notebook: Mapping[str, Any],
    max_observations: int = MATERIALIZED_STRUCTURE_MAX_OBSERVATIONS,
    stable_observations: int = MATERIALIZED_STRUCTURE_STABLE_OBSERVATIONS,
    delay_seconds: float = MATERIALIZED_STRUCTURE_OBSERVATION_DELAY_SECONDS,
) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Wait for every manifest-bound hierarchy object to be present and stable."""

    if max_observations < stable_observations or stable_observations < 2:
        raise ValueError("Materialized hierarchy convergence requires at least two observations.")
    if delay_seconds < 0:
        raise ValueError("Materialized hierarchy convergence delay cannot be negative.")

    prior_signature: tuple[tuple[Any, ...], ...] | None = None
    stable_count = 0
    observations: list[dict[str, Any]] = []
    latest_snapshot: dict[str, Any] | None = None
    latest_rebound: dict[str, dict[str, Any]] = {}
    latest_remap: dict[str, Any] = {
        "schema_version": 1,
        "mappings": [],
        "failures": [{"reason": "not-observed"}],
        "passed": False,
    }
    deterministic_error: Exception | None = None

    for attempt in range(1, max_observations + 1):
        observation: dict[str, Any] = {"attempt": attempt}
        try:
            tree_result = await client.call_tool(
                "get_tree",
                {"root_id": str(working_notebook["id"]), "max_depth": 8},
            )
            tree = tree_result.get("tree") if isinstance(tree_result, Mapping) else None
            if not isinstance(tree, dict):
                raise InvariantFailure(
                    "Materialized hierarchy observation returned no typed tree."
                )
            latest_snapshot = {
                "notebook_id": str(working_notebook["id"]),
                "items": [stable_item(item) for item in _flatten_materialized_tree(tree)],
            }
            latest_rebound, latest_remap = _rebind_materialized_structure(
                dict(structure),
                source_notebook=source_notebook,
                working_notebook=working_notebook,
                snapshot=latest_snapshot,
            )
            observation.update(
                structure_complete=latest_remap.get("passed") is True,
                mapping_count=len(latest_remap.get("mappings", ())),
                failures=list(latest_remap.get("failures", ())),
            )
            if latest_remap.get("passed") is True:
                signature = _materialized_structure_signature(latest_rebound)
                stable_count = stable_count + 1 if signature == prior_signature else 1
                prior_signature = signature
            else:
                prior_signature = None
                stable_count = 0
        except InvariantFailure as exc:
            deterministic_error = exc
            stable_count = 0
            observation.update(
                structure_complete=False,
                error_type=type(exc).__name__,
                error=str(exc),
            )
        except Exception as exc:  # transient MCP/COM read failure; bounded below
            stable_count = 0
            prior_signature = None
            observation.update(
                structure_complete=False,
                error_type=type(exc).__name__,
                error=str(exc),
            )
        observation["stable_observations"] = stable_count
        observations.append(observation)
        if stable_count >= stable_observations:
            return (
                latest_snapshot,
                latest_rebound,
                latest_remap,
                {
                    "schema_version": 1,
                    "role": role,
                    "phase": "hierarchy_convergence",
                    "passed": True,
                    "attempts": attempt,
                    "required_stable_observations": stable_observations,
                    "stable_observations": stable_count,
                    "declared_object_count": len(structure),
                    "observations": observations,
                    "full_content_validation_started": False,
                    "full_content_validation_completed": False,
                },
            )
        if deterministic_error is not None:
            break
        if attempt < max_observations and delay_seconds:
            await asyncio.sleep(delay_seconds)

    return (
        latest_snapshot,
        latest_rebound,
        latest_remap,
        {
            "schema_version": 1,
            "role": role,
            "phase": "hierarchy_convergence",
            "passed": False,
            "attempts": len(observations),
            "required_stable_observations": stable_observations,
            "stable_observations": stable_count,
            "declared_object_count": len(structure),
            "observations": observations,
            "full_content_validation_started": False,
            "full_content_validation_completed": False,
            "error": (
                str(deterministic_error)
                if deterministic_error is not None
                else "deadline exceeded before the declared hierarchy was stable"
            ),
        },
    )


def _flatten_materialized_tree(tree: Mapping[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def visit(node: Mapping[str, Any]) -> None:
        item = node.get("item")
        if isinstance(item, dict):
            items.append(item)
        children = node.get("children", ())
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    visit(child)

    visit(tree)
    return items


async def prepare_materialized_fixture_bundle(
    scenario: Scenario,
    args: argparse.Namespace,
    options: RuntimeOptions,
    client: MCPStdioClient,
    notebooks: Mapping[str, dict[str, Any]],
    notebook_paths: Mapping[str, str],
    spec: ScenarioSpec,
    hit: CacheHit,
    materialized: MaterializedBundle,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebind and live-validate every materialized role before any mutation."""

    recipe = scenario.fixture_recipe
    roles = tuple(role.role for role in recipe.cache_identity.notebook_roles)
    if tuple(hit.entry.get("roles", ())) != roles:
        raise InvariantFailure("Materialized cache role set differs from the Recipe identity.")
    if set(notebooks) != set(roles) or set(notebook_paths) != set(roles):
        raise InvariantFailure("Materialized fixture runtime did not receive every role.")
    observations: dict[str, FixtureRoleObservation] = {}
    role_structures: dict[str, dict[str, dict[str, Any]]] = {}
    snapshots: dict[str, Mapping[str, Any]] = {}
    remaps: dict[str, Any] = {}
    convergence_reports: dict[str, Any] = {}
    source_manifest: dict[str, Any] | None = None
    cached_results: dict[str, Mapping[str, Any]] = {}
    convergence_path = options.run_dir / "cache-hierarchy-convergence.json"

    def persist_convergence() -> None:
        write_json(
            convergence_path,
            {
                "schema_version": 1,
                "passed": set(convergence_reports) == set(roles)
                and all(
                    value.get("passed") is True
                    and value.get("full_content_validation_completed") is True
                    for value in convergence_reports.values()
                ),
                "roles": convergence_reports,
            },
        )

    for role_index, role in enumerate(roles, start=1):
        artifact_root = hit.entry_path / "notebooks" / role
        cached_manifest = read_json(artifact_root / "template-manifest.json")
        cached_results[role] = read_json(artifact_root / "template-fixture-result.json")
        if role == "source":
            source_manifest = cached_manifest
        structure = cached_manifest.get("structure")
        if not isinstance(structure, dict):
            raise InvariantFailure(f"Cached fixture role {role} has no typed structure.")
        options.progress.unit_started("cache hierarchy", role, role_index, len(roles))
        (
            converged_snapshot,
            converged_rebound,
            _converged_remap,
            convergence,
        ) = await _await_materialized_structure_convergence(
            client,
            role=role,
            structure=structure,
            source_notebook=cached_manifest.get("notebook", {}),
            working_notebook=notebooks[role],
        )
        convergence_reports[role] = convergence
        persist_convergence()
        if convergence.get("passed") is not True or converged_snapshot is None:
            raise InvariantFailure(
                f"Materialized fixture role {role} hierarchy convergence failed: "
                f"{convergence.get('error', 'declared hierarchy was not stable')}."
            )
        options.progress.unit_completed(
            "cache hierarchy", role, role_index, len(roles)
        )
        options.progress.unit_started("cache content", role, role_index, len(roles))
        convergence["full_content_validation_started"] = True
        persist_convergence()
        try:
            snapshot = await capture_snapshot(client, str(notebooks[role]["id"]))
        except Exception as exc:
            convergence.update(
                passed=False,
                phase="full_content_validation",
                full_content_validation_completed=False,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            persist_convergence()
            raise InvariantFailure(
                f"Materialized fixture role {role} full content validation failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        rebound, remap = _rebind_materialized_structure(
            structure,
            source_notebook=cached_manifest.get("notebook", {}),
            working_notebook=notebooks[role],
            snapshot=snapshot,
        )
        hierarchy_changed = (
            remap["passed"] is True
            and _materialized_structure_signature(rebound)
            != _materialized_structure_signature(converged_rebound)
        )
        if remap["passed"] is not True or hierarchy_changed:
            remaps[role] = remap
            convergence.update(
                passed=False,
                phase="full_content_validation",
                full_content_validation_completed=True,
                error=(
                    "declared hierarchy changed after convergence"
                    if hierarchy_changed
                    else "declared hierarchy was incomplete during full content validation"
                ),
            )
            persist_convergence()
            write_json(
                options.run_dir / "cache-structure-remap.json",
                {
                    "schema_version": 1,
                    "passed": False,
                    "roles": remaps,
                },
            )
            raise InvariantFailure(
                f"Materialized fixture role {role} full content validation observed an "
                "incomplete or changed hierarchy."
            )
        convergence.update(
            passed=True,
            phase="full_content_validation",
            full_content_validation_completed=True,
        )
        persist_convergence()
        options.progress.unit_completed("cache content", role, role_index, len(roles))
        cached_evidence = {
            key: cached_manifest[key]
            for key in ("copy_fixture", "reparent_page_fixture")
            if key in cached_manifest
        }
        evidence, evidence_remap = _rebind_materialized_evidence(
            structure,
            rebound,
            cached_evidence,
        )
        remap["evidence_rebinding"] = evidence_remap
        remap["passed"] = evidence_remap["passed"] is True
        remaps[role] = remap
        if evidence_remap["passed"] is not True:
            write_json(
                options.run_dir / "cache-structure-remap.json",
                {
                    "schema_version": 1,
                    "passed": False,
                    "roles": remaps,
                },
            )
            raise InvariantFailure(
                f"Materialized fixture role {role} evidence could not be safely rebound to live IDs."
            )
        cached_manifest.update(evidence)
        if role == "source":
            source_manifest = cached_manifest
        build = FixtureBuildResult(rebound, evidence)
        role_structures[role] = rebound
        snapshots[role] = snapshot
        observations[role] = FixtureRoleObservation(
            role=role,
            args=args,
            notebook=notebooks[role],
            notebook_path=notebook_paths[role],
            snapshot=snapshot,
            build=build,
        )
        write_json(_role_snapshot_path(options.run_dir, role), snapshot)
    write_json(
        options.run_dir / "cache-structure-remap.json",
        {
            "schema_version": 1,
            "passed": all(value.get("passed") is True for value in remaps.values()),
            "roles": remaps,
        },
    )
    report = recipe.validate_live(FixtureBundleObservation(roles=observations))
    interactive_validation: dict[str, Any] | None = None
    if recipe.build_mode == BuildMode.HUMAN_BOOTSTRAP_REQUIRED:
        bundle_observation = FixtureBundleObservation(roles=observations)
        interactive_validation = recipe.validate_authored_content(bundle_observation)
        if isinstance(recipe, UserAuthoredRecipe):
            frozen = recipe.freeze_authored_instance(bundle_observation)
            _assert_authored_cache_identity(hit, frozen)
    if source_manifest is None:
        raise InvariantFailure("Materialized bundle has no source manifest.")
    combined_structure = {
        key: stable_item(value)
        for role in roles
        for key, value in role_structures[role].items()
    }
    manifest = dict(source_manifest)
    manifest.update(
        run_id=options.run_dir.name,
        notebook=stable_item(notebooks["source"]),
        notebooks={role: stable_item(notebooks[role]) for role in roles},
        notebook_paths={role: str(Path(notebook_paths[role]).resolve()) for role in roles},
        structure=combined_structure,
        role_structures={
            role: {key: stable_item(value) for key, value in role_structures[role].items()}
            for role in roles
        },
        lifecycle_leases={
            role: str(
                (
                    options.run_dir
                    / ("lifecycle-lease.json" if role == "source" else f"lifecycle-lease-{role}.json")
                ).resolve()
            )
            for role in roles
        },
        fixture_validation={
            "status": "passed",
            "passed": report.passed,
            "role_checks": {
                role: list(report.role_checks[role]) for role in roles
            },
            "bundle_checks": list(report.bundle_checks),
            "live_materialized_revalidation": True,
        },
        fixture_cache={
            "cache_mode": "use_cache",
            "decision": "validated_hit",
            "fingerprint": hit.fingerprint,
            "template_instance_id": hit.template_instance_id,
            "roles": {
                role: {
                    "template_path": str(materialized.template_paths[role]),
                    "working_path": str(materialized.working_paths[role]),
                    "opened_template": False,
                }
                for role in roles
            },
            "opened_template": False,
        },
    )
    manifest.setdefault("disposable_targets", {}).update(
        {
            f"{role}_notebook_path": str(Path(notebook_paths[role]).resolve())
            for role in roles
        }
    )
    if interactive_validation is not None:
        manifest["fixture_cache"]["interactive_live_validation"] = interactive_validation
    _record_run_identity(manifest, args)
    result = {
        "scenario": scenario.name,
        "notebook": stable_item(notebooks["source"]),
        "notebooks": {role: stable_item(notebooks[role]) for role in roles},
        "structure_ids": {key: str(value["id"]) for key, value in combined_structure.items()},
        "roles": {
            role: {
                **dict(cached_results[role]),
                "notebook": stable_item(notebooks[role]),
                "structure_ids": {
                    key: str(value["id"]) for key, value in role_structures[role].items()
                },
                "validation": {
                    "passed": True,
                    "checks": list(report.role_checks[role]),
                    "live_materialized_revalidation": True,
                },
            }
            for role in roles
        },
        "validation": {
            "passed": True,
            "role_checks": {
                role: list(report.role_checks[role]) for role in roles
            },
            "bundle_checks": list(report.bundle_checks),
            "live_materialized_revalidation": True,
        },
    }
    write_json(options.run_dir / "prepared.json", snapshots["source"])
    write_json(options.run_dir / "fixture-snapshot.json", snapshots["source"])
    write_json(options.run_dir / "page-hashes.json", snapshots["source"].get("page_hashes", {}))
    write_json(options.run_dir / "manifest.json", manifest)
    write_json(options.run_dir / "fixture-result.json", result)
    return manifest, result


def _rebind_materialized_structure(
    structure: dict[str, Any],
    *,
    source_notebook: Mapping[str, Any],
    working_notebook: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Bind cached manifest keys to unique live IDs by an exact relative hierarchy address."""

    def address(item: Mapping[str, Any], root: Mapping[str, Any]) -> tuple[Any, ...]:
        item_path = str(item.get("path", ""))
        root_path = str(root.get("path") or root.get("name") or "")
        prefix = f"{root_path}/"
        if not item_path or not root_path or not item_path.casefold().startswith(
            prefix.casefold()
        ):
            raise InvariantFailure("Materialized structure has no exact Notebook-relative path.")
        relative = item_path[len(prefix) :].casefold()
        resource_type = str(item.get("resource_type", ""))
        page_position = (
            int(item.get("order", -1)),
            int(item.get("page_level", -1)),
        ) if resource_type == "page" else ()
        return (resource_type, relative, *page_position)

    live_by_address: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for item in snapshot.get("items", ()):
        if not isinstance(item, dict) or item.get("resource_type") == "notebook":
            continue
        key = address(item, working_notebook)
        live_by_address.setdefault(key, []).append(item)

    rebound: dict[str, dict[str, Any]] = {}
    mappings: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for manifest_key, declared in structure.items():
        if not isinstance(declared, dict):
            failures.append({"manifest_key": manifest_key, "reason": "invalid-declaration"})
            continue
        key = address(declared, source_notebook)
        matches = live_by_address.get(key, [])
        if len(matches) != 1:
            failures.append(
                {
                    "manifest_key": manifest_key,
                    "relative_path": key[1],
                    "resource_type": key[0],
                    "match_count": len(matches),
                    "reason": "missing" if not matches else "ambiguous",
                }
            )
            continue
        live = stable_item(matches[0])
        rebound[manifest_key] = live
        mappings.append(
            {
                "manifest_key": manifest_key,
                "relative_path": key[1],
                "resource_type": key[0],
                "source_id": str(declared.get("id", "")),
                "working_id": str(live.get("id", "")),
                "id_changed": str(declared.get("id", "")) != str(live.get("id", "")),
            }
        )
    report = {
        "schema_version": 1,
        "source_notebook_id": str(source_notebook.get("id", "")),
        "working_notebook_id": str(working_notebook.get("id", "")),
        "notebook_id_changed": str(source_notebook.get("id", ""))
        != str(working_notebook.get("id", "")),
        "mappings": mappings,
        "failures": failures,
        "passed": not failures and set(rebound) == set(structure),
    }
    return rebound, report


def _rebind_materialized_evidence(
    source_structure: Mapping[str, Any],
    working_structure: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebind only typed evidence ID fields that are owned by a known structure key."""

    rebound = deepcopy(dict(evidence))
    mappings: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    if "reparent_page_fixture" in rebound:
        rich = rebound["reparent_page_fixture"]
        source_target = source_structure.get("reparent_page")
        working_target = working_structure.get("reparent_page")
        source_id = (
            str(source_target.get("id", ""))
            if isinstance(source_target, Mapping)
            else ""
        )
        working_id = (
            str(working_target.get("id", ""))
            if isinstance(working_target, Mapping)
            else ""
        )
        if not source_id or not working_id:
            failures.append(
                {
                    "field": "reparent_page_fixture",
                    "reason": "missing-reparent-page-structure-binding",
                }
            )
        elif not isinstance(rich, dict):
            failures.append(
                {
                    "field": "reparent_page_fixture",
                    "reason": "invalid-evidence-shape",
                }
            )
        else:
            list_tag = rich.get("list_tag")
            fields = [
                ("reparent_page_fixture.page_id", rich),
                ("reparent_page_fixture.list_tag.page_id", list_tag),
            ]
            for field, owner in fields:
                observed = owner.get("page_id") if isinstance(owner, dict) else None
                if not isinstance(owner, dict):
                    failures.append({"field": field, "reason": "invalid-evidence-shape"})
                    continue
                if str(observed or "") != source_id:
                    failures.append(
                        {
                            "field": field,
                            "reason": "source-id-mismatch",
                            "expected_source_id": source_id,
                            "observed_source_id": str(observed or ""),
                        }
                    )
                    continue
                owner["page_id"] = working_id
                mappings.append(
                    {
                        "field": field,
                        "manifest_key": "reparent_page",
                        "source_id": source_id,
                        "working_id": working_id,
                        "id_changed": source_id != working_id,
                    }
                )

    return rebound, {
        "schema_version": 1,
        "mappings": mappings,
        "failures": failures,
        "passed": not failures,
    }


__all__ = [
    "_rebind_materialized_evidence",
    "_rebind_materialized_structure",
    "bundle_cache_artifacts",
    "prepare_fixture",
    "prepare_fixture_bundle",
    "prepare_materialized_fixture",
    "prepare_materialized_fixture_bundle",
    "prepare_reopened_fixture_bundle",
]
