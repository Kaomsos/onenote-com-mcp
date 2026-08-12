"""Pure contracts for user-invoked managed validation cleanup."""

from __future__ import annotations

from types import SimpleNamespace
import json
from pathlib import Path

import pytest

from tests.manual_validation.maintenance import cleanup
from tests.manual_validation.maintenance.cleanup import OpenNotebookPathSnapshot
from tests.manual_validation.runner import build_parser, main
from tests.manual_validation.runtime import EXIT_INVARIANT, RunnerFailure
from tests.manual_validation.scenarios.common.fixture_cache import BundleCacheStore
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.test_utils import write_json


def _args(action: str, *, dry_run: bool):
    return SimpleNamespace(
        command="clear",
        clear_action=action.removeprefix("clear-"),
        dry_run=dry_run,
        json_output=True,
    )


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    validation = workspace / ".local-validation"
    validation.mkdir()
    return workspace, validation


def _run(validation: Path, name: str, notebook_count: int = 1) -> Path:
    target = validation / name
    target.mkdir()
    for index in range(notebook_count):
        notebook = target / "notebooks" / f"Notebook-{index}"
        notebook.mkdir(parents=True)
        (notebook / "Open Notebook.onetoc2").write_bytes(b"catalog")
        (notebook / "Section.one").write_bytes(b"section")
    write_json(
        target / "run-state.json",
        {
            "schema_version": 1,
            "command": "copy-page",
            "scenario": "copy-page",
            "status": "running",
            "human_only": True,
            "agent_execution_prohibited": True,
            "run_dir": str(target.resolve()),
        },
    )
    return target


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "closed-source"
    source.mkdir()
    (source / "Open Notebook.onetoc2").write_bytes(b"catalog")
    (source / "Section.one").write_bytes(b"section")
    return source


def _cache(validation: Path, tmp_path: Path):
    recipe = SCENARIO_REGISTRY.get("copy-notebook").fixture_recipe
    store = BundleCacheStore(validation / "fixture-cache")
    store.initialize()
    instance_id = "x"
    hit = store.publish(
        recipe,
        instance_id,
        source_paths={"source": _source(tmp_path)},
        source_notebooks={"source": {"id": "closed-id", "name": "Disposable"}},
        closed_roles={"source"},
        validation={"passed": True},
    )
    return recipe, store, hit


def _execute(
    args,
    workspace: Path,
    validation: Path,
    *,
    snapshot: OpenNotebookPathSnapshot | None = None,
    delete_tree=None,
    confirmation: str | None = None,
):
    kwargs = {}
    if delete_tree is not None:
        kwargs["delete_tree"] = delete_tree
    if confirmation is not None:
        kwargs["confirmation_reader"] = lambda _prompt: confirmation
    return cleanup.run_maintenance(
        args,
        workspace_root=workspace,
        validation_root=validation,
        snapshot=snapshot or OpenNotebookPathSnapshot("complete"),
        **kwargs,
    )


def test_parser_registers_only_clear_group_and_three_maintenance_subactions() -> None:
    parser = build_parser()
    for action in cleanup.CONFIRMATIONS:
        subaction = action.removeprefix("clear-")
        args = parser.parse_args(["clear", subaction, "--dry-run", "--json"])
        assert args.command == "clear"
        assert args.clear_action == subaction
        assert args.dry_run is True
        assert args.json_output is True
    for forbidden in (
        "--path",
        "--force",
        "--ignore-open",
        "--run-dir",
        "--use-cache",
        "--confirm",
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(["clear", "all", forbidden, "value"])
    for removed in cleanup.CONFIRMATIONS:
        with pytest.raises(SystemExit):
            parser.parse_args([removed, "--dry-run"])


def test_real_action_requires_interactive_action_bound_confirmation(tmp_path) -> None:
    workspace, validation = _roots(tmp_path)
    target = _run(validation, "run-2026-08-12-10-00-13")
    with pytest.raises(RunnerFailure, match="interactive terminal"):
        _execute(_args("clear-runs", dry_run=False), workspace, validation)
    assert target.exists()
    with pytest.raises(RunnerFailure, match="did not match CLEAR-RUNS"):
        _execute(
            _args("clear-runs", dry_run=False),
            workspace,
            validation,
            confirmation="CLEAR-CACHE",
        )
    assert target.exists()


def test_dry_run_never_requests_confirmation(tmp_path) -> None:
    workspace, validation = _roots(tmp_path)
    _run(validation, "run-2026-08-12-10-00-14")
    result, exit_code = cleanup.run_maintenance(
        _args("clear-runs", dry_run=True),
        workspace_root=workspace,
        validation_root=validation,
        snapshot=OpenNotebookPathSnapshot("complete"),
        confirmation_reader=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("confirmation requested")
        ),
    )
    assert exit_code == 0
    assert result["confirmation_mode"] is None


