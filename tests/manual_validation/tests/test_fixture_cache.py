"""Temporary-filesystem contracts for the opaque local fixture bundle cache."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from tests.manual_validation import local_filesystem
from tests.manual_validation.runtime import (
    InvariantFailure,
    PathBudgetFailure,
    RunnerFailure,
    RuntimeOptions,
)
from tests.manual_validation.run_identity import (
    new_run_identity,
    validation_notebook_names,
)
from tests.manual_validation.scenarios.common.fixture_cache import (
    BundleCacheStore,
    CacheHit,
    MaterializedBundle,
    inventory_directory,
)
from tests.manual_validation.scenarios.common import fixture_cache as fixture_cache_module
from tests.manual_validation.scenarios.common import fixture_runtime as fixture_runtime_module
from tests.manual_validation.scenarios.common.fixture_runtime import (
    _await_materialized_structure_convergence,
    _assert_authored_cache_identity,
    _rebind_materialized_evidence,
    _rebind_materialized_run_local_paths,
    _rebind_materialized_structure,
    prepare_materialized_fixture,
    prepare_materialized_fixture_bundle,
)
from tests.manual_validation.scenarios.common import orchestrator as validation
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.common.orchestrator import run_validate
from tests.manual_validation.test_utils import read_json, write_json
from tests.manual_validation.scenarios.fixture_recipes.recipe_base import (
    BuildMode,
    FixtureValidationReport,
    NotebookRoleSpec,
    RecipeBase,
)


@pytest.fixture
def tmp_path(tmp_path_factory) -> Path:
    """Unique short root for deep Windows cache/materialize paths."""

    return tmp_path_factory.mktemp("fc")


@pytest.fixture(autouse=True)
def _running_onenote_gui(monkeypatch):
    monkeypatch.setattr(validation, "require_onenote_desktop", lambda: None)


def _source(tmp_path: Path, role: str = "source") -> Path:
    root = tmp_path / "closed" / role
    root.mkdir(parents=True)
    (root / "Open Notebook.onetoc2").write_bytes(b"opaque-catalog")
    (root / "Section.one").write_bytes(b"opaque-section")
    return root


def test_materialized_bundle_stages_one_validated_scenario_before_snapshot_per_role(
    monkeypatch,
    tmp_path,
) -> None:
    roles = ("destination", "source")
    run_dir = tmp_path / "run"
    entry_path = tmp_path / "cache-entry"
    run_dir.mkdir(parents=True)
    notebooks = {
        role: {
            "id": f"working-{role}",
            "name": f"Working {role}",
            "path": str(run_dir / "notebooks" / role),
            "resource_type": "notebook",
        }
        for role in roles
    }
    notebook_paths = {role: str(run_dir / "notebooks" / role) for role in roles}
    template_paths = {role: entry_path / "notebooks" / role / "template" for role in roles}
    working_paths = {role: Path(notebook_paths[role]) for role in roles}
    cached_run_dir = tmp_path / "cached-run"
    cached_notebook_paths = {
        role: cached_run_dir / "notebooks" / f"{role}-template" for role in roles
    }
    for role in roles:
        artifact_root = entry_path / "notebooks" / role
        artifact_root.mkdir(parents=True)
        template_paths[role].mkdir()
        working_paths[role].mkdir(parents=True)
        write_json(
            artifact_root / "template-manifest.json",
            {
                "notebook": {
                    "id": f"template-{role}",
                    "name": f"Template {role}",
                    "path": str(template_paths[role]),
                    "resource_type": "notebook",
                },
                "structure": {},
                "disposable_targets": {
                    "notebook_copy_root": str(cached_run_dir / "notebook-copies"),
                    **{
                        f"{cached_role}_notebook_path": str(cached_path)
                        for cached_role, cached_path in cached_notebook_paths.items()
                    },
                },
                "lifecycle_lease": str(cached_run_dir / "lifecycle-lease.json"),
                "lifecycle_leases": {
                    cached_role: str(
                        cached_run_dir
                        / (
                            "lifecycle-lease.json"
                            if cached_role == "source"
                            else f"lifecycle-lease-{cached_role}.json"
                        )
                    )
                    for cached_role in roles
                },
                "scenario_policies": {"stale-scenario": {"writes_enabled": False}},
                "scenario_spec": {
                    "scenario": "stale-scenario",
                    "fixture_profile": {"actual_manifest_keys": ["stale"]},
                    "tool_allowlist": ["list_pages"],
                },
                "mcp_process_contract": {"maximum_starts": 99},
            },
        )
        write_json(
            artifact_root / "template-fixture-result.json",
            {"scenario": "fake-materialized"},
        )

    class FakeRecipe:
        build_mode = BuildMode.PROGRAMMATIC
        cache_identity = argparse.Namespace(
            notebook_roles=tuple(argparse.Namespace(role=role) for role in roles)
        )

        def validate_live(self, observation):
            assert set(observation.roles) == set(roles)
            return FixtureValidationReport(
                passed=True,
                role_checks={role: ("content baseline verified",) for role in roles},
                bundle_checks=("roles remain distinct",),
            )

    events: list[str] = []

    async def fake_convergence(_client, *, role, working_notebook, **_kwargs):
        events.append(f"hierarchy-{role}")
        snapshot = {
            "notebook_id": working_notebook["id"],
            "items": [],
            "page_hashes": {},
            "page_objects": {},
        }
        return snapshot, {}, {"passed": True}, {
            "schema_version": 1,
            "role": role,
            "phase": "hierarchy_convergence",
            "passed": True,
            "scenario_before_snapshot_started": False,
            "scenario_before_snapshot_completed": False,
        }

    capture_calls: list[str] = []

    async def fake_capture(_client, notebook_id):
        events.append(f"content-{notebook_id}")
        capture_calls.append(notebook_id)
        return {
            "notebook_id": notebook_id,
            "items": [],
            "page_hashes": {},
            "page_objects": {},
        }

    monkeypatch.setattr(
        fixture_runtime_module,
        "_await_materialized_structure_convergence",
        fake_convergence,
    )
    original_rebind_evidence = fixture_runtime_module._rebind_materialized_evidence

    def recording_rebind_evidence(*args, **kwargs):
        events.append("evidence-rebind")
        return original_rebind_evidence(*args, **kwargs)

    monkeypatch.setattr(
        fixture_runtime_module,
        "_rebind_materialized_evidence",
        recording_rebind_evidence,
    )
    monkeypatch.setattr(fixture_runtime_module, "capture_snapshot", fake_capture)
    scenario = argparse.Namespace(name="fake-materialized", fixture_recipe=FakeRecipe())
    current_spec = SCENARIO_REGISTRY.get("copy-page").spec
    hit = CacheHit("f" * 64, "instance", entry_path, {"roles": list(roles)})
    materialized = MaterializedBundle(
        hit.fingerprint,
        hit.template_instance_id,
        template_paths,
        working_paths,
        run_dir / "cache-materialization.json",
    )

    manifest, result = asyncio.run(
        prepare_materialized_fixture_bundle(
            scenario,
            argparse.Namespace(),
            RuntimeOptions(run_dir, 30, False, False, use_cache=True),
            object(),
            notebooks,
            notebook_paths,
            current_spec,
            hit,
            materialized,
        )
    )

    assert capture_calls == ["working-destination", "working-source"]
    assert events == [
        "hierarchy-destination",
        "evidence-rebind",
        "content-working-destination",
        "hierarchy-source",
        "evidence-rebind",
        "content-working-source",
    ]
    convergence = read_json(run_dir / "cache-hierarchy-convergence.json")
    assert convergence["passed"] is True
    assert all(
        role_report["scenario_before_snapshot_completed"] is True
        for role_report in convergence["roles"].values()
    )
    assert manifest["fixture_validation"]["scenario_before_snapshot_reused"] is True
    assert manifest["scenario_spec"] == {
        **current_spec.as_dict(),
        "fixture_profile": {
            **current_spec.fixture.as_dict(),
            "actual_manifest_keys": [],
        },
    }
    assert manifest["scenario_policies"] == {
        current_spec.name: current_spec.policy.as_dict()
    }
    assert manifest["mcp_process_contract"]["maximum_starts"] == 1
    assert manifest["fixture_cache"]["current_contract_refreshed"] is True
    assert result["validation"]["scenario_before_snapshot_reused"] is True
    assert manifest["disposable_targets"]["notebook_copy_root"] == str(
        (run_dir / "notebook-copies").resolve()
    )
    assert manifest["lifecycle_lease"] == str(
        (run_dir / "lifecycle-lease.json").resolve()
    )
    path_remap = read_json(run_dir / "cache-run-local-path-remap.json")
    assert path_remap["passed"] is True
    assert path_remap["cache_template_modified"] is False
    cached_source_after = read_json(
        entry_path / "notebooks" / "source" / "template-manifest.json"
    )
    assert cached_source_after["disposable_targets"]["notebook_copy_root"] == str(
        cached_run_dir / "notebook-copies"
    )
    assert cached_source_after["lifecycle_lease"] == str(
        cached_run_dir / "lifecycle-lease.json"
    )


def test_materialized_run_local_paths_fail_closed_on_inconsistent_copy_root(
    tmp_path,
) -> None:
    cached_run_dir = tmp_path / "cached-run"
    run_dir = tmp_path / "run"
    working = run_dir / "notebooks" / "working-source"
    manifest = {
        "disposable_targets": {
            "notebook_copy_root": str(tmp_path / "different-run" / "notebook-copies"),
            "source_notebook_path": str(
                cached_run_dir / "notebooks" / "cached-source"
            ),
        },
        "lifecycle_lease": str(cached_run_dir / "lifecycle-lease.json"),
    }

    with pytest.raises(
        InvariantFailure,
        match="Copy root is inconsistent with its source run",
    ):
        _rebind_materialized_run_local_paths(
            manifest,
            run_dir=run_dir,
            notebook_paths={"source": str(working)},
            roles=("source",),
        )

    assert manifest["disposable_targets"]["notebook_copy_root"] == str(
        tmp_path / "different-run" / "notebook-copies"
    )


def test_materialized_bundle_opens_each_role_once_before_live_validation(
    tmp_path,
) -> None:
    run_dir = tmp_path / "run"
    working_paths = {
        role: run_dir / "notebooks" / f"{role}-working"
        for role in ("source", "destination")
    }
    template_paths = {
        role: tmp_path / "cache" / f"{role}-template"
        for role in ("source", "destination")
    }
    for path in (*working_paths.values(), *template_paths.values()):
        path.mkdir(parents=True)
    materialized = MaterializedBundle(
        "f" * 64,
        "programmatic-test",
        template_paths,
        working_paths,
        run_dir / "cache-materialization.json",
    )
    calls: list[tuple[str, str, object]] = []

    class FakeStore:
        def record_opened_working_role(self, _bundle, **kwargs):
            calls.append(("record", kwargs["role"], kwargs["notebook_id"]))

    class FakeWrapper:
        def __init__(self, role: str) -> None:
            self.role = role
            self.run_dir = run_dir
            suffix = "" if role == "source" else f"-{role}"
            self.lease_path = run_dir / f"lifecycle-lease{suffix}.json"
            self.open_count = 0

        def working_notebook_open_lock(self):
            from contextlib import nullcontext

            return nullcontext()

        def snapshot_open_notebooks(self):
            calls.append(("snapshot", self.role, self.open_count))
            return {}

        def assert_no_active_working_conflict(self, **kwargs):
            calls.append(("conflict", self.role, kwargs.get("notebook_ids")))

        def open_working_notebook(self, _name, path, **kwargs):
            self.open_count += 1
            calls.append(
                (
                    "open",
                    self.role,
                    {"count": self.open_count},
                )
            )
            notebook_id = f"{self.role}-live-id"
            return (
                {"id": notebook_id, "name": working_paths[self.role].name},
                {
                    "actual_local_path": str(Path(path).resolve()),
                    "hierarchy_open_status": "passed",
                    "opened_hierarchy": [{"object_id": "section-id"}],
                },
            )

    wrappers = {role: FakeWrapper(role) for role in ("source", "destination")}
    notebooks, leases = validation._open_materialized_bundle(
        FakeStore(),
        materialized,
        wrappers,
        ("source", "destination"),
    )

    lifecycle_calls = [
        (name, role)
        for name, role, _value in calls
        if name in {"open", "close"}
    ]
    assert lifecycle_calls == [
        ("open", "source"),
        ("open", "destination"),
    ]
    open_calls = [value for name, _role, value in calls if name == "open"]
    assert open_calls == [
        {"count": 1},
        {"count": 1},
    ]
    assert notebooks["source"]["id"] == "source-live-id"
    assert notebooks["destination"]["id"] == "destination-live-id"
    assert leases["source"]["actual_local_path"] == str(
        working_paths["source"].resolve()
    )
    assert calls[-2:] == [
        ("record", "source", "source-live-id"),
        ("record", "destination", "destination-live-id"),
    ]
def _publish(tmp_path: Path):
    recipe = SCENARIO_REGISTRY.get("copy-notebook").fixture_recipe
    store = BundleCacheStore(tmp_path / "cache")
    store.initialize()
    source = _source(tmp_path)
    hit = store.publish(
        recipe,
        recipe.default_template_instance_id,
        source_paths={"source": source},
        source_notebooks={"source": {"id": "template-id", "name": "Disposable"}},
        closed_roles={"source"},
        validation={"passed": True},
    )
    return recipe, store, source, hit


def _windows_error(code: int) -> OSError:
    error = PermissionError(f"injected WinError {code}")
    error.winerror = code
    return error


def _legacy_empty_cache_shell(tmp_path: Path, *, with_summary: bool) -> Path:
    validation_root = tmp_path / ".local-validation"
    cache_root = validation_root / "fixture-cache"
    cache_root.mkdir(parents=True)
    write_json(
        validation_root / ".managed-validation-root.json",
        {
            "schema_version": 1,
            "purpose": "local-onenote-mcp-manual-validation",
        },
    )
    write_json(
        cache_root / ".managed-fixture-cache.json",
        {
            "schema_version": 1,
            "purpose": "local-onenote-mcp-fixture-cache",
        },
    )
    write_json(cache_root / "index.json", {"schema_version": 1, "entries": {}})
    if with_summary:
        summary_path = validation_root / ("cleanup-summary-" + "a" * 32 + ".json")
        write_json(
            summary_path,
            {
                "schema_version": 1,
                "action": "clear-all",
                "dry_run": False,
                "ok": True,
                "human_confirmation_required": True,
                "confirmation_mode": "interactive_stdin",
                "created_at": "2026-08-13T00:00:00+00:00",
                "summary_path": str(summary_path.resolve()),
                "managed_roots": {
                    "validation": str(validation_root.resolve()),
                    "cache": str(cache_root.resolve()),
                    "workspace": str(tmp_path.resolve()),
                },
                "root_checks": {
                    "fixed_repository_root": True,
                    "not_filesystem_root": True,
                    "not_workspace_root": True,
                    "root_marker_valid": True,
                    "root_reparse_point_free": True,
                },
                "open_path_snapshot": {"status": "complete", "error": None},
                "counts": {
                    "discovered": 0,
                    "planned": 0,
                    "deleted": 0,
                    "refused": 0,
                    "failed": 0,
                },
                "targets": [],
                "finalization": {"failures": []},
            },
        )
    return cache_root


def test_legacy_empty_cache_shell_requires_durable_clear_all_proof(tmp_path) -> None:
    cache_root = _legacy_empty_cache_shell(tmp_path, with_summary=False)

    with pytest.raises(RunnerFailure, match="Legacy fixture cache schema"):
        BundleCacheStore(cache_root).initialize()

    assert read_json(cache_root / ".managed-fixture-cache.json")["schema_version"] == 1
    assert read_json(cache_root / "index.json")["schema_version"] == 1


def test_legacy_empty_cache_shell_activation_refuses_any_payload(tmp_path) -> None:
    cache_root = _legacy_empty_cache_shell(tmp_path, with_summary=True)
    (cache_root / ("a" * 64)).mkdir()

    with pytest.raises(RunnerFailure, match="Legacy fixture cache schema"):
        BundleCacheStore(cache_root).initialize()


def test_legacy_empty_cache_shell_activates_schema_v2(tmp_path) -> None:
    cache_root = _legacy_empty_cache_shell(tmp_path, with_summary=True)

    BundleCacheStore(cache_root).initialize()

    marker = read_json(cache_root / ".managed-fixture-cache.json")
    index = read_json(cache_root / "index.json")
    assert marker["schema_version"] == 2
    assert index["schema_version"] == 2
    assert marker["activated_from_schema_version"] == 1
    assert index["activated_from_schema_version"] == 1
    assert marker["activation_summary"] == index["activation_summary"]


def test_legacy_empty_cache_shell_activation_resumes_after_index_stamp(tmp_path) -> None:
    cache_root = _legacy_empty_cache_shell(tmp_path, with_summary=True)
    summary_path = next(cache_root.parent.glob("cleanup-summary-*.json"))
    write_json(
        cache_root / "index.json",
        {
            "schema_version": 2,
            "entries": {},
            "activated_from_schema_version": 1,
            "activation_summary": str(summary_path.resolve()),
            "activated_at": "2026-08-13T00:00:01+00:00",
        },
    )

    BundleCacheStore(cache_root).initialize()

    assert read_json(cache_root / ".managed-fixture-cache.json")["schema_version"] == 2
    assert read_json(cache_root / "index.json")["schema_version"] == 2


def test_legacy_empty_cache_shell_allows_only_post_summary_schema_v2_runs(tmp_path) -> None:
    cache_root = _legacy_empty_cache_shell(tmp_path, with_summary=True)
    run_root = cache_root.parent / "run-2026-08-13-00-00-01"
    run_root.mkdir()
    write_json(
        run_root / "run-state.json",
        {
            "schema_version": 2,
            "human_only": True,
            "agent_execution_prohibited": True,
            "started_at": "2026-08-13T00:00:01+00:00",
        },
    )

    BundleCacheStore(cache_root).initialize()
    assert read_json(cache_root / ".managed-fixture-cache.json")["schema_version"] == 2

    second = _legacy_empty_cache_shell(tmp_path / "legacy-run", with_summary=True)
    legacy_run = second.parent / "run-2026-08-13-00-00-02"
    legacy_run.mkdir()
    write_json(
        legacy_run / "run-state.json",
        {
            "schema_version": 1,
            "human_only": True,
            "agent_execution_prohibited": True,
        },
    )
    with pytest.raises(RunnerFailure, match="Legacy fixture cache schema"):
        BundleCacheStore(second).initialize()


def test_orchestrator_accepts_only_proof_backed_empty_legacy_cache_shell(tmp_path) -> None:
    cache_root = _legacy_empty_cache_shell(tmp_path, with_summary=True)

    validation._assert_no_legacy_validation_payload(
        cache_root.parent / "run-new",
        cache_root,
    )

    (cache_root / ("b" * 64)).mkdir()
    with pytest.raises(RunnerFailure, match="Legacy fixture cache metadata"):
        validation._assert_no_legacy_validation_payload(
            cache_root.parent / "run-new",
            cache_root,
        )


def test_orchestrator_rejects_schema_v2_run_without_ownership_flags(tmp_path) -> None:
    validation_root = tmp_path / ".local-validation"
    run_root = validation_root / "run-2026-08-13-00-00-04"
    run_root.mkdir(parents=True)
    write_json(run_root / "run-state.json", {"schema_version": 2})

    with pytest.raises(RunnerFailure, match="unowned run metadata"):
        validation._assert_no_legacy_validation_payload(
            validation_root / "run-new",
            None,
        )


@pytest.mark.parametrize(
    "relative",
    (
        Path("unknown-directory"),
        Path("unknown-file.json"),
        Path("a" * 32) / "unexpected",
        Path("b" * 32) / "instances" / "p" / "unexpected",
        Path("c" * 32) / "instances" / "p",
        Path(f".s-{'d' * 16}"),
    ),
)
def test_cache_initialize_rejects_unknown_schema_v2_layout(tmp_path, relative) -> None:
    store = BundleCacheStore(tmp_path / "cache")
    store.initialize()
    target = store.cache_root / relative
    if target.suffix:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("unknown", encoding="utf-8")
    else:
        target.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RunnerFailure, match="layout|staging"):
        store.initialize()


def test_authored_live_revalidation_checks_full_projection_digest(tmp_path) -> None:
    recipe, store, _source_path, _programmatic = _publish(tmp_path)
    authored_id = f"authored-{'a' * 24}"
    authored = store.publish(
        recipe,
        authored_id,
        source_paths={"source": _source(tmp_path / "authored-full-digest")},
        source_notebooks={"source": {"id": "authored-id", "name": "Authored"}},
        closed_roles={"source"},
        validation={"passed": True},
        projection_digest="a" * 64,
    )

    _assert_authored_cache_identity(
        authored,
        type(
            "Frozen",
            (),
            {
                "template_instance_id": authored_id,
                "projection_digest": "a" * 64,
                "state": "ready",
                "mutation_eligible": True,
                "move_source_deletion_allowed": True,
            },
        )(),
    )
    with pytest.raises(InvariantFailure, match="full frozen identity"):
        _assert_authored_cache_identity(
            authored,
            type(
                "Frozen",
                (),
                {
                    "template_instance_id": authored_id,
                    "projection_digest": ("a" * 24) + ("b" * 40),
                    "state": "ready",
                    "mutation_eligible": True,
                    "move_source_deletion_allowed": True,
                },
            )(),
        )


def test_authored_ready_instance_metadata_drives_unique_and_ambiguous_selection(
    tmp_path,
) -> None:
    recipe = SCENARIO_REGISTRY.get("interactive-user-authored-fixture").fixture_recipe
    store = BundleCacheStore(tmp_path / "cache-authored-selection")
    store.initialize()
    ready_id = f"authored-{'a' * 24}"
    ready = store.publish(
        recipe,
        ready_id,
        source_paths={"source": _source(tmp_path / "ready")},
        source_notebooks={"source": {"id": "ready-id", "name": "Ready"}},
        closed_roles={"source"},
        validation={"passed": True},
        projection_digest="a" * 64,
        state="ready",
        mutation_eligible=True,
        move_source_deletion_allowed=True,
    )
    evidence_id = f"authored-{'b' * 24}"
    evidence = store.publish(
        recipe,
        evidence_id,
        source_paths={"source": _source(tmp_path / "evidence")},
        source_notebooks={"source": {"id": "evidence-id", "name": "Evidence"}},
        closed_roles={"source"},
        validation={"passed": True},
        projection_digest="b" * 64,
        state="evidence_only",
        mutation_eligible=False,
        move_source_deletion_allowed=False,
    )

    assert ready.entry["mutation_eligible"] is True
    assert ready.entry["move_source_deletion_allowed"] is True
    assert evidence.entry["mutation_eligible"] is False
    assert evidence.entry["move_source_deletion_allowed"] is False
    assert store.list_ready_instances(recipe, mutation_eligible_only=True) == [ready_id]
    assert recipe.select_template_instance_id(
        argparse.Namespace(template_instance_id=None),
        cache_store=store,
    ) == ready_id

    second_ready_id = f"authored-{'c' * 24}"
    store.publish(
        recipe,
        second_ready_id,
        source_paths={"source": _source(tmp_path / "second-ready")},
        source_notebooks={"source": {"id": "second-id", "name": "Second"}},
        closed_roles={"source"},
        validation={"passed": True},
        projection_digest="c" * 64,
        state="ready",
    )
    with pytest.raises(RunnerFailure, match="interactive_cache_ambiguous"):
        recipe.select_template_instance_id(
            argparse.Namespace(template_instance_id=None),
            cache_store=store,
        )


def test_authored_publish_rejects_state_eligibility_mismatch(tmp_path) -> None:
    recipe = SCENARIO_REGISTRY.get("interactive-user-authored-fixture").fixture_recipe
    store = BundleCacheStore(tmp_path / "cache-authored-mismatch")
    store.initialize()

    with pytest.raises(RunnerFailure, match="eligibility"):
        store.publish(
            recipe,
            f"authored-{'d' * 24}",
            source_paths={"source": _source(tmp_path / "mismatch")},
            source_notebooks={"source": {"id": "mismatch-id", "name": "Mismatch"}},
            closed_roles={"source"},
            validation={"passed": True},
            projection_digest="d" * 64,
            state="evidence_only",
            mutation_eligible=True,
            move_source_deletion_allowed=False,
        )


@pytest.mark.parametrize(
    ("operation", "phase"),
    (
        ("quarantine", "cache_quarantine_preflight"),
        ("invalidate", "cache_invalidation_preflight"),
    ),
)
def test_cache_state_change_budget_failure_precedes_metadata_or_open_probe(
    tmp_path,
    monkeypatch,
    operation,
    phase,
) -> None:
    recipe, store, _source_path, hit = _publish(tmp_path)
    original_preflight = fixture_cache_module.preflight_paths
    probes: list[bool] = []

    def fail_selected(paths, *, phase: str):
        if phase == selected_phase:
            raise PathBudgetFailure(
                phase=phase,
                target_kind="cache_tombstone_evidence",
                path=store.tombstone_path,
                actual_utf16=241,
                limit_utf16=240,
                relative_path=None,
                remediation={"code": "shorten_repository_path", "message": "shorten"},
            )
        return original_preflight(paths, phase=phase)

    selected_phase = phase
    monkeypatch.setattr(fixture_cache_module, "preflight_paths", fail_selected)
    with pytest.raises(PathBudgetFailure):
        if operation == "quarantine":
            store.quarantine_exact(
                recipe,
                hit.template_instance_id,
                reason="test",
                run_id="run-test",
            )
        else:
            store.invalidate_exact(
                recipe,
                hit.template_instance_id,
                reason="test",
                open_state_probe=lambda _entry: probes.append(True) or False,
            )

    entry = read_json(hit.entry_path / "bundle-entry.json")
    index = read_json(store.cache_root / "index.json")
    key = f"{hit.fingerprint}:{hit.template_instance_id}"
    assert entry["state"] == "ready"
    assert index["entries"][key]["state"] == "ready"
    assert probes == []


def test_publish_and_materialize_preserve_opaque_byte_inventory(tmp_path) -> None:
    recipe, store, source, hit = _publish(tmp_path)

    lookup = store.lookup(recipe, recipe.default_template_instance_id)
    assert lookup is not None
    materialized = store.materialize(
        lookup,
        tmp_path / "run",
        cache_origin="cold_build",
    )

    assert inventory_directory(source) == inventory_directory(
        materialized.template_paths["source"]
    )
    assert inventory_directory(source) == inventory_directory(
        materialized.working_paths["source"]
    )
    assert materialized.template_paths["source"] != materialized.working_paths["source"]
    assert hit.entry["opened_template"] is False
    evidence = read_json(tmp_path / "run" / "cache-materialization.json")
    assert evidence["decision"] == "validated_hit"
    assert evidence["cache_origin"] == "cold_build"
    assert set(evidence["phases_seconds"]) == {
        "preflight",
        "copy_and_verify",
        "publish_working_paths",
        "total",
    }
    assert all(value >= 0 for value in evidence["phases_seconds"].values())
    store.record_opened_working_role(
        materialized,
        role="source",
        notebook_id="working-id",
        actual_path=materialized.working_paths["source"],
    )
    immutability = store.verify_templates_unchanged(materialized)
    assert immutability["all_templates_unchanged"] is True


def test_cache_publish_retries_transient_windows_directory_lock(
    tmp_path,
    monkeypatch,
) -> None:
    original_replace = os.replace
    directory_attempts = 0
    delays: list[float] = []

    def flaky_replace(source, destination) -> None:
        nonlocal directory_attempts
        source_path = Path(source)
        if source_path.name.startswith(".s-") and source_path.is_dir():
            directory_attempts += 1
            if directory_attempts == 1:
                raise _windows_error(5)
        original_replace(source, destination)

    monkeypatch.setattr(local_filesystem, "_IS_WINDOWS", True)
    monkeypatch.setattr(local_filesystem.os, "replace", flaky_replace)
    monkeypatch.setattr(local_filesystem.time, "sleep", delays.append)

    recipe, store, _source_path, hit = _publish(tmp_path)

    assert directory_attempts == 2
    assert delays == [0.05]
    assert hit.entry_path.is_dir()
    assert store.lookup(recipe, recipe.default_template_instance_id) is not None


def test_materialize_retries_transient_windows_directory_lock(
    tmp_path,
    monkeypatch,
) -> None:
    recipe, store, _source_path, hit = _publish(tmp_path)
    original_replace = os.replace
    directory_attempts = 0
    delays: list[float] = []

    def flaky_replace(source, destination) -> None:
        nonlocal directory_attempts
        source_path = Path(source)
        if source_path.parent.name.startswith(".m-"):
            directory_attempts += 1
            if directory_attempts == 1:
                raise _windows_error(32)
        original_replace(source, destination)

    monkeypatch.setattr(local_filesystem, "_IS_WINDOWS", True)
    monkeypatch.setattr(local_filesystem.os, "replace", flaky_replace)
    monkeypatch.setattr(local_filesystem.time, "sleep", delays.append)

    materialized = store.materialize(hit, tmp_path / "run")

    assert directory_attempts == 2
    assert delays == [0.05]
    assert materialized.evidence_path.is_file()
    assert inventory_directory(source := materialized.template_paths["source"]) == (
        inventory_directory(materialized.working_paths["source"])
    )
    assert source != materialized.working_paths["source"]
    assert not list((tmp_path / "run").glob(".m-*"))


def test_cache_publish_retry_exhaustion_leaves_no_matchable_entry(
    tmp_path,
    monkeypatch,
) -> None:
    recipe = SCENARIO_REGISTRY.get("copy-notebook").fixture_recipe
    store = BundleCacheStore(tmp_path / "cache")
    store.initialize()
    source = _source(tmp_path)
    original_replace = os.replace

    def locked_publish(source_path, destination_path) -> None:
        candidate = Path(source_path)
        if candidate.name.startswith(".s-") and candidate.is_dir():
            raise _windows_error(32)
        original_replace(source_path, destination_path)

    monkeypatch.setattr(local_filesystem, "_IS_WINDOWS", True)
    monkeypatch.setattr(local_filesystem.os, "replace", locked_publish)
    monkeypatch.setattr(local_filesystem.time, "sleep", lambda _delay: None)

    with pytest.raises(OSError) as captured:
        store.publish(
            recipe,
            recipe.default_template_instance_id,
            source_paths={"source": source},
            source_notebooks={"source": {"id": "template-id", "name": "Disposable"}},
            closed_roles={"source"},
            validation={"passed": True},
        )

    assert captured.value.winerror == 32
    assert store.exact_entry_state(recipe, recipe.default_template_instance_id) is None
    assert not list(store.cache_root.glob(".s-*"))


def test_publish_requires_every_declared_role_to_be_closed(tmp_path) -> None:
    recipe = SCENARIO_REGISTRY.get("copy-notebook").fixture_recipe
    store = BundleCacheStore(tmp_path / "cache")
    store.initialize()
    with pytest.raises(RunnerFailure, match="precisely closed"):
        store.publish(
            recipe,
            recipe.default_template_instance_id,
            source_paths={"source": _source(tmp_path)},
            source_notebooks={"source": {"id": "template-id"}},
            closed_roles=set(),
            validation={"passed": True},
        )


def test_exact_entry_state_distinguishes_missing_matchable_and_invalid(tmp_path) -> None:
    recipe = SCENARIO_REGISTRY.get("copy-notebook").fixture_recipe
    store = BundleCacheStore(tmp_path / "cache")
    store.initialize()

    assert store.exact_entry_state(recipe, recipe.default_template_instance_id) is None
    _recipe, store, _source_path, hit = _publish(tmp_path)
    assert store.exact_entry_state(recipe, recipe.default_template_instance_id) == "ready"

    store.quarantine_exact(
        recipe,
        recipe.default_template_instance_id,
        reason="injected invalid entry",
        run_id="run-invalid",
    )
    assert store.exact_entry_state(recipe, recipe.default_template_instance_id) == "invalid"
    assert hit.entry_path.exists()


@pytest.mark.parametrize("state", ["cleanup_failed", "unexpected"])
def test_unusable_exact_entry_state_blocks_automatic_rebuild(tmp_path, state) -> None:
    recipe, store, _source_path, hit = _publish(tmp_path)
    entry_path = hit.entry_path / "bundle-entry.json"
    entry = read_json(entry_path)
    entry["state"] = state
    write_json(entry_path, entry)

    if state == "cleanup_failed":
        assert store.exact_entry_state(recipe, recipe.default_template_instance_id) == state
        with pytest.raises(RunnerFailure, match="previously failed"):
            validation._resolve_exact_cache_entry(
                store,
                recipe,
                recipe.default_template_instance_id,
                run_id="run-retry",
                open_state_probe=lambda _entry: False,
                allow_open_failure_recovery=False,
            )
    else:
        with pytest.raises(RunnerFailure, match="unsupported state"):
            store.exact_entry_state(recipe, recipe.default_template_instance_id)
    assert hit.entry_path.exists()


def test_exact_entry_missing_ownership_metadata_blocks_cleanup_and_rebuild(tmp_path) -> None:
    recipe = SCENARIO_REGISTRY.get("copy-notebook").fixture_recipe
    store = BundleCacheStore(tmp_path / "cache")
    store.initialize()
    path = store.instance_path(
        recipe.cache_fingerprint,
        recipe.default_template_instance_id,
    )
    path.mkdir(parents=True)

    with pytest.raises(RunnerFailure, match="missing ownership metadata"):
        store.exact_entry_state(recipe, recipe.default_template_instance_id)
    assert path.exists()


def test_lookup_detects_template_mutation(tmp_path) -> None:
    recipe, store, _source_path, hit = _publish(tmp_path)
    (hit.entry_path / "notebooks" / "source" / "template-notebook" / "Section.one").write_bytes(
        b"mutated"
    )

    with pytest.raises(InvariantFailure, match="inventory mismatch"):
        store.lookup(recipe, recipe.default_template_instance_id)


def test_materialized_working_copy_does_not_block_exact_cache_cleanup(tmp_path) -> None:
    recipe, store, _source_path, hit = _publish(tmp_path)
    materialized = store.materialize(hit, tmp_path / "run")
    evidence = store.invalidate_exact(
        recipe,
        recipe.default_template_instance_id,
        reason="test invalidation",
    )
    assert evidence["deleted"] is True
    assert evidence["template_not_open"] is True
    assert evidence["run_lease_checked"] is False
    assert not hit.entry_path.exists()
    assert materialized.working_paths["source"].exists()
    assert store.cache_root.exists()


def test_invalid_entry_open_template_blocks_cleanup_and_rebuild(tmp_path) -> None:
    recipe, store, _source_path, hit = _publish(tmp_path)
    store.quarantine_exact(
        recipe,
        recipe.default_template_instance_id,
        reason="materialized-live-validation failed: injected mismatch",
        run_id="run-invalid",
    )

    with pytest.raises(RunnerFailure, match="template is open"):
        validation._resolve_exact_cache_entry(
            store,
            recipe,
            recipe.default_template_instance_id,
            run_id="run-retry",
            open_state_probe=lambda _entry: True,
            allow_open_failure_recovery=False,
        )

    assert store.exact_entry_state(recipe, recipe.default_template_instance_id) == "invalid"
    assert hit.entry_path.exists()
    assert not store.tombstone_path.exists()


def test_cache_rejects_arbitrary_identity_selectors(tmp_path) -> None:
    store = BundleCacheStore(tmp_path / "cache")
    store.initialize()
    with pytest.raises(RunnerFailure, match="fingerprint"):
        store.instance_path("../outside", "instance")
    with pytest.raises(RunnerFailure, match="instance"):
        store.instance_path("0" * 64, "../outside")


def test_failed_materialized_open_records_run_local_live_working_notebook_id(tmp_path) -> None:
    _recipe, store, _source_path, hit = _publish(tmp_path)
    run_dir = tmp_path / "run"
    materialized = store.materialize(hit, run_dir)
    lifecycle_lease = run_dir / "lifecycle-lease.json"
    write_json(
        lifecycle_lease,
        {
            "schema_version": 2,
            "notebook_id": "live-working-id",
            "actual_local_path": str(materialized.working_paths["source"]),
            "hierarchy_open_status": "failed",
            "state": "active",
        },
    )

    class FailedOpenWrapper:
        lease_path = lifecycle_lease

    validation._record_failed_materialized_open(
        store,
        FailedOpenWrapper(),
        materialized,
        RuntimeOptions(run_dir, 180, False, False, use_cache=True),
    )

    evidence = read_json(run_dir / "cache-failed-open-live-id.json")
    assert evidence["bound"] is True
    assert evidence["notebook_id"] == "live-working-id"


def test_failed_live_validation_quarantines_without_deleting_template(tmp_path) -> None:
    recipe, store, _source_path, hit = _publish(tmp_path)

    evidence = store.quarantine_exact(
        recipe,
        recipe.default_template_instance_id,
        reason="injected live validation failure",
        run_id="run-failed",
    )

    assert evidence["template_deleted"] is False
    assert hit.entry_path.exists()
    assert store.lookup(recipe, recipe.default_template_instance_id) is None
    assert read_json(hit.entry_path / "bundle-entry.json")["state"] == "invalid"


def test_run_local_materialized_failure_does_not_quarantine_template(tmp_path) -> None:
    recipe, store, _source_path, hit = _publish(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    validation._record_materialized_failure(
        store,
        SCENARIO_REGISTRY.get("copy-notebook"),
        hit,
        RuntimeOptions(run_dir, 180, False, False, use_cache=True),
        RunnerFailure("injected activation timeout"),
        phase="cold-materialized-open",
    )

    evidence = read_json(run_dir / "cache-live-validation-failure.json")
    assert evidence["cache_entry_matchable"] is True
    assert evidence["template_integrity_not_implicated"] is True
    assert evidence["retryable_with_same_template"] is True
    assert evidence["quarantine"] is None
    assert store.exact_entry_state(recipe, recipe.default_template_instance_id) == "ready"
    assert not store.quarantine_path.exists()


@pytest.mark.parametrize(
    "phase",
    ["materialized-open", "cold-materialized-open", "bootstrap-materialized-open"],
)
def test_working_copy_open_failure_quarantine_can_be_recovered_after_inventory_check(
    tmp_path, phase,
) -> None:
    recipe, store, _source_path, hit = _publish(tmp_path)
    store.quarantine_exact(
        recipe,
        recipe.default_template_instance_id,
        reason=f"{phase} failed: RunnerFailure: injected activation timeout",
        run_id="run-failed",
    )

    recovered = store.recover_retryable_open_failure(
        recipe,
        recipe.default_template_instance_id,
        run_id="run-retry",
    )

    assert recovered is not None
    entry = read_json(hit.entry_path / "bundle-entry.json")
    assert entry["state"] == "ready"
    assert entry["open_failure_recoveries"][0]["invalidated_by_run"] == "run-failed"
    assert store.lookup(recipe, recipe.default_template_instance_id) is not None
    recovery = store.recovery_path.read_text(encoding="utf-8")
    assert "template_inventory_revalidated" in recovery


def test_programmatic_cold_open_failure_is_recovered_before_invalid_cleanup(
    tmp_path,
) -> None:
    recipe, store, _source_path, hit = _publish(tmp_path)
    store.quarantine_exact(
        recipe,
        recipe.default_template_instance_id,
        reason="cold-materialized-open failed: RunnerFailure: injected activation timeout",
        run_id="run-failed",
    )

    recovered, decision, invalidated = validation._resolve_exact_cache_entry(
        store,
        recipe,
        recipe.default_template_instance_id,
        run_id="run-retry",
        open_state_probe=lambda _entry: False,
        allow_open_failure_recovery=True,
    )

    assert recovered is not None
    assert decision == "recovered_retryable_open_failure"
    assert invalidated is False
    assert hit.entry_path.exists()
    assert store.exact_entry_state(recipe, recipe.default_template_instance_id) == "ready"
    assert not store.tombstone_path.exists()


def test_consumer_retryable_open_failure_is_recovered_before_invalid_cleanup(
    tmp_path,
) -> None:
    class ConsumerRecipe(RecipeBase):
        consumer_scenario = True

        async def build(self, context):  # pragma: no cover - cache contract only
            raise AssertionError("consumer must not build a fixture")

        def validate(self, context, build):
            return ("recording validator",)

    recipe = ConsumerRecipe("copy-notebook")
    store = BundleCacheStore(tmp_path / "cache")
    store.initialize()
    source = _source(tmp_path)
    hit = store.publish(
        recipe,
        recipe.default_template_instance_id,
        source_paths={"source": source},
        source_notebooks={"source": {"id": "template-id", "name": "Disposable"}},
        closed_roles={"source"},
        validation={"passed": True},
    )
    store.quarantine_exact(
        recipe,
        recipe.default_template_instance_id,
        reason="materialized-open failed: RunnerFailure: injected activation timeout",
        run_id="run-failed",
    )

    recovered, decision, invalidated = validation._resolve_exact_cache_entry(
        store,
        recipe,
        recipe.default_template_instance_id,
        run_id="run-retry",
        open_state_probe=lambda _entry: False,
        allow_open_failure_recovery=True,
    )

    assert recovered is not None
    assert decision == "recovered_retryable_open_failure"
    assert invalidated is False
    assert hit.entry_path.exists()
    assert store.exact_entry_state(recipe, recipe.default_template_instance_id) == "ready"
    assert not store.tombstone_path.exists()


def test_content_validation_quarantine_is_not_recovered_as_open_failure(tmp_path) -> None:
    recipe, store, _source_path, _hit = _publish(tmp_path)
    store.quarantine_exact(
        recipe,
        recipe.default_template_instance_id,
        reason="materialized-live-validation failed: injected detector mismatch",
        run_id="run-failed",
    )

    recovered = store.recover_retryable_open_failure(
        recipe,
        recipe.default_template_instance_id,
        run_id="run-retry",
    )

    assert recovered is None
    assert store.lookup(recipe, recipe.default_template_instance_id) is None


def test_materialized_structure_rebinds_changed_notebook_section_and_page_ids() -> None:
    source_notebook = {"id": "source-notebook", "path": "Original"}
    working_notebook = {"id": "working-notebook", "path": "source-working-copy"}
    structure = {
        "canvas_section": {
            "id": "source-section",
            "resource_type": "section",
            "path": "Original/00-InsertedFile-Canvas",
        },
        "canvas_page": {
            "id": "source-page",
            "resource_type": "page",
            "path": "Original/00-InsertedFile-Canvas/01-Interactive-Canvas",
            "order": 0,
            "page_level": 1,
        },
    }
    snapshot = {
        "items": [
            {
                "id": "working-section",
                "resource_type": "section",
                "path": "source-working-copy/00-InsertedFile-Canvas",
            },
            {
                "id": "working-page",
                "resource_type": "page",
                "path": "source-working-copy/00-InsertedFile-Canvas/01-Interactive-Canvas",
                "order": 0,
                "page_level": 1,
            },
        ]
    }

    rebound, report = _rebind_materialized_structure(
        structure,
        source_notebook=source_notebook,
        working_notebook=working_notebook,
        snapshot=snapshot,
    )

    assert report["passed"] is True
    assert report["notebook_id_changed"] is True
    assert rebound["canvas_section"]["id"] == "working-section"
    assert rebound["canvas_page"]["id"] == "working-page"


def test_materialized_reparent_page_evidence_rebinds_only_owned_page_id_fields() -> None:
    source_structure = {
        "reparent_page": {"id": "source-page", "resource_type": "page"}
    }
    working_structure = {
        "reparent_page": {"id": "working-page", "resource_type": "page"}
    }
    cached = {
        "reparent_page_fixture": {
            "page_id": "source-page",
            "manual_content": ["literal source-page remains content"],
            "list_tag": {
                "page_id": "source-page",
                "observed_capabilities": ["List", "Tag"],
            },
        }
    }

    rebound, report = _rebind_materialized_evidence(
        source_structure,
        working_structure,
        cached,
    )

    assert report["passed"] is True
    assert [mapping["field"] for mapping in report["mappings"]] == [
        "reparent_page_fixture.page_id",
        "reparent_page_fixture.list_tag.page_id",
    ]
    rich = rebound["reparent_page_fixture"]
    assert rich["page_id"] == "working-page"
    assert rich["list_tag"]["page_id"] == "working-page"
    assert rich["manual_content"] == ["literal source-page remains content"]
    assert cached["reparent_page_fixture"]["page_id"] == "source-page"
    assert cached["reparent_page_fixture"]["list_tag"]["page_id"] == "source-page"


def test_materialized_scoped_reparent_page_evidence_rebinds_each_owned_page() -> None:
    source_structure = {
        "root_only_selected": {"id": "source-root", "resource_type": "page"},
        "subtree_selected": {"id": "source-subtree", "resource_type": "page"},
    }
    working_structure = {
        "root_only_selected": {"id": "working-root", "resource_type": "page"},
        "subtree_selected": {"id": "working-subtree", "resource_type": "page"},
    }
    cached = {
        "reparent_page_fixture": {
            "page_id": "source-root",
            "manual_content": ["literal source-root remains content"],
            "scope_pages": {
                "root_only_selected": {"page_id": "source-root"},
                "subtree_selected": {"page_id": "source-subtree"},
            },
        }
    }

    rebound, report = _rebind_materialized_evidence(
        source_structure,
        working_structure,
        cached,
    )

    assert report["passed"] is True
    assert [mapping["field"] for mapping in report["mappings"]] == [
        "reparent_page_fixture.scope_pages.root_only_selected.page_id",
        "reparent_page_fixture.scope_pages.subtree_selected.page_id",
        "reparent_page_fixture.page_id",
    ]
    rich = rebound["reparent_page_fixture"]
    assert rich["page_id"] == "working-root"
    assert rich["scope_pages"]["root_only_selected"]["page_id"] == "working-root"
    assert rich["scope_pages"]["subtree_selected"]["page_id"] == "working-subtree"
    assert rich["manual_content"] == ["literal source-root remains content"]
    assert cached["reparent_page_fixture"]["page_id"] == "source-root"
    assert (
        cached["reparent_page_fixture"]["scope_pages"]["subtree_selected"]["page_id"]
        == "source-subtree"
    )


def test_materialized_scoped_reparent_page_evidence_rejects_unknown_scope_key() -> None:
    _rebound, report = _rebind_materialized_evidence(
        {"root_only_selected": {"id": "source-root"}},
        {"root_only_selected": {"id": "working-root"}},
        {
            "reparent_page_fixture": {
                "page_id": "source-root",
                "scope_pages": {
                    "root_only_selected": {"page_id": "source-root"},
                    "undeclared_selected": {"page_id": "source-other"},
                },
            }
        },
    )

    assert report["passed"] is False
    assert any(
        failure["field"]
        == "reparent_page_fixture.scope_pages.undeclared_selected.page_id"
        and failure["reason"] == "missing-structure-binding"
        and failure["manifest_key"] == "undeclared_selected"
        for failure in report["failures"]
    )


@pytest.mark.parametrize(
    "field_path",
    ["page_id", "list_tag.page_id"],
)
def test_materialized_reparent_page_evidence_rejects_unbound_source_id(
    field_path: str,
) -> None:
    cached = {
        "reparent_page_fixture": {
            "page_id": "source-page",
            "list_tag": {"page_id": "source-page"},
        }
    }
    if field_path == "page_id":
        cached["reparent_page_fixture"]["page_id"] = "unexpected-page"
    else:
        cached["reparent_page_fixture"]["list_tag"]["page_id"] = "unexpected-page"

    _rebound, report = _rebind_materialized_evidence(
        {"reparent_page": {"id": "source-page"}},
        {"reparent_page": {"id": "working-page"}},
        cached,
    )

    assert report["passed"] is False
    assert any(
        failure["field"] == f"reparent_page_fixture.{field_path}"
        and failure["reason"] == "source-id-mismatch"
        for failure in report["failures"]
    )


def test_materialized_reparent_page_evidence_rejects_invalid_shape() -> None:
    _rebound, report = _rebind_materialized_evidence(
        {"reparent_page": {"id": "source-page"}},
        {"reparent_page": {"id": "working-page"}},
        {"reparent_page_fixture": None},
    )

    assert report["passed"] is False
    assert report["failures"] == [
        {
            "field": "reparent_page_fixture",
            "reason": "invalid-evidence-shape",
        }
    ]


class _MaterializedHierarchyClient:
    def __init__(self, trees: list[dict]) -> None:
        self.trees = iter(trees)
        self.calls: list[str] = []

    async def call_tool(self, name, _arguments):
        self.calls.append(name)
        assert name == "expand_hierarchy"
        return {"tree": next(self.trees)}


def _materialized_tree(*, page_id: str | None, page_order: int = 0) -> dict:
    page_children = []
    if page_id is not None:
        page_children.append(
            {
                "item": {
                    "id": page_id,
                    "resource_type": "page",
                    "title": "01-Page",
                    "path": "Working/01-Section/01-Page",
                    "parent_id": "working-section",
                    "section_id": "working-section",
                    "order": page_order,
                    "page_level": 1,
                    "parent_page_id": None,
                },
                "children": [],
            }
        )
    return {
        "item": {
            "id": "working-notebook",
            "resource_type": "notebook",
            "name": "Working",
            "path": "Working",
        },
        "children": [
            {
                "item": {
                    "id": "working-section",
                    "resource_type": "section",
                    "name": "01-Section",
                    "path": "Working/01-Section",
                    "parent_id": "working-notebook",
                },
                "children": page_children,
            }
        ],
    }


def test_materialized_structure_waits_for_pages_and_two_stable_observations() -> None:
    client = _MaterializedHierarchyClient(
        [
            _materialized_tree(page_id=None),
            _materialized_tree(page_id="working-page"),
            _materialized_tree(page_id="working-page"),
        ]
    )
    structure = {
        "section": {
            "id": "source-section",
            "resource_type": "section",
            "path": "Template/01-Section",
        },
        "page": {
            "id": "source-page",
            "resource_type": "page",
            "path": "Template/01-Section/01-Page",
            "order": 0,
            "page_level": 1,
        },
    }

    _snapshot, rebound, remap, report = asyncio.run(
        _await_materialized_structure_convergence(
            client,
            role="source",
            structure=structure,
            source_notebook={"id": "source-notebook", "path": "Template"},
            working_notebook={"id": "working-notebook", "path": "Working"},
            max_observations=3,
            stable_observations=2,
            delay_seconds=0,
        )
    )

    assert report["passed"] is True
    assert report["attempts"] == 3
    assert report["stable_observations"] == 2
    assert rebound["page"]["id"] == "working-page"
    assert remap["passed"] is True
    assert client.calls == ["expand_hierarchy", "expand_hierarchy", "expand_hierarchy"]


def test_materialized_structure_rejects_hierarchy_oscillation() -> None:
    client = _MaterializedHierarchyClient(
        [
            _materialized_tree(page_id="page-a", page_order=0),
            _materialized_tree(page_id="page-b", page_order=0),
            _materialized_tree(page_id="page-a", page_order=0),
        ]
    )
    structure = {
        "section": {
            "id": "source-section",
            "resource_type": "section",
            "path": "Template/01-Section",
        },
        "page": {
            "id": "source-page",
            "resource_type": "page",
            "path": "Template/01-Section/01-Page",
            "order": 0,
            "page_level": 1,
        },
    }

    _snapshot, _rebound, _remap, report = asyncio.run(
        _await_materialized_structure_convergence(
            client,
            role="source",
            structure=structure,
            source_notebook={"id": "source-notebook", "path": "Template"},
            working_notebook={"id": "working-notebook", "path": "Working"},
            max_observations=3,
            stable_observations=2,
            delay_seconds=0,
        )
    )

    assert report["passed"] is False
    assert report["stable_observations"] == 1
    assert "deadline exceeded" in report["error"]


def test_reparent_page_materialization_persists_rebound_run_local_evidence(
    monkeypatch,
    tmp_path,
) -> None:
    scenario = SCENARIO_REGISTRY.get("reparent-page")
    artifact_root = tmp_path / "entry" / "notebooks" / "source"
    source_notebook = {"id": "source-notebook", "name": "Original", "path": "Original"}
    declarations = [
        ("description_section", "section", "00-Description", 0),
        ("description_page", "page", "00-Reparent-Page-Description", 0),
        ("source_section", "section", "01-Source-Section", 0),
        ("destination_section", "section", "02-Destination-Section", 0),
        ("reparent_page", "page", "01-Reparent-Page", 0),
        ("destination_anchor_page", "page", "02-Destination-Anchor", 0),
        ("destination_anchor_page_b", "page", "03-Destination-Anchor", 1),
    ]
    source_structure: dict[str, dict] = {}
    working_structure: dict[str, dict] = {}
    section_paths = {
        "description_page": "00-Description",
        "reparent_page": "01-Source-Section",
        "destination_anchor_page": "02-Destination-Section",
        "destination_anchor_page_b": "02-Destination-Section",
    }
    for key, resource_type, name, order in declarations:
        suffix = name if resource_type == "section" else f"{section_paths[key]}/{name}"
        source_structure[key] = {
            "id": f"source-{key}",
            "resource_type": resource_type,
            "path": f"Original/{suffix}",
            **({"order": order, "page_level": 1} if resource_type == "page" else {}),
        }
        working_structure[key] = {
            "id": f"working-{key}",
            "resource_type": resource_type,
            "path": f"working-copy/{suffix}",
            "notebook_id": "working-notebook",
            "is_in_recycle_bin": False,
            **({"name": name, "parent_id": "working-notebook"} if resource_type == "section" else {"title": name, "order": order, "page_level": 1, "parent_page_id": None}),
        }
    description_id = working_structure["description_section"]["id"]
    source_id = working_structure["source_section"]["id"]
    destination_id = working_structure["destination_section"]["id"]
    for key, section_id in {
        "description_page": description_id,
        "reparent_page": source_id,
        "destination_anchor_page": destination_id,
        "destination_anchor_page_b": destination_id,
    }.items():
        working_structure[key]["section_id"] = section_id
        working_structure[key]["parent_id"] = section_id
    rich = {
        "page_id": "source-reparent_page",
        "automated_content": ["rich_text", "table", "image", "list", "tag"],
        "list_tag": {
            "page_id": "source-reparent_page",
            "observed_capabilities": ["List", "Tag"],
            "observed_counts": {"List": 3, "Tag": 3},
        },
    }
    cached_run_dir = tmp_path / "cached-run"
    source_manifest = {
        "notebook": source_notebook,
        "structure": source_structure,
        "reparent_page_fixture": rich,
        "disposable_targets": {
            "notebook_copy_root": str(cached_run_dir / "notebook-copies"),
            "source_notebook_path": str(
                cached_run_dir / "notebooks" / "source-template"
            ),
        },
        "lifecycle_lease": str(cached_run_dir / "lifecycle-lease.json"),
        "fixture_validation": {"status": "passed", "checks": []},
        "scenario_policies": {"stale-scenario": {"writes_enabled": False}},
        "scenario_spec": {
            "scenario": "stale-scenario",
            "fixture_profile": {"actual_manifest_keys": ["stale"]},
            "tool_allowlist": ["list_pages"],
        },
        "mcp_process_contract": {"maximum_starts": 99},
    }
    write_json(artifact_root / "template-manifest.json", source_manifest)
    write_json(
        artifact_root / "template-fixture-result.json",
        {"notebook": source_notebook, "structure_ids": {}},
    )
    snapshot = {"items": list(working_structure.values()), "page_hashes": {}}

    events: list[str] = []

    async def fake_snapshot(_client, _notebook_id):
        events.append("full-content")
        return snapshot

    async def fake_convergence(*_args, **_kwargs):
        events.append("hierarchy-stable")
        return (
            {"items": list(working_structure.values())},
            working_structure,
            {"passed": True, "mappings": [], "failures": []},
            {
                "passed": True,
                "phase": "hierarchy_convergence",
                "scenario_before_snapshot_started": False,
                "scenario_before_snapshot_completed": False,
            },
        )

    monkeypatch.setattr(
        "tests.manual_validation.scenarios.common.fixture_runtime.capture_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr(
        "tests.manual_validation.scenarios.common.fixture_runtime._await_materialized_structure_convergence",
        fake_convergence,
    )
    hit = CacheHit(
        scenario.fixture_recipe.cache_fingerprint,
        scenario.fixture_recipe.default_template_instance_id,
        tmp_path / "entry",
        {"roles": ["source"]},
    )
    run_dir = tmp_path / "run"
    working = run_dir / "notebooks" / "working-copy"
    template = tmp_path / "cache" / "template"
    working.mkdir(parents=True)
    template.mkdir(parents=True)
    materialized = MaterializedBundle(
        hit.fingerprint,
        hit.template_instance_id,
        {"source": template},
        {"source": working},
        run_dir / "cache-materialization.json",
    )

    manifest, _result = asyncio.run(
        prepare_materialized_fixture(
            scenario,
            argparse.Namespace(),
            RuntimeOptions(run_dir, 180, False, False, use_cache=True),
            object(),
            {"id": "working-notebook", "name": "working-copy", "path": "working-copy"},
            str(working),
            scenario.spec,
            hit,
            materialized,
        )
    )

    assert manifest["reparent_page_fixture"]["page_id"] == "working-reparent_page"
    assert manifest["scenario_spec"]["scenario"] == "reparent-page"
    assert manifest["scenario_spec"]["tool_allowlist"] == sorted(
        scenario.spec.tool_allowlist
    )
    assert "list_pages" not in manifest["scenario_spec"]["tool_allowlist"]
    assert manifest["scenario_policies"] == {
        scenario.name: scenario.spec.policy.as_dict()
    }
    assert manifest["mcp_process_contract"]["maximum_starts"] == 1
    assert manifest["fixture_cache"]["current_contract_refreshed"] is True
    assert (
        manifest["reparent_page_fixture"]["list_tag"]["page_id"]
        == "working-reparent_page"
    )
    remap = read_json(run_dir / "cache-structure-remap.json")
    assert remap["passed"] is True
    assert len(remap["evidence_rebinding"]["mappings"]) == 2
    assert events == ["hierarchy-stable", "full-content"]
    convergence = read_json(run_dir / "cache-hierarchy-convergence.json")
    assert convergence["passed"] is True
    assert convergence["elapsed_seconds"] >= 0
    assert convergence["roles"]["source"]["hierarchy_elapsed_seconds"] >= 0
    assert convergence["roles"]["source"]["scenario_before_elapsed_seconds"] >= 0
    assert convergence["roles"]["source"]["scenario_before_snapshot_completed"] is True
    cached_after = read_json(artifact_root / "template-manifest.json")
    assert cached_after["reparent_page_fixture"]["page_id"] == "source-reparent_page"
    assert (
        cached_after["reparent_page_fixture"]["list_tag"]["page_id"]
        == "source-reparent_page"
    )


def test_inserted_file_copy_live_validates_rebound_cached_structure(
    monkeypatch,
    tmp_path,
) -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-copy-inserted-file")
    artifact_root = tmp_path / "entry" / "notebooks" / "source"
    cached_run_dir = tmp_path / "cached-run"
    source_manifest = {
        "notebook": {"id": "source-notebook", "name": "Original", "path": "Original"},
        "structure": {
            "canvas_section": {
                "id": "source-section",
                "resource_type": "section",
                "path": "Original/00-InsertedFile-Canvas",
            },
            "canvas_page": {
                "id": "source-page",
                "resource_type": "page",
                "path": "Original/00-InsertedFile-Canvas/01-Interactive-Canvas",
                "section_id": "source-section",
                "order": 0,
                "page_level": 1,
            },
        },
        "disposable_targets": {
            "notebook_copy_root": str(cached_run_dir / "notebook-copies"),
            "source_notebook_path": str(
                cached_run_dir / "notebooks" / "source-template"
            ),
        },
        "lifecycle_lease": str(cached_run_dir / "lifecycle-lease.json"),
        "fixture_validation": {"status": "passed", "checks": []},
    }
    write_json(artifact_root / "template-manifest.json", source_manifest)
    write_json(
        artifact_root / "template-fixture-result.json",
        {"notebook": source_manifest["notebook"], "structure_ids": {}},
    )
    working_page = "working-page"
    snapshot = {
        "items": [
            {
                "id": "working-section",
                "resource_type": "section",
                "path": "source-working-copy/00-InsertedFile-Canvas",
                "parent_id": "working-notebook",
            },
            {
                "id": working_page,
                "resource_type": "page",
                "path": "source-working-copy/00-InsertedFile-Canvas/01-Interactive-Canvas",
                "section_id": "working-section",
                "parent_id": "working-section",
                "order": 0,
                "page_level": 1,
            },
        ],
        "page_objects": {
            working_page: [
                {"kind": "Outline"},
                {"kind": "OE"},
                {"kind": "InsertedFile"},
            ]
        },
        "page_capability_projections": {
            working_page: {
                "schema_version": 1,
                "capabilities": ["InsertedFile", "Outline"],
                "object_kind_counts": {"InsertedFile": 1, "OE": 1, "Outline": 1},
                "unknown_nodes": [],
                "unsupported_page_roots": [],
                "complete": True,
            }
        },
        "page_hashes": {working_page: "hash"},
    }

    async def fake_snapshot(_client, _notebook_id):
        return snapshot

    async def fake_convergence(*_args, **_kwargs):
        rebound = {
            "canvas_section": snapshot["items"][0],
            "canvas_page": snapshot["items"][1],
        }
        return (
            {"items": snapshot["items"]},
            rebound,
            {"passed": True, "mappings": [], "failures": []},
            {
                "passed": True,
                "phase": "hierarchy_convergence",
                "scenario_before_snapshot_started": False,
                "scenario_before_snapshot_completed": False,
            },
        )

    monkeypatch.setattr(
        "tests.manual_validation.scenarios.common.fixture_runtime.capture_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr(
        "tests.manual_validation.scenarios.common.fixture_runtime._await_materialized_structure_convergence",
        fake_convergence,
    )
    hit = CacheHit(
        scenario.fixture_recipe.cache_fingerprint,
        scenario.fixture_recipe.default_template_instance_id,
        tmp_path / "entry",
        {"roles": ["source"]},
    )
    working = tmp_path / "run" / "notebooks" / "source-working-copy"
    template = tmp_path / "cache" / "template"
    working.mkdir(parents=True)
    template.mkdir(parents=True)
    materialized = MaterializedBundle(
        hit.fingerprint,
        hit.template_instance_id,
        {"source": template},
        {"source": working},
        tmp_path / "run" / "cache-materialization.json",
    )
    args = argparse.Namespace()

    manifest, result = asyncio.run(
        prepare_materialized_fixture(
            scenario,
            args,
            RuntimeOptions(tmp_path / "run", 180, False, False, use_cache=True),
            object(),
            {"id": "working-notebook", "name": "source-working-copy", "path": "source-working-copy"},
            str(working),
            scenario.spec,
            hit,
            materialized,
        )
    )

    assert manifest["structure"]["canvas_page"]["id"] == working_page
    assert manifest["fixture_cache"]["interactive_live_validation"]["passed"] is True
    assert result["structure_ids"]["canvas_page"] == working_page
    assert read_json(tmp_path / "run" / "cache-structure-remap.json")["passed"] is True


def test_fixed_invalidation_scenario_exposes_no_arbitrary_entry_selector() -> None:
    from tests.manual_validation.runner import build_parser

    parser = build_parser()
    args = parser.parse_args(["cache-invalidation", "--use-cache", "--dry-run"])
    assert args.use_cache is True
    assert SCENARIO_REGISTRY.get("cache-invalidation").included_in_all is False
    assert SCENARIO_REGISTRY.get("cache-invalidation").fixture_recipe.invalidation_probe is True
    for unsafe in ("--fingerprint", "--template-path", "--notebook-id"):
        with pytest.raises(SystemExit):
            parser.parse_args(["cache-invalidation", unsafe, "value", "--dry-run"])


def test_inserted_file_copy_has_copy_only_policy_and_shared_cache_identity() -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-copy-inserted-file")
    recipe = scenario.fixture_recipe

    assert recipe.default_template_instance_id.startswith("programmatic-")
    assert len(recipe.default_template_instance_id) == len("programmatic-") + 16
    assert recipe.cache_fingerprint
    assert scenario.spec.policy.writes_enabled is True
    assert scenario.spec.policy.create_enabled is True
    assert scenario.spec.policy.deletes_enabled is False
    assert "copy_page" in scenario.spec.tool_allowlist
    assert "plan_copy" not in scenario.spec.tool_allowlist
    assert not any(tool.startswith("delete_") for tool in scenario.spec.tool_allowlist)
    assert scenario.spec.fixture.creation_tools <= scenario.spec.tool_allowlist


def test_programmatic_interactive_bootstrap_publishes_null_template_instance(
    monkeypatch,
    tmp_path,
) -> None:
    scenario = SCENARIO_REGISTRY.get("interactive-copy-inserted-file")
    recipe = scenario.fixture_recipe
    run_dir = tmp_path / "run-programmatic-bootstrap"
    source_path = run_dir / "notebooks" / "source"
    source_path.mkdir(parents=True)
    write_json(
        run_dir / "manifest.json",
        {"fixture_validation": {"status": "passed"}},
    )
    published: dict = {}
    cache_hit = CacheHit(
        recipe.cache_fingerprint,
        recipe.default_template_instance_id,
        tmp_path / "cache-entry",
        {},
    )
    materialized = MaterializedBundle(
        recipe.cache_fingerprint,
        recipe.default_template_instance_id,
        {"source": tmp_path / "template"},
        {"source": tmp_path / "working"},
        tmp_path / "materialized.json",
    )

    class Lock:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class CacheStore:
        def lock(self, *_args, **_kwargs):
            return Lock()

        def publish(self, *_args, **kwargs):
            published.update(kwargs)
            return cache_hit

    monkeypatch.setattr(validation, "_close_bundle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(validation, "bundle_cache_artifacts", lambda *_args: {})
    monkeypatch.setattr(
        validation,
        "_resolve_exact_cache_entry",
        lambda *_args, **_kwargs: (None, None, False),
    )
    monkeypatch.setattr(
        validation,
        "_materialize_with_budget_context",
        lambda *_args, **_kwargs: materialized,
    )
    monkeypatch.setattr(
        validation,
        "_open_materialized_bundle",
        lambda *_args, **_kwargs: (
            {"source": {"id": "working-id", "name": "Working"}},
            {"source": {"expected_local_path": str(tmp_path / "working")}},
        ),
    )
    args = argparse.Namespace(notebook_name="Fresh", cached_notebook_names=None)
    bootstrap_result = {
        "template_instance_id": recipe.default_template_instance_id,
        "template_state": "ready",
        "template_instance": None,
    }

    result = asyncio.run(
        validation._publish_interactive_bootstrap_template(
            scenario=scenario,
            args=args,
            options=RuntimeOptions(run_dir, 1_800, False, False),
            bootstrap_result=bootstrap_result,
            manifest={},
            fixture_result={},
            wrappers={},
            roles=("source",),
            notebooks={"source": {"id": "fresh-id", "name": "Fresh"}},
            leases={"source": {"expected_local_path": str(source_path)}},
            cache_store=CacheStore(),
            wrapper=type(
                "Wrapper",
                (),
                {"any_cache_template_open": staticmethod(lambda _entry: False)},
            )(),
        )
    )

    assert result[0] is cache_hit
    assert published["projection_digest"] is None
    assert published["mutation_eligible"] is None
    assert published["move_source_deletion_allowed"] is None
    assert bootstrap_result["template_published"] is True
    assert args.notebook_name == "Working"


def test_inserted_file_copy_cache_miss_fails_before_notebook_open(
    tmp_path,
) -> None:
    run_dir = tmp_path / "run"
    args = argparse.Namespace(
        command="interactive-copy-inserted-file",
        scenario="interactive-copy-inserted-file",
        notebook_name="Disposable",
        run_dir=run_dir,
        timeout=180,
        dry_run=False,
        json_output=False,
        keep_notebook=False,
        keep_worksite=False,
    )

    with pytest.raises(
        RunnerFailure,
        match="interactive_cache_miss",
    ):
        asyncio.run(
            run_validate(
                args,
                RuntimeOptions(
                    run_dir,
                    180,
                    False,
                    False,
                    use_cache=True,
                    cache_root=tmp_path / "cache",
                ),
            )
        )

    assert not (run_dir / "lifecycle-lease.json").exists()
    assert not (run_dir / "scenario-mcp").exists()
    assert not (run_dir / "notebooks").exists()


@pytest.mark.parametrize(
    ("invalid_timing", "expected_decision"),
    [
        ("none", "cold_build"),
        ("before_lookup", "invalidated_rebuild"),
        ("before_publish", "invalidated_rebuild"),
    ],
)
def test_programmatic_cold_build_adopts_materialized_working_notebook_name(
    monkeypatch,
    tmp_path,
    invalid_timing,
    expected_decision,
) -> None:
    run_dir = tmp_path / "run"
    cache_root = tmp_path / "cache"
    identity = new_run_identity(
        datetime(2026, 8, 11, 11, 5, 49, 123_000, tzinfo=timezone(timedelta(hours=8)))
    )
    fresh_names = validation_notebook_names(
        "copy-notebook", identity, ("source",), cached=False
    )
    cached_names = validation_notebook_names(
        "copy-notebook", identity, ("source",), cached=True
    )
    initial_name = fresh_names["source"]
    working_name = cached_names["source"]
    recipe = SCENARIO_REGISTRY.get("copy-notebook").fixture_recipe
    seed_store = BundleCacheStore(cache_root)
    seed_store.initialize()
    seed_hit: CacheHit | None = None

    def seed_invalid_entry() -> None:
        nonlocal seed_hit
        if seed_hit is not None:
            return
        seed_hit = seed_store.publish(
            recipe,
            recipe.default_template_instance_id,
            source_paths={"source": _source(tmp_path / "seed")},
            source_notebooks={
                "source": {"id": "seed-template-id", "name": "Seed"}
            },
            closed_roles={"source"},
            validation={"passed": True},
        )
        seed_store.quarantine_exact(
            recipe,
            recipe.default_template_instance_id,
            reason="materialized-live-validation failed: injected mismatch",
            run_id="run-seed-invalid",
        )
        assert seed_hit.entry_path.exists()

    if invalid_timing == "before_lookup":
        seed_invalid_entry()

    class FakeLifecycle:
        def __init__(self, run_dir: Path, *, timeout_seconds: int) -> None:
            self.run_dir = run_dir
            self.timeout_seconds = timeout_seconds
            self.lease_path = run_dir / "lifecycle-lease.json"
            self.current_notebook: dict[str, str] | None = None
            self.materialized_open_count = 0

        def create_fresh_notebook(self, name: str):
            path = (self.run_dir / "notebooks" / name).resolve()
            path.mkdir(parents=True)
            (path / "Section.one").write_bytes(b"opaque-section")
            notebook = {"id": "cold-source-id", "name": name, "path": name}
            lease = {
                "notebook_id": notebook["id"],
                "expected_name": name,
                "expected_local_path": str(path),
            }
            self.current_notebook = notebook
            write_json(self.lease_path, {"schema_version": 1, **lease})
            return notebook, lease

        def open_working_notebook(
            self,
            expected_name,
            working_path,
            *,
            template_paths,
        ):
            assert expected_name == working_name
            assert working_path.name == working_name
            assert working_path not in template_paths
            self.materialized_open_count += 1
            assert self.materialized_open_count == 1
            notebook = {
                "id": "working-id",
                "name": working_name,
                "path": working_name,
            }
            lease = {
                "notebook_id": notebook["id"],
                "expected_name": working_name,
                "expected_local_path": str(working_path.resolve()),
                "actual_local_path": str(working_path.resolve()),
                "hierarchy_open_status": "passed",
                "opened_hierarchy": [{}],
            }
            self.current_notebook = notebook
            write_json(self.lease_path, {"schema_version": 1, **lease})
            return notebook, lease

        def close_exact_notebook(self, *, sync_to_disk=False):
            assert self.current_notebook is not None
            return {
                "closed": True,
                "source_notebook_id": self.current_notebook["id"],
                "close_before": dict(self.current_notebook),
            }

        def any_cache_template_open(self, _entry) -> bool:
            return False

        def working_notebook_open_lock(self):
            from contextlib import nullcontext

            return nullcontext()

        def snapshot_open_notebooks(self):
            return {}

        def assert_no_active_working_conflict(self, **_kwargs) -> None:
            return None

    class FakeMCP:
        def __init__(self, **_kwargs) -> None:
            self.staged_before = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stage_scenario_before_snapshots(self, snapshots, notebook_ids, evidence_path):
            self.staged_before = (snapshots, notebook_ids, evidence_path)

    async def fake_prepare_fixture(
        _scenario,
        _args,
        options,
        _client,
        notebook,
        notebook_path,
        _spec,
    ):
        if invalid_timing == "before_publish":
            seed_invalid_entry()
        manifest = {
            "notebook": dict(notebook),
            "notebooks": {"source": dict(notebook)},
            "structure": {},
            "role_structures": {"source": {}},
            "disposable_targets": {"source_notebook_path": notebook_path},
            "fixture_validation": {"passed": True},
        }
        write_json(options.run_dir / "manifest.json", manifest)
        write_json(options.run_dir / "fixture-snapshot.json", {"items": []})
        role_result = {"notebook": dict(notebook), "validation": {"passed": True}}
        return manifest, {
            "notebook": dict(notebook),
            "roles": {"source": role_result},
            "validation": {"passed": True},
        }

    async def fake_prepare_materialized_fixture(
        _scenario,
        args,
        options,
        _client,
        notebook,
        notebook_path,
        _spec,
        _hit,
        _materialized,
    ):
        assert args.notebook_name == working_name
        manifest = {
            "notebook": dict(notebook),
            "structure": {},
            "disposable_targets": {"source_notebook_path": notebook_path},
            "fixture_validation": {"passed": True},
            "fixture_cache": {},
        }
        write_json(options.run_dir / "manifest.json", manifest)
        write_json(
            options.run_dir / "fixture-snapshot.json",
            {"notebook_id": notebook["id"], "items": [], "page_hashes": {}},
        )
        return manifest, {"notebook": dict(notebook), "validation": {"passed": True}}

    async def fake_prepare_fixture_bundle(
        scenario, args, options, client, notebooks, notebook_paths, spec
    ):
        return await fake_prepare_fixture(
            scenario,
            args,
            options,
            client,
            notebooks["source"],
            notebook_paths["source"],
            spec,
        )

    async def fake_prepare_materialized_fixture_bundle(
        scenario,
        args,
        options,
        client,
        notebooks,
        notebook_paths,
        spec,
        hit,
        materialized,
    ):
        return await fake_prepare_materialized_fixture(
            scenario,
            args,
            options,
            client,
            notebooks["source"],
            notebook_paths["source"],
            spec,
            hit,
            materialized,
        )

    async def fake_execute(args, _options, manifest, **_kwargs):
        assert args.notebook_name == working_name
        assert manifest["notebook"]["name"] == working_name
        return {"status": "passed"}

    monkeypatch.setattr(validation, "NotebookLifecycleWrapper", FakeLifecycle)
    monkeypatch.setattr(validation, "MCPStdioClient", FakeMCP)
    monkeypatch.setattr(
        validation,
        "prepare_fixture_bundle",
        fake_prepare_fixture_bundle,
    )
    monkeypatch.setattr(
        validation,
        "prepare_materialized_fixture_bundle",
        fake_prepare_materialized_fixture_bundle,
    )
    monkeypatch.setattr(validation, "render_report", lambda path: path / "report.md")
    monkeypatch.setattr(SCENARIO_REGISTRY.get("copy-notebook"), "execute", fake_execute)

    args = argparse.Namespace(
        command="copy-notebook",
        scenario="copy-notebook",
        notebook_name=initial_name,
        run_dir=run_dir,
        timeout=1_800,
        dry_run=False,
        json_output=False,
        keep_notebook=False,
        keep_worksite=False,
        run_identity=identity,
        fresh_notebook_names=fresh_names,
        cached_notebook_names=cached_names,
    )
    result = asyncio.run(
        run_validate(
            args,
            RuntimeOptions(
                run_dir,
                1_800,
                False,
                False,
                use_cache=True,
                cache_root=cache_root,
            ),
        )
    )

    assert result["status"] == "passed"
    assert result["notebook_name"] == working_name
    assert result["cache"]["decision"] == expected_decision
    if invalid_timing != "none":
        assert seed_hit is not None
        assert seed_store.exact_entry_state(
            recipe, recipe.default_template_instance_id
        ) == "ready"
        tombstones = [
            json.loads(line)
            for line in seed_store.tombstone_path.read_text(encoding="utf-8").splitlines()
        ]
        assert tombstones[-1]["fingerprint"] == recipe.cache_fingerprint
        assert (
            tombstones[-1]["template_instance_id"]
            == recipe.default_template_instance_id
        )
        assert tombstones[-1]["deleted"] is True
        assert Path(tombstones[-1]["target"]) == seed_hit.entry_path


def test_multi_role_bundle_uses_the_same_publish_and_materialize_runtime(tmp_path) -> None:
    profile = SCENARIO_REGISTRY.get("copy-page").fixture_profile

    class MultiRoleRecipe(RecipeBase):
        async def build(self, context):  # pragma: no cover - cache contract only
            raise AssertionError("cache test does not build live OneNote fixtures")

        def validate(self, context, build):
            return ("recording validator",)

    recipe = MultiRoleRecipe(
        "copy-page",
        notebook_roles=(
            NotebookRoleSpec("destination", profile, {"manifest_keys": ["destination"]}),
            NotebookRoleSpec("source", profile, {"manifest_keys": ["source"]}),
        ),
    )
    store = BundleCacheStore(tmp_path / "cache")
    store.initialize()
    sources = {
        "destination": _source(tmp_path, "destination"),
        "source": _source(tmp_path, "source"),
    }
    hit = store.publish(
        recipe,
        recipe.default_template_instance_id,
        source_paths=sources,
        source_notebooks={
            "destination": {"id": "destination-id", "name": "Destination"},
            "source": {"id": "source-id", "name": "Source"},
        },
        closed_roles={"destination", "source"},
        validation={"passed": True},
    )
    materialized = store.materialize(hit, tmp_path / "run")
    for role in ("destination", "source"):
        store.record_opened_working_role(
            materialized,
            role=role,
            notebook_id=f"{role}-id",
            actual_path=materialized.working_paths[role],
        )
    assert store.verify_templates_unchanged(materialized)["all_templates_unchanged"] is True


def test_multi_role_materialize_preserves_competing_destination(
    tmp_path,
    monkeypatch,
) -> None:
    profile = SCENARIO_REGISTRY.get("copy-page").fixture_profile

    class MultiRoleRecipe(RecipeBase):
        async def build(self, context):  # pragma: no cover - cache contract only
            raise AssertionError("cache test does not build live OneNote fixtures")

        def validate(self, context, build):
            return ("recording validator",)

    recipe = MultiRoleRecipe(
        "copy-page",
        notebook_roles=(
            NotebookRoleSpec("destination", profile, {"manifest_keys": ["destination"]}),
            NotebookRoleSpec("source", profile, {"manifest_keys": ["source"]}),
        ),
    )
    store = BundleCacheStore(tmp_path / "cache")
    store.initialize()
    hit = store.publish(
        recipe,
        recipe.default_template_instance_id,
        source_paths={
            "destination": _source(tmp_path, "destination"),
            "source": _source(tmp_path, "source"),
        },
        source_notebooks={
            "destination": {"id": "destination-id", "name": "Destination"},
            "source": {"id": "source-id", "name": "Source"},
        },
        closed_roles={"destination", "source"},
        validation={"passed": True},
    )
    original = fixture_cache_module.atomic_replace_with_retry
    directory_publications = 0

    def competing_replace(source, destination, **kwargs) -> None:
        nonlocal directory_publications
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path.parent.name.startswith(".m-"):
            directory_publications += 1
            if directory_publications == 2:
                destination_path.mkdir()
                (destination_path / "competitor.txt").write_text(
                    "not-owned-by-materialize",
                    encoding="utf-8",
                )
                raise InvariantFailure("injected competing destination")
        original(source_path, destination_path, **kwargs)

    monkeypatch.setattr(
        fixture_cache_module,
        "atomic_replace_with_retry",
        competing_replace,
    )
    run_dir = tmp_path / "run"

    with pytest.raises(InvariantFailure, match="competing destination"):
        store.materialize(hit, run_dir)

    first_owned = run_dir / "notebooks" / "destination-working-copy"
    competitor = run_dir / "notebooks" / "source-working-copy"
    assert not first_owned.exists()
    assert (competitor / "competitor.txt").read_text(encoding="utf-8") == (
        "not-owned-by-materialize"
    )
    assert not list(run_dir.glob(".m-*"))
