"""Branch-free orchestration for scenario-owned fixture recipes."""

from __future__ import annotations

import argparse
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
    evidence = {
        key: manifest[key]
        for key in ("copy_fixture", "reparent_page_fixture")
        if isinstance(manifest.get(key), dict)
    }
    snapshot = await capture_snapshot(client, str(notebook["id"]))
    structure, remap = _rebind_materialized_structure(
        structure,
        source_notebook=manifest.get("notebook", {}),
        working_notebook=notebook,
        snapshot=snapshot,
    )
    write_json(options.run_dir / "cache-structure-remap.json", remap)
    if remap["passed"] is not True:
        raise InvariantFailure(
            "Materialized fixture structure could not be uniquely rebound to live IDs: "
            + ", ".join(
                f"{value['manifest_key']}={value['reason']}"
                for value in remap["failures"]
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
            if frozen.template_instance_id != hit.template_instance_id:
                raise InvariantFailure("User-authored working bundle drifted from its frozen instance.")
    manifest["run_id"] = options.run_dir.name
    manifest["notebook"] = stable_item(notebook)
    manifest["structure"] = {
        key: stable_item(value) for key, value in structure.items()
    }
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
    source_manifest: dict[str, Any] | None = None
    cached_results: dict[str, Mapping[str, Any]] = {}
    for role in roles:
        artifact_root = hit.entry_path / "notebooks" / role
        cached_manifest = read_json(artifact_root / "template-manifest.json")
        cached_results[role] = read_json(artifact_root / "template-fixture-result.json")
        if role == "source":
            source_manifest = cached_manifest
        structure = cached_manifest.get("structure")
        if not isinstance(structure, dict):
            raise InvariantFailure(f"Cached fixture role {role} has no typed structure.")
        snapshot = await capture_snapshot(client, str(notebooks[role]["id"]))
        rebound, remap = _rebind_materialized_structure(
            structure,
            source_notebook=cached_manifest.get("notebook", {}),
            working_notebook=notebooks[role],
            snapshot=snapshot,
        )
        remaps[role] = remap
        if remap["passed"] is not True:
            raise InvariantFailure(
                f"Materialized fixture role {role} could not be uniquely rebound to live IDs."
            )
        evidence = {
            key: cached_manifest[key]
            for key in ("copy_fixture", "reparent_page_fixture")
            if isinstance(cached_manifest.get(key), dict)
        }
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
            if frozen.template_instance_id != hit.template_instance_id:
                raise InvariantFailure(
                    "User-authored working bundle drifted from its frozen instance."
                )
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


__all__ = [
    "_rebind_materialized_structure",
    "bundle_cache_artifacts",
    "prepare_fixture",
    "prepare_fixture_bundle",
    "prepare_materialized_fixture",
    "prepare_materialized_fixture_bundle",
]