def test_interactive_prompt_is_action_bound_and_shows_exact_plan_count() -> None:
    prompts: list[str] = []

    cleanup._require_interactive_confirmation(
        "clear-cache",
        planned_count=7,
        residue_count=3,
        reader=lambda prompt: prompts.append(prompt) or "CLEAR-CACHE\n",
    )

    assert prompts == [
        "clear-cache is ready to delete 7 exact managed target(s). "
        "It will also compact/prune 3 verified residue item(s). "
        "Type CLEAR-CACHE to continue: "
    ]


@pytest.mark.parametrize("notebook_count", [0, 1, 2, 4])
def test_clear_runs_dry_run_accepts_owned_plain_runs_without_count_assumptions(
    tmp_path, notebook_count
) -> None:
    workspace, validation = _roots(tmp_path)
    target = _run(validation, "run-2026-08-12-10-00-00", notebook_count)

    result, exit_code = _execute(
        _args("clear-runs", dry_run=True), workspace, validation
    )

    assert exit_code == 0, json.dumps(result, indent=2)
    assert result["counts"] == {
        "discovered": 1,
        "planned": 1,
        "deleted": 0,
        "refused": 0,
        "failed": 0,
    }
    assert result["targets"][0]["target"] == str(target.resolve())
    assert target.exists()


def test_dry_run_performs_no_managed_write_or_delete(tmp_path, monkeypatch) -> None:
    workspace, validation = _roots(tmp_path)
    target = _run(validation, "run-2026-08-12-10-00-01")
    before = sorted(str(path.relative_to(validation)) for path in validation.rglob("*"))

    monkeypatch.setattr(
        cleanup,
        "_atomic_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("write")),
    )
    result, exit_code = _execute(
        _args("clear-runs", dry_run=True),
        workspace,
        validation,
        delete_tree=lambda _path: (_ for _ in ()).throw(AssertionError("delete")),
    )

    assert exit_code == 0
    assert result["dry_run"] is True
    assert target.exists()
    assert before == sorted(str(path.relative_to(validation)) for path in validation.rglob("*"))


def test_open_run_is_refused_while_independent_run_is_planned(tmp_path) -> None:
    workspace, validation = _roots(tmp_path)
    opened = _run(validation, "run-2026-08-12-10-00-02")
    closed = _run(validation, "run-2026-08-12-10-00-03")
    snapshot = OpenNotebookPathSnapshot(
        "complete", (("open-id", opened / "notebooks" / "Notebook-0"),)
    )

    result, exit_code = _execute(
        _args("clear-runs", dry_run=True), workspace, validation, snapshot=snapshot
    )

    assert exit_code == EXIT_INVARIANT
    by_target = {item["target"]: item for item in result["targets"]}
    assert by_target[str(opened.resolve())]["reason"] == "refused_open"
    assert by_target[str(closed.resolve())]["decision"] == "would_delete"


def test_unowned_run_and_failed_snapshot_fail_closed(tmp_path) -> None:
    workspace, validation = _roots(tmp_path)
    unowned = validation / "run-2026-08-12-10-00-04"
    unowned.mkdir()
    result, exit_code = _execute(
        _args("clear-runs", dry_run=True), workspace, validation
    )
    assert exit_code == EXIT_INVARIANT
    assert result["targets"][0]["reason"] == "refused_unowned"

    _run(validation, "run-2026-08-12-10-00-05")
    failed = OpenNotebookPathSnapshot("failed", error="probe failed")
    result, exit_code = _execute(
        _args("clear-runs", dry_run=True), workspace, validation, snapshot=failed
    )
    assert exit_code == EXIT_INVARIANT
    assert all(item["decision"] == "refused" for item in result["targets"])


def test_reparse_point_run_is_refused(tmp_path, monkeypatch) -> None:
    workspace, validation = _roots(tmp_path)
    target = _run(validation, "run-2026-08-12-10-00-10")
    original = cleanup._plain_tree
    monkeypatch.setattr(
        cleanup,
        "_plain_tree",
        lambda path: (False, str(path / "junction")) if path == target else original(path),
    )

    result, exit_code = _execute(
        _args("clear-runs", dry_run=True), workspace, validation
    )

    assert exit_code == EXIT_INVARIANT
    assert result["targets"][0]["reason"] == "refused_reparse_point"
    assert target.exists()


