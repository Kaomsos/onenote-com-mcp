"""Windows UTF-16 path-budget and short cache-schema contracts."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.manual_validation import local_filesystem
from tests.manual_validation.path_budget import (
    MAX_MANAGED_PATH_UNITS,
    MAX_RUN_EVIDENCE_LEAF_UNITS,
    fingerprint_disk_key,
    managed_absolute,
    preflight_path,
    remediation_for,
    validate_role,
    validate_run_evidence_leaf,
    validate_physical_name_has_no_onenote_id,
    validate_working_name,
    windows_path_units,
)
from tests.manual_validation.runner import main
from tests.manual_validation.runtime import (
    EXIT_INVARIANT,
    PathBudgetFailure,
    RunnerFailure,
)
from tests.manual_validation.scenarios.common.fixture_cache import BundleCacheStore
from tests.manual_validation.scenarios.common import fixture_cache as fixture_cache_module
from tests.manual_validation.scenarios.common.orchestrator import record_failure
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.common.specs import SCENARIO_SPECS
from tests.manual_validation.test_utils import read_json, write_json


@pytest.fixture
def cache_tmp_path(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("fc")


def _source(root: Path, *, relative: Path | None = None) -> Path:
    source = root / "closed-source"
    source.mkdir(parents=True)
    (source / "Open Notebook.onetoc2").write_bytes(b"opaque-catalog")
    target = source / (relative or Path("Section.one"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"opaque-section")
    return source


def _path_at_units(base: Path, target_units: int) -> Path:
    path = managed_absolute(base)
    while windows_path_units(path) < target_units:
        remaining = target_units - windows_path_units(path)
        component_units = min(120, remaining - 1)
        if component_units < 1:
            raise AssertionError("Cannot construct requested exact path budget.")
        path /= "x" * component_units
    assert windows_path_units(path) == target_units
    return path


def _publish(root: Path):
    recipe = SCENARIO_REGISTRY.get("copy-notebook").fixture_recipe
    store = BundleCacheStore(root / "cache")
    store.initialize()
    hit = store.publish(
        recipe,
        recipe.default_template_instance_id,
        source_paths={"source": _source(root)},
        source_notebooks={"source": {"id": "closed-id", "name": "Disposable"}},
        closed_roles={"source"},
        validation={"passed": True},
    )
    return recipe, store, hit


def test_utf16_units_cover_ascii_bmp_and_surrogate_pairs() -> None:
    assert windows_path_units("A") == 1
    assert windows_path_units("汉") == 1
    assert windows_path_units("😀") == 2
    assert windows_path_units("A汉😀") == 4


def test_managed_path_exact_239_240_241_boundaries(cache_tmp_path) -> None:
    for units in (239, 240):
        evidence = preflight_path(
            _path_at_units(cache_tmp_path, units),
            phase="boundary_preflight",
            target_kind="working_copy",
        )
        assert evidence.longest_path_utf16 == units
        assert evidence.remaining_utf16 == MAX_MANAGED_PATH_UNITS - units
    with pytest.raises(PathBudgetFailure) as captured:
        preflight_path(
            _path_at_units(cache_tmp_path, 241),
            phase="boundary_preflight",
            target_kind="working_copy",
        )
    assert captured.value.actual_utf16 == 241
    assert captured.value.over_by_utf16 == 1


@pytest.mark.parametrize(
    "target_kind",
    (
        "cache_publish_staging",
        "materialize_staging",
        "cache_template_source",
        "cache_tombstone_evidence",
        "maintenance_metadata",
    ),
)
def test_shared_preflight_enforces_operation_boundaries(
    cache_tmp_path,
    target_kind,
) -> None:
    for units in (239, 240):
        assert preflight_path(
            _path_at_units(cache_tmp_path, units),
            phase=f"{target_kind}_preflight",
            target_kind=target_kind,
        ).passed
    with pytest.raises(PathBudgetFailure) as captured:
        preflight_path(
            _path_at_units(cache_tmp_path, 241),
            phase=f"{target_kind}_preflight",
            target_kind=target_kind,
        )
    assert captured.value.over_by_utf16 == 1


def test_role_disk_key_and_typed_remediation_contracts() -> None:
    assert validate_role("r12345678901") == "r12345678901"
    with pytest.raises(ValueError, match="0,11"):
        validate_role("r123456789012")
    assert fingerprint_disk_key("a" * 64) == "a" * 32
    assert remediation_for("cache_root")["code"] == "shorten_repository_path"
    assert remediation_for("run_root")["code"] == "use_shorter_unique_run_dir"
    assert remediation_for("opaque_relative_path")["code"] == (
        "shorten_disposable_fixture_hierarchy"
    )
    assert remediation_for("working_name")["code"] == "fix_typed_path_contract"
    assert remediation_for("legacy_schema")["code"] == (
        "clear_legacy_with_previous_version"
    )


def test_working_name_counts_utf16_and_enforces_64_units() -> None:
    assert validate_working_name("😀" * 32) == "😀" * 32
    with pytest.raises(PathBudgetFailure) as captured:
        validate_working_name("😀" * 33)
    assert captured.value.target_kind == "working_name"
    assert captured.value.limit_utf16 == 64
    assert captured.value.actual_utf16 == 66


def test_run_evidence_leaf_limit_and_dispatch_reserve(cache_tmp_path, capsys) -> None:
    assert validate_run_evidence_leaf(
        cache_tmp_path / ("e" * MAX_RUN_EVIDENCE_LEAF_UNITS)
    ).name == "e" * 64
    with pytest.raises(PathBudgetFailure) as captured:
        validate_run_evidence_leaf(cache_tmp_path / ("e" * 65))
    assert captured.value.target_kind == "run_evidence_name"
    assert captured.value.limit_utf16 == 64

    run_root = _path_at_units(cache_tmp_path / "evidence-reserve", 155)
    assert main(
        [
            "copy-page",
            "--run-dir",
            str(run_root),
            "--use-cache",
            "--dry-run",
            "--json",
        ]
    ) != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_type"] == "path_budget_exceeded"
    assert payload["target_kind"] == "run_evidence_temp"
    assert payload["filesystem_changes_started"] is False
    assert not run_root.exists()


def test_generated_physical_names_reject_canonical_onenote_object_ids(
    cache_tmp_path,
) -> None:
    object_id = "{01234567-89AB-CDEF-0123-456789ABCDEF}{1}{E12345}"
    with pytest.raises(RunnerFailure, match="must remain in JSON evidence"):
        validate_run_evidence_leaf(cache_tmp_path / f"result-{object_id}.json")
    with pytest.raises(RunnerFailure, match="must remain in JSON evidence"):
        validate_physical_name_has_no_onenote_id(f"working-{object_id}")


def test_manual_validation_source_never_interpolates_ids_into_physical_names() -> None:
    root = Path(__file__).parents[1]
    violations: set[str] = set()
    local_id_names = {
        "case_id",
        "instance_id",
        "logical_instance_id",
        "receipt_id",
        "run_id",
        "selected_instance_id",
        "template_instance_id",
    }

    def references_id(node: ast.AST) -> bool:
        for candidate in ast.walk(node):
            if isinstance(candidate, (ast.Name, ast.Attribute)):
                name = candidate.id if isinstance(candidate, ast.Name) else candidate.attr
                if name == "id" or (name.endswith("_id") and name not in local_id_names):
                    return True
            if isinstance(candidate, ast.Subscript):
                key = candidate.slice
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and (
                        key.value == "id"
                        or (
                            key.value.endswith("_id")
                            and key.value not in local_id_names
                        )
                    )
                ):
                    return True
        return False

    def physical_context(node: ast.JoinedStr, parents: dict[ast.AST, ast.AST]) -> bool:
        literal = "".join(
            value.value for value in node.values if isinstance(value, ast.Constant)
        ).casefold()
        if any(
            marker in literal
            for marker in (".json", ".jsonl", ".xml", ".md", ".tmp", ".one", ".onetoc2")
        ):
            return True
        current: ast.AST = node
        for _ in range(4):
            parent = parents.get(current)
            if parent is None:
                return False
            if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div):
                return True
            if isinstance(parent, ast.Call):
                name = (
                    parent.func.attr
                    if isinstance(parent.func, ast.Attribute)
                    else parent.func.id
                    if isinstance(parent.func, ast.Name)
                    else ""
                )
                if name in {
                    "Path",
                    "joinpath",
                    "with_name",
                    "write_json",
                    "write_sensitive_page_xml",
                    "call_with_result_evidence",
                }:
                    return True
            current = parent
        return False

    for source in sorted(root.rglob("*.py")):
        if "tests" in source.relative_to(root).parts:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        relative_source = source.relative_to(root)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.JoinedStr)
                and references_id(node)
                and physical_context(node, parents)
            ):
                violations.add(f"{relative_source}:{node.lineno}")
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Div)
                and references_id(node.right)
            ):
                violations.add(f"{relative_source}:{node.lineno}")
            if isinstance(node, ast.Call):
                call_name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                path_arguments: list[ast.AST] = []
                if call_name in {"Path", "joinpath", "with_name"}:
                    path_arguments.extend(node.args)
                elif call_name in {"write_json", "write_sensitive_page_xml"}:
                    path_arguments.extend(node.args[:1])
                elif call_name == "call_with_result_evidence":
                    path_arguments.extend(node.args[3:4])
                path_arguments.extend(
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg in {"path", "evidence_path"}
                )
                if any(references_id(argument) for argument in path_arguments):
                    violations.add(f"{relative_source}:{node.lineno}")

    assert sorted(violations) == []


def test_declared_dynamic_evidence_names_fit_reserved_leaf_contract() -> None:
    case_names = {
        str(case["name"])
        for spec in SCENARIO_SPECS.values()
        for case in spec.execution_contract.get("cases", [])
        if isinstance(case, dict) and isinstance(case.get("name"), str)
    }
    stems = {
        "after",
        "before",
        "copy-result",
        "destination-position-evidence",
        "machine-comparison",
        "mutation-response",
        "partial-result-admission",
        "plan-attempts",
        "request",
        "sensitive-evidence",
        "source-detection",
    }
    generated = {
        f"{stem}-{case_name}.json"
        for stem in stems
        for case_name in case_names
    }
    generated.update(
        f"{stem}-cross-section-chain-5.json"
        for stem in stems
    )
    generated.update(
        f"cleanup-created-page-{ordinal:02d}-result.json"
        for ordinal in (1, 2)
    )

    assert generated
    assert max(windows_path_units(name) for name in generated) <= (
        MAX_RUN_EVIDENCE_LEAF_UNITS
    )


def test_short_programmatic_and_authored_disk_locations_preserve_full_identity(
    cache_tmp_path,
) -> None:
    recipe, store, programmatic = _publish(cache_tmp_path)
    assert programmatic.entry_path == (
        store.cache_root
        / fingerprint_disk_key(recipe.cache_fingerprint)
        / "instances"
        / "p"
    )
    assert programmatic.entry["fingerprint"] == recipe.cache_fingerprint
    assert programmatic.entry["instance_location"]["kind"] == "programmatic"

    authored_id = f"authored-{'a' * 24}"
    authored_digest = "a" * 64
    authored = store.publish(
        recipe,
        authored_id,
        source_paths={"source": _source(cache_tmp_path / "authored")},
        source_notebooks={"source": {"id": "authored-id", "name": "Authored"}},
        closed_roles={"source"},
        validation={"passed": True},
        projection_digest=authored_digest,
    )
    assert authored.entry_path == (
        store.cache_root
        / fingerprint_disk_key(recipe.cache_fingerprint)
        / "instances"
        / "a"
        / ("a" * 24)
    )
    assert authored.entry["instance_location"]["projection_digest"] == authored_digest
    with pytest.raises(RunnerFailure, match="does not match"):
        store.publish(
            recipe,
            f"authored-{'b' * 24}",
            source_paths={"source": _source(cache_tmp_path / "authored-mismatch")},
            source_notebooks={"source": {"id": "mismatch", "name": "Mismatch"}},
            closed_roles={"source"},
            validation={"passed": True},
            projection_digest="c" * 64,
        )


def test_fingerprint_disk_key_collision_fails_closed(cache_tmp_path, monkeypatch) -> None:
    recipe, store, _hit = _publish(cache_tmp_path)
    colliding = recipe.cache_fingerprint[:32] + ("f" * 32)
    if colliding == recipe.cache_fingerprint:
        colliding = recipe.cache_fingerprint[:32] + ("e" * 32)
    monkeypatch.setattr(recipe, "cache_fingerprint", colliding)
    with pytest.raises(RunnerFailure, match="disk-key collision"):
        store.lookup(recipe, recipe.default_template_instance_id)


def test_programmatic_single_disk_location_collision_fails_closed(cache_tmp_path) -> None:
    recipe, store, _hit = _publish(cache_tmp_path)
    colliding_instance = "programmatic-0000000000000000"
    if colliding_instance == recipe.default_template_instance_id:
        colliding_instance = "programmatic-ffffffffffffffff"

    with pytest.raises(RunnerFailure, match="identity or schema"):
        store.lookup(recipe, colliding_instance)


@pytest.mark.parametrize("metadata", ("entry", "index"))
def test_full_identity_mismatch_in_entry_or_index_fails_closed(
    cache_tmp_path,
    metadata,
) -> None:
    recipe, store, hit = _publish(cache_tmp_path)
    path = (
        hit.entry_path / "bundle-entry.json"
        if metadata == "entry"
        else store.cache_root / "index.json"
    )
    payload = read_json(path)
    if metadata == "entry":
        payload["fingerprint"] = recipe.cache_fingerprint[:32] + ("0" * 32)
    else:
        key = f"{recipe.cache_fingerprint}:{recipe.default_template_instance_id}"
        payload["entries"][key]["instance_location"] = {
            **payload["entries"][key]["instance_location"],
            "logical_instance_id": "programmatic-0000000000000000",
        }
    write_json(path, payload)
    with pytest.raises(RunnerFailure, match="identity|collision"):
        store.lookup(recipe, recipe.default_template_instance_id)


def test_publish_preflight_rejects_long_final_path_before_staging(cache_tmp_path) -> None:
    recipe = SCENARIO_REGISTRY.get("copy-notebook").fixture_recipe
    long_cache_root = _path_at_units(cache_tmp_path / "long-cache", 175)
    store = BundleCacheStore(long_cache_root)
    store.initialize()
    source = _source(cache_tmp_path / "publish-source")

    with pytest.raises(PathBudgetFailure) as captured:
        store.publish(
            recipe,
            recipe.default_template_instance_id,
            source_paths={"source": source},
            source_notebooks={"source": {"id": "closed-id", "name": "Disposable"}},
            closed_roles={"source"},
            validation={"passed": True},
        )

    assert captured.value.phase == "cache_publish_preflight"
    assert captured.value.cache_entry_published is False
    assert captured.value.onenote_opened is False
    assert not list(store.cache_root.glob(".s-*"))
    assert not (store.cache_root / fingerprint_disk_key(recipe.cache_fingerprint)).exists()


def test_materialize_preflight_rejects_long_working_path_before_run_creation(
    cache_tmp_path,
) -> None:
    _recipe, store, hit = _publish(cache_tmp_path)
    long_run_root = _path_at_units(cache_tmp_path / "long-run", 170)
    with pytest.raises(PathBudgetFailure) as captured:
        store.materialize(
            hit,
            long_run_root,
            working_names={"source": "w" * 64},
        )
    assert captured.value.phase == "materialize_preflight"
    assert captured.value.target_kind == "working_copy"
    assert not long_run_root.exists()


def test_publish_and_materialize_staging_nonce_collisions_allocate_new_names(
    cache_tmp_path,
    monkeypatch,
) -> None:
    values = iter([f"{value:x}" * 32 for value in range(1, 16)])
    monkeypatch.setattr(
        fixture_cache_module.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex=next(values)),
    )
    recipe = SCENARIO_REGISTRY.get("copy-notebook").fixture_recipe
    store = BundleCacheStore(cache_tmp_path / "collision-cache")
    store.initialize()
    (store.cache_root / f".s-{'2' * 16}").mkdir()
    hit = store.publish(
        recipe,
        recipe.default_template_instance_id,
        source_paths={"source": _source(cache_tmp_path / "collision-source")},
        source_notebooks={"source": {"id": "closed-id", "name": "Disposable"}},
        closed_roles={"source"},
        validation={"passed": True},
    )
    marker = read_json(hit.entry_path / "staging-marker.json")
    assert marker["staging_name"] == f".s-{'3' * 16}"

    run_dir = cache_tmp_path / "collision-run"
    run_dir.mkdir()
    (run_dir / f".m-{'8' * 16}").mkdir()
    materialized = store.materialize(hit, run_dir)
    assert materialized.working_paths["source"].is_dir()
    assert (run_dir / f".m-{'8' * 16}").is_dir()


@pytest.mark.parametrize(
    ("relative", "target_kind"),
    (
        (Path("x" * 90) / "Section.one", "opaque_relative_path"),
        (Path("x" * 121), "opaque_component"),
        (Path("a/b/c/d/e/f/g/h/Section.one"), "opaque_hierarchy_depth"),
    ),
)
def test_opaque_relative_path_limits_fail_before_publish_staging(
    cache_tmp_path,
    relative,
    target_kind,
) -> None:
    recipe = SCENARIO_REGISTRY.get("copy-notebook").fixture_recipe
    store = BundleCacheStore(cache_tmp_path / target_kind / "cache")
    store.initialize()
    source = _source(cache_tmp_path / target_kind, relative=relative)
    with pytest.raises(PathBudgetFailure) as captured:
        store.publish(
            recipe,
            recipe.default_template_instance_id,
            source_paths={"source": source},
            source_notebooks={"source": {"id": "closed-id", "name": "Disposable"}},
            closed_roles={"source"},
            validation={"passed": True},
        )
    assert captured.value.target_kind == target_kind
    assert not list(store.cache_root.glob(".s-*"))


def test_path_budget_cli_json_and_terminal_contracts(cache_tmp_path, capsys) -> None:
    long_run = _path_at_units(cache_tmp_path / "cli", 241)
    args = ["copy-page", "--run-dir", str(long_run), "--use-cache", "--dry-run"]
    assert main([*args, "--json"]) != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_type"] == "path_budget_exceeded"
    assert payload["limit_utf16"] == 240
    assert payload["actual_utf16"] == 241
    assert payload["over_by_utf16"] == 1
    assert payload["filesystem_changes_started"] is False
    assert payload["failure_evidence_written"] is False
    assert payload["remediation"]["code"] == "use_shorter_unique_run_dir"

    assert main(args) != 0
    terminal = capsys.readouterr().out
    assert "ERROR: Fixture cache path budget exceeded." in terminal
    assert "exceeded by: 1" in terminal
    assert "How to fix:" in terminal


def test_path_budget_failure_evidence_matches_structured_error(cache_tmp_path) -> None:
    run_dir = cache_tmp_path / "run-2026-08-13-10-00-00"
    run_dir.mkdir()
    write_json(
        run_dir / "run-state.json",
        {
            "schema_version": 2,
            "command": "copy-page",
            "scenario": "copy-page",
            "status": "running",
            "human_only": True,
            "agent_execution_prohibited": True,
            "run_dir": str(run_dir.resolve()),
            "completed_steps": [],
            "current_step": "preflight",
            "finalization_started": False,
        },
    )
    error = PathBudgetFailure(
        phase="cache_publish_preflight",
        target_kind="cache_template",
        path=_path_at_units(cache_tmp_path / "error", 241),
        actual_utf16=241,
        limit_utf16=240,
        relative_path="Section.one",
        remediation={
            "code": "shorten_repository_path",
            "message": "Move the repository to a shorter local path, then start a new run.",
        },
    )
    args = SimpleNamespace(
        command="copy-page",
        scenario="copy-page",
        run_dir=run_dir,
        notebook_name="Disposable",
    )
    record_failure(args, error, EXIT_INVARIANT)

    failure = read_json(run_dir / "run-failure.json")
    assert error.failure_evidence_written is True
    assert failure["structured_error"] == error.as_error_dict()


def test_winerror_3_is_never_retried(cache_tmp_path, monkeypatch) -> None:
    source = cache_tmp_path / "source"
    destination = cache_tmp_path / "destination"
    source.write_text("source", encoding="utf-8")
    attempts = 0
    delays: list[float] = []

    def fail_once(_source, _destination):
        nonlocal attempts
        attempts += 1
        error = FileNotFoundError("injected WinError 3")
        error.winerror = 3
        raise error

    monkeypatch.setattr(local_filesystem, "_IS_WINDOWS", True)
    monkeypatch.setattr(local_filesystem.os, "replace", fail_once)
    monkeypatch.setattr(local_filesystem.time, "sleep", delays.append)
    with pytest.raises(FileNotFoundError):
        local_filesystem.atomic_replace_with_retry(source, destination)
    assert attempts == 1
    assert delays == []