def test_clear_runs_actual_compacts_success_receipt_into_summary(tmp_path) -> None:
    workspace, validation = _roots(tmp_path)
    target = _run(validation, "run-2026-08-12-10-00-06")

    result, exit_code = _execute(
        _args("clear-runs", dry_run=False),
        workspace,
        validation,
        confirmation="CLEAR-RUNS",
    )

    assert exit_code == 0
    assert not target.exists()
    assert validation.exists()
    assert (validation / cleanup.VALIDATION_MARKER).exists()
    assert result["counts"]["deleted"] == 1
    assert result["confirmation_mode"] == "interactive_stdin"
    assert result["confirmation_value_recorded"] is False
    assert result["receipt_paths"] == []
    assert result["finalization"]["receipts_compacted"] == 1
    assert not list(validation.glob(f"{cleanup.RECEIPT_PREFIX}*.json"))
    assert Path(result["summary_tombstone_path"]).is_file()


def test_pending_receipt_failure_prevents_delete(tmp_path, monkeypatch) -> None:
    workspace, validation = _roots(tmp_path)
    target = _run(validation, "run-2026-08-12-10-00-11")
    original = cleanup._atomic_json

    def fail_receipt(path: Path, value) -> None:
        if path.name.startswith(cleanup.RECEIPT_PREFIX):
            raise OSError("injected receipt failure")
        original(path, value)

    monkeypatch.setattr(cleanup, "_atomic_json", fail_receipt)
    result, exit_code = _execute(
        _args("clear-runs", dry_run=False),
        workspace,
        validation,
        confirmation="CLEAR-RUNS",
    )

    assert exit_code == EXIT_INVARIANT
    assert target.exists()
    assert result["counts"]["failed"] == 1
    assert "pending_receipt_failed" in result["targets"][0]["reason"]


def test_clear_cache_refuses_open_template_but_ignores_open_working_copy(tmp_path) -> None:
    workspace, validation = _roots(tmp_path)
    _recipe, _store, hit = _cache(validation, tmp_path)
    template = Path(hit.entry["role_entries"]["source"]["template_path"])
    working = validation / "run-2026-08-12-10-00-07" / "notebooks" / "Working"

    opened_template = OpenNotebookPathSnapshot("complete", (("template-id", template),))
    result, exit_code = _execute(
        _args("clear-cache", dry_run=True),
        workspace,
        validation,
        snapshot=opened_template,
    )
    assert exit_code == EXIT_INVARIANT
    assert result["targets"][0]["reason"] == "refused_open"

    opened_working = OpenNotebookPathSnapshot("complete", (("working-id", working),))
    result, exit_code = _execute(
        _args("clear-cache", dry_run=True),
        workspace,
        validation,
        snapshot=opened_working,
    )
    assert exit_code == 0
    assert result["targets"][0]["decision"] == "would_delete"


def test_clear_cache_actual_deletes_entry_rebuilds_index_and_retains_marker(tmp_path) -> None:
    workspace, validation = _roots(tmp_path)
    recipe, store, hit = _cache(validation, tmp_path)

    result, exit_code = _execute(
        _args("clear-cache", dry_run=False),
        workspace,
        validation,
        confirmation="CLEAR-CACHE",
    )

    assert exit_code == 0, json.dumps(result, indent=2)
    assert not hit.entry_path.exists()
    assert store.marker_path.exists()
    index = json.loads((store.cache_root / "index.json").read_text(encoding="utf-8"))
    key = f"{recipe.cache_fingerprint}:{hit.template_instance_id}"
    assert key not in index["entries"]
    assert result["finalization"]["cache_index_entries_removed"] == 1
    assert result["finalization"]["empty_cache_directories_pruned"] == 2
    assert result["cache_marker_deleted"] is False


def test_clear_cache_integrates_historical_receipt_index_and_empty_scaffold_cleanup(
    tmp_path,
) -> None:
    workspace, validation = _roots(tmp_path)
    store = BundleCacheStore(validation / "fixture-cache")
    store.initialize()
    fingerprint = "c" * 64
    instance_id = "historical"
    instances = store.cache_root / fingerprint / "instances"
    instances.mkdir(parents=True)
    write_json(
        store.cache_root / "index.json",
        {
            "schema_version": 1,
            "entries": {
                f"{fingerprint}:{instance_id}": {
                    "fingerprint": fingerprint,
                    "template_instance_id": instance_id,
                    "state": "tombstone",
                }
            },
        },
    )
    target = validation / "run-2026-08-12-10-00-15"
    receipt = validation / f"{cleanup.RECEIPT_PREFIX}{'d' * 32}.json"
    write_json(
        receipt,
        {
            "schema_version": 1,
            "receipt_id": "d" * 32,
            "action": "clear-runs",
            "status": "deleted",
            "target": {"target": str(target.resolve())},
        },
    )
    historical_summary = validation / f"{cleanup.SUMMARY_PREFIX}{'e' * 32}.json"
    write_json(
        historical_summary,
        {
            "action": "clear-runs",
            "targets": [
                {"target": str(target.resolve()), "decision": "deleted"}
            ],
            "receipt_paths": [str(receipt.resolve())],
        },
    )

    dry_result, dry_code = _execute(
        _args("clear-cache", dry_run=True), workspace, validation
    )
    assert dry_code == 0
    assert dry_result["finalization_plan"] == {
        "successful_receipts_eligible": 1,
        "empty_cache_directories_eligible": 2,
        "cache_index_tombstones_eligible": 1,
        "assessment_failures": [],
    }
    assert receipt.exists()
    assert instances.exists()

    result, exit_code = _execute(
        _args("clear-cache", dry_run=False),
        workspace,
        validation,
        confirmation="CLEAR-CACHE",
    )

    assert exit_code == 0, json.dumps(result, indent=2)
    assert not receipt.exists()
    assert not instances.parent.exists()
    index = json.loads((store.cache_root / "index.json").read_text(encoding="utf-8"))
    assert index["entries"] == {}
    assert result["finalization"]["receipts_compacted"] == 1
    assert result["finalization"]["empty_cache_directories_pruned"] == 2
    assert result["finalization"]["cache_index_entries_removed"] == 1
    compacted_summary = json.loads(historical_summary.read_text(encoding="utf-8"))
    assert compacted_summary["receipt_paths"] == []
    assert compacted_summary["receipt_compaction"]["compacted_count"] == 1


def test_clear_cache_handles_legacy_working_leases_as_independent_metadata(tmp_path) -> None:
    workspace, validation = _roots(tmp_path)
    _recipe, store, _hit = _cache(validation, tmp_path)
    legacy = store.cache_root / "working-leases"
    legacy.mkdir()
    write_json(legacy / "old.json", {"state": "active"})

    result, exit_code = _execute(
        _args("clear-cache", dry_run=True), workspace, validation
    )

    assert exit_code == 0
    legacy_target = next(
        target for target in result["targets"] if target["kind"] == "legacy_working_leases"
    )
    assert legacy_target["decision"] == "would_delete"
    assert legacy_target["checks"]["template_not_open"] is True


def test_cache_index_path_escape_is_refused_without_scanning_outside_root(
    tmp_path, monkeypatch
) -> None:
    workspace, validation = _roots(tmp_path)
    store = BundleCacheStore(validation / "fixture-cache")
    store.initialize()
    outside = workspace / "outside"
    outside.mkdir()
    write_json(
        store.cache_root / "index.json",
        {
            "schema_version": 1,
            "entries": {
                "../../outside:x": {
                    "fingerprint": "../../outside",
                    "template_instance_id": "x",
                    "state": "ready",
                }
            },
        },
    )
    original = cleanup._plain_tree

    def bounded_plain_tree(path: Path):
        assert cleanup._inside(path, store.cache_root)
        return original(path)

    monkeypatch.setattr(cleanup, "_plain_tree", bounded_plain_tree)
    result, exit_code = _execute(
        _args("clear-cache", dry_run=True), workspace, validation
    )

    assert exit_code == EXIT_INVARIANT
    assert result["targets"][0]["reason"] == "refused_outside_exact_cache_entry"
    assert outside.exists()


def test_owned_staging_is_planned_as_one_exact_target(tmp_path) -> None:
    workspace, validation = _roots(tmp_path)
    store = BundleCacheStore(validation / "fixture-cache")
    store.initialize()
    staging = store.cache_root / f".staging-{'a' * 32}"
    template = staging / "notebooks" / "source" / "template-notebook"
    template.mkdir(parents=True)
    (template / "Open Notebook.onetoc2").write_bytes(b"catalog")
    write_json(
        staging / "bundle-entry.json",
        {
            "schema_version": 1,
            "fingerprint": "b" * 64,
            "template_instance_id": "x",
            "roles": ["source"],
        },
    )

    result, exit_code = _execute(
        _args("clear-cache", dry_run=True), workspace, validation
    )

    assert exit_code == 0
    assert result["targets"] == [
        {
            "kind": "cache_staging",
            "target": str(staging.resolve()),
            "identity": {"name": staging.name},
            "checks": result["targets"][0]["checks"],
            "decision": "would_delete",
            "reason": "all_special_cache_cleanup_checks_passed",
        }
    ]


def test_clear_all_reports_mixed_deleted_refused_and_failed_without_broad_delete(
    tmp_path,
) -> None:
    workspace, validation = _roots(tmp_path)
    deletable = _run(validation, "run-2026-08-12-10-00-08")
    opened = _run(validation, "run-2026-08-12-10-00-09")
    _recipe, _store, hit = _cache(validation, tmp_path)
    snapshot = OpenNotebookPathSnapshot(
        "complete", (("opened-id", opened / "notebooks" / "Notebook-0"),)
    )

    def fail_cache(path: Path) -> None:
        if path == hit.entry_path:
            raise OSError("injected delete failure")
        cleanup.shutil.rmtree(path)

    result, exit_code = _execute(
        _args("clear-all", dry_run=False),
        workspace,
        validation,
        snapshot=snapshot,
        delete_tree=fail_cache,
        confirmation="CLEAR-ALL",
    )

    assert exit_code == EXIT_INVARIANT
    assert not deletable.exists()
    assert opened.exists()
    assert hit.entry_path.exists()
    assert result["counts"]["deleted"] == 1
    assert result["counts"]["refused"] == 1
    assert result["counts"]["failed"] == 1
    assert validation.exists()
    assert (_store.cache_root / cleanup.MANAGED_MARKER).exists()


def test_fixed_root_rejects_workspace_or_arbitrary_path(tmp_path) -> None:
    workspace, validation = _roots(tmp_path)
    with pytest.raises(RunnerFailure, match="fixed-root"):
        cleanup.run_maintenance(
            _args("clear-runs", dry_run=True),
            workspace_root=workspace,
            validation_root=tmp_path / "arbitrary",
            snapshot=OpenNotebookPathSnapshot("complete"),
        )
    with pytest.raises(RunnerFailure, match="fixed-root"):
        cleanup.run_maintenance(
            _args("clear-runs", dry_run=True),
            workspace_root=workspace,
            validation_root=workspace,
            snapshot=OpenNotebookPathSnapshot("complete"),
        )


def test_main_refuses_noninteractive_confirmation_before_any_delete(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        OpenNotebookPathSnapshot,
        "capture",
        classmethod(lambda cls, **_kwargs: OpenNotebookPathSnapshot("complete")),
    )
    monkeypatch.setattr(cleanup, "_discover", lambda *_args, **_kwargs: ([], None, None))
    assert main(["clear", "runs", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "interactive terminal" in payload["error"]


def test_open_snapshot_normalizes_file_uri_and_onetoc_path(monkeypatch, tmp_path) -> None:
    notebook_dir = tmp_path / "Notebook Space"
    xml = (
        '<one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">'
        f'<one:Notebook ID="nb" path="{(notebook_dir / "Open Notebook.onetoc2").as_uri()}" />'
        "</one:Notebooks>"
    )
    calls = []

    class FakeBridge:
        def __init__(self, *, timeout_seconds):
            assert timeout_seconds == 30

        def call(self, operation, **params):
            calls.append((operation, params))
            return {"xml": xml}

    monkeypatch.setattr(cleanup, "OneNoteBridge", FakeBridge)
    snapshot = OpenNotebookPathSnapshot.capture()

    assert snapshot.status == "complete"
    assert snapshot.notebooks == (("nb", notebook_dir.resolve()),)
    assert calls == [
        (
            "get_hierarchy",
            {"start_id": "", "scope": 2, "schema": 2},
        )
    ]


def test_clear_all_captures_one_snapshot(monkeypatch, tmp_path) -> None:
    workspace, validation = _roots(tmp_path)
    _run(validation, "run-2026-08-12-10-00-12")
    calls = 0

    def capture(cls, **_kwargs):
        nonlocal calls
        calls += 1
        return OpenNotebookPathSnapshot("complete")

    monkeypatch.setattr(OpenNotebookPathSnapshot, "capture", classmethod(capture))
    result, exit_code = cleanup.run_maintenance(
        _args("clear-all", dry_run=True),
        workspace_root=workspace,
        validation_root=validation,
    )

    assert exit_code == 0
    assert result["counts"]["planned"] == 1
    assert calls == 1
