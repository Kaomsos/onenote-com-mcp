"""Pure contracts for the narrow source-Notebook lifecycle wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.manual_validation.lifecycle import NotebookLifecycleWrapper
from tests.manual_validation.runtime import EXIT_MCP, RestoreFailure, RunnerFailure
from tests.manual_validation.test_utils import read_json, write_json


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.hierarchy = None
        self.opened_paths: dict[Path, str] = {}

    def call(self, name: str, **kwargs):
        self.calls.append((name, kwargs))
        if name == "open_hierarchy":
            path = Path(kwargs["path"])
            path.mkdir(parents=True, exist_ok=True)
            self.opened_paths[path.resolve()] = "notebook-id"
            self.reported_path = str(path.resolve())
            self.hierarchy.closed = False
            return {"object_id": "notebook-id"}
        if name == "close_notebook":
            self.hierarchy.closed = True
            return {"ok": True}
        if name == "sync_hierarchy":
            return {"ok": True}
        if name == "get_hierarchy":
            return {
                "xml": (
                    '<one:Notebook xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
                    f'ID="notebook-id" path="{self.reported_path}" />'
                )
            }
        raise AssertionError(name)


class BatchFakeBridge(FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.batch_paths_existed: list[bool] = []

    def call(self, name: str, **kwargs):
        if name != "open_hierarchy_batch":
            return super().call(name, **kwargs)
        self.calls.append((name, kwargs))
        requests = list(kwargs["requests"])
        working = Path(self.reported_path)
        self.batch_paths_existed = [
            (working / request["key"]).exists() for request in requests
        ]
        claimed = working / "Group" / "B.one"
        if claimed.exists():
            claimed.unlink()
        object_ids = {
            "Group": "group-id",
            "Group/A.one": "section-a-id",
            "Group/B.one": "section-b-id",
            "Root.one": "root-section-id",
        }
        return {
            "items": [
                {
                    "key": request["key"],
                    "ok": True,
                    "object_id": object_ids[request["key"]],
                    "relative_to_id": (
                        "group-id" if request["parent_key"] == "Group" else "notebook-id"
                    ),
                    "error": None,
                }
                for request in requests
            ],
            "xml": (
                '<one:Notebook xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
                'ID="notebook-id" name="__ISOLATED__">'
                '<one:SectionGroup ID="group-id" name="Group">'
                '<one:Section ID="section-a-id" name="A" />'
                '<one:Section ID="section-b-id" name="B" />'
                '</one:SectionGroup>'
                '<one:Section ID="root-section-id" name="Root" />'
                '</one:Notebook>'
            ),
        }


class LaggingBatchFakeBridge(BatchFakeBridge):
    def call(self, name: str, **kwargs):
        result = super().call(name, **kwargs)
        if name == "open_hierarchy_batch":
            result["xml"] = (
                '<one:Notebook xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
                'ID="notebook-id" name="__ISOLATED__" />'
            )
        return result


class ReadFailingBatchFakeBridge(BatchFakeBridge):
    def call(self, name: str, **kwargs):
        result = super().call(name, **kwargs)
        if name == "open_hierarchy_batch":
            result["xml"] = None
            result["hierarchy_error"] = {
                "hresult": -2147023174,
                "leaf_exception_type": "System.Runtime.InteropServices.COMException",
            }
        return result


class RetryingItemBatchFakeBridge(BatchFakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.batch_call_count = 0

    def call(self, name: str, **kwargs):
        result = super().call(name, **kwargs)
        if name != "open_hierarchy_batch":
            return result
        self.batch_call_count += 1
        if self.batch_call_count == 1:
            root = next(item for item in result["items"] if item["key"] == "Root.one")
            root.update(
                ok=False,
                object_id=None,
                error={
                    "hresult": -2147023174,
                    "leaf_exception_type": "System.Runtime.InteropServices.COMException",
                },
            )
            result["xml"] = result["xml"].replace(
                '<one:Section ID="root-section-id" name="Root" />', ""
            )
        return result


class FakeHierarchy:
    def __init__(self, name: str = "__ISOLATED__") -> None:
        self.name = name
        self.closed = False
        self.created = False

    def list_notebooks(self):
        return {"items": [] if not self.created else [self._item()], "count": int(self.created)}

    def wait_for_created(
        self,
        _path: str,
        _type: str,
        _fallback_id: str,
        **_kwargs,
    ):
        self.created = True
        if _type != "notebook":
            return {
                "id": _fallback_id,
                "name": Path(_path).name,
                "resource_type": _type,
                "path": _path,
                "parent_id": _fallback_id.rsplit("::", 1)[0],
                "is_open": True,
            }
        return self._item()

    def resource(self, object_id: str, resource_type: str):
        assert resource_type == "notebook"
        if self.closed or object_id != "notebook-id":
            raise ValueError(object_id)
        return self._item()

    def _item(self):
        return {
            "id": "notebook-id",
            "name": self.name,
            "resource_type": "notebook",
            "path": self.name,
            "is_open": True,
        }


def _wrapper(tmp_path: Path):
    bridge = FakeBridge()
    hierarchy = FakeHierarchy()
    bridge.hierarchy = hierarchy
    bridge.reported_path = ""
    wrapper = NotebookLifecycleWrapper(tmp_path / "run", timeout_seconds=10, bridge=bridge)
    wrapper._hierarchy = hierarchy
    return wrapper, bridge, hierarchy


def test_open_working_copy_proves_actual_path_is_not_template(tmp_path) -> None:
    wrapper, bridge, _hierarchy = _wrapper(tmp_path)
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    working.mkdir(parents=True)
    template.mkdir(parents=True)
    bridge.reported_path = str(working.resolve())

    notebook, lease = wrapper.open_working_notebook(
        "__ISOLATED__",
        working,
        template_paths=(template,),
    )

    assert notebook["id"] == "notebook-id"
    assert lease["actual_local_path"] == str(working.resolve())
    assert lease["opened_template"] is False
    assert lease["template_paths"] == [str(template.resolve())]


def test_index_checkpoint_reopen_uses_its_own_closed_lease_archive(tmp_path) -> None:
    wrapper, bridge, _hierarchy = _wrapper(tmp_path)
    working = wrapper.notebook_root / "fresh-search"
    working.mkdir(parents=True)
    bridge.reported_path = str(working.resolve())
    write_json(
        wrapper.lease_path,
        {
            "schema_version": 1,
            "state": "closed",
            "notebook_id": "old-notebook-id",
            "expected_local_path": str(working.resolve()),
        },
    )

    wrapper.open_working_notebook(
        "__ISOLATED__",
        working,
        template_paths=(),
        lease_archive_kind="index-checkpoint",
    )

    assert (wrapper.run_dir / "lifecycle-index-checkpoint-lease.json").is_file()
    assert not (wrapper.run_dir / "lifecycle-cold-build-lease.json").exists()


def test_materialized_batch_freezes_paths_before_one_parent_first_com_session(
    tmp_path,
) -> None:
    bridge = BatchFakeBridge()
    hierarchy = FakeHierarchy()
    bridge.hierarchy = hierarchy
    bridge.reported_path = ""
    wrapper = NotebookLifecycleWrapper(
        tmp_path / "run", timeout_seconds=10, bridge=bridge
    )
    wrapper._hierarchy = hierarchy
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    (working / "Group").mkdir(parents=True)
    (working / "OneNote_RecycleBin").mkdir(parents=True)
    template.mkdir(parents=True)
    (working / "Group" / "A.one").write_bytes(b"a")
    (working / "Group" / "B.one").write_bytes(b"b")
    (working / "OneNote_RecycleBin" / "OneNote_DeletedPages.one").write_bytes(
        b"deleted"
    )
    (working / "Root.one").write_bytes(b"root")
    bridge.reported_path = str(working.resolve())

    _notebook, lease = wrapper.open_working_notebook(
        "__ISOLATED__",
        working,
        template_paths=(template,),
    )

    batch_calls = [kwargs for name, kwargs in bridge.calls if name == "open_hierarchy_batch"]
    assert len(batch_calls) == 1
    assert bridge.batch_paths_existed == [True, True, True, True]
    assert [request["key"] for request in batch_calls[0]["requests"]] == [
        "Group",
        "Group/A.one",
        "Group/B.one",
        "Root.one",
    ]
    assert batch_calls[0]["requests"][1]["parent_key"] == "Group"
    assert batch_calls[0]["requests"][1]["path"] == "A.one"
    assert batch_calls[0]["requests"][1]["relative_to_id"] == ""
    assert batch_calls[0]["requests"][0]["relative_to_id"] == ""
    assert Path(batch_calls[0]["requests"][0]["path"]).is_absolute()
    assert [item["relative_path"] for item in lease["opened_hierarchy"]] == [
        "Group",
        "Group/A.one",
        "Group/B.one",
        "Root.one",
    ]
    evidence = read_json(wrapper.materialized_evidence_path)
    assert evidence["schema_version"] == 2
    assert evidence["batch_session_count"] == 1
    assert evidence["ignored_system_paths"] == [
        {
            "relative_path": "OneNote_RecycleBin",
            "reason": "onenote_recycle_bin_not_activation_target",
        }
    ]
    assert all(attempt["activated"] is True for attempt in evidence["attempts"])


@pytest.mark.parametrize(
    "bridge_type,xml_available",
    ((LaggingBatchFakeBridge, True), (ReadFailingBatchFakeBridge, False)),
)
def test_materialized_batch_defers_snapshot_lag_to_fixture_convergence(
    tmp_path, bridge_type, xml_available
) -> None:
    bridge = bridge_type()
    hierarchy = FakeHierarchy()
    bridge.hierarchy = hierarchy
    bridge.reported_path = ""
    wrapper = NotebookLifecycleWrapper(
        tmp_path / "run", timeout_seconds=10, bridge=bridge
    )
    wrapper._hierarchy = hierarchy
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    (working / "Group").mkdir(parents=True)
    template.mkdir(parents=True)
    (working / "Group" / "A.one").write_bytes(b"a")
    (working / "Group" / "B.one").write_bytes(b"b")
    (working / "Root.one").write_bytes(b"root")
    bridge.reported_path = str(working.resolve())

    _notebook, lease = wrapper.open_working_notebook(
        "__ISOLATED__", working, template_paths=(template,)
    )

    assert len([call for call in bridge.calls if call[0] == "open_hierarchy_batch"]) == 1
    assert len(lease["opened_hierarchy"]) == 4
    assert all(item["snapshot_visible"] is False for item in lease["opened_hierarchy"])
    evidence = read_json(wrapper.materialized_evidence_path)
    assert evidence["status"] == "passed"
    assert evidence["batch_session_count"] == 1
    assert evidence["batch_observations"][0]["hierarchy_xml_available"] is xml_available
    assert all(
        attempt["activation_proof"]
        == "open_hierarchy_returned_id_pending_fixture_convergence"
        for attempt in evidence["attempts"]
    )


def test_materialized_batch_retries_only_the_item_that_failed_to_open(tmp_path) -> None:
    bridge = RetryingItemBatchFakeBridge()
    hierarchy = FakeHierarchy()
    bridge.hierarchy = hierarchy
    bridge.reported_path = ""
    wrapper = NotebookLifecycleWrapper(
        tmp_path / "run", timeout_seconds=10, bridge=bridge
    )
    wrapper._hierarchy = hierarchy
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    (working / "Group").mkdir(parents=True)
    template.mkdir(parents=True)
    (working / "Group" / "A.one").write_bytes(b"a")
    (working / "Group" / "B.one").write_bytes(b"b")
    (working / "Root.one").write_bytes(b"root")
    bridge.reported_path = str(working.resolve())

    wrapper.open_working_notebook(
        "__ISOLATED__", working, template_paths=(template,)
    )

    batch_calls = [kwargs for name, kwargs in bridge.calls if name == "open_hierarchy_batch"]
    assert len(batch_calls) == 2
    assert [request["key"] for request in batch_calls[1]["requests"]] == ["Root.one"]
    evidence = read_json(wrapper.materialized_evidence_path)
    assert evidence["status"] == "passed"
    assert evidence["batch_session_count"] == 2
    root_attempts = [
        attempt for attempt in evidence["attempts"] if attempt["relative_path"] == "Root.one"
    ]
    assert [attempt["activated"] for attempt in root_attempts] == [False, True]


def test_materialized_schema_two_lease_closes_by_exact_id_and_path(tmp_path) -> None:
    wrapper, bridge, _hierarchy = _wrapper(tmp_path)
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    working.mkdir(parents=True)
    template.mkdir(parents=True)
    bridge.reported_path = str(working.resolve())
    wrapper.open_working_notebook(
        "__ISOLATED__",
        working,
        template_paths=(template,),
    )

    result = wrapper.close_exact_notebook()

    assert result["closed"] is True
    assert result["convergence"]["stable_observations"] == 2
    assert read_json(wrapper.lease_path)["state"] == "closed"


def test_run_local_active_lease_rejects_same_live_notebook_id(tmp_path) -> None:
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    old_run = tmp_path / "old-run"
    old_run.mkdir()
    old_path = old_run / "notebooks" / "working"
    write_json(
        old_run / "lifecycle-lease.json",
        {
            "state": "active",
            "notebook_id": "notebook-id",
            "actual_local_path": str(old_path),
        },
    )
    hierarchy.created = True
    bridge.reported_path = str(old_path.resolve())

    with pytest.raises(RunnerFailure, match="run_id=old-run"):
        wrapper.assert_no_active_working_conflict(
            notebook_ids={"source": "notebook-id"},
            working_paths={"source": tmp_path / "run" / "notebooks" / "new"},
            open_notebooks={"notebook-id": old_path.resolve()},
        )


def test_reused_notebook_id_at_a_new_path_does_not_revive_stale_lease(tmp_path) -> None:
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    old_run = tmp_path / "old-run"
    old_run.mkdir()
    write_json(
        old_run / "lifecycle-lease.json",
        {
            "state": "active",
            "notebook_id": "notebook-id",
            "actual_local_path": str(old_run / "notebooks" / "old-working"),
        },
    )
    new_path = tmp_path / "run" / "notebooks" / "new-working"
    bridge.reported_path = str(new_path.resolve())
    hierarchy.created = True

    wrapper.assert_no_active_working_conflict(
        notebook_ids={"source": "notebook-id"},
        working_paths={"source": new_path},
        open_notebooks={"notebook-id": new_path.resolve()},
    )


def test_closed_run_local_notebook_does_not_block_reused_id(tmp_path) -> None:
    wrapper, _bridge, _hierarchy = _wrapper(tmp_path)
    old_run = tmp_path / "old-run"
    old_run.mkdir()
    write_json(
        old_run / "lifecycle-lease.json",
        {
            "state": "active",
            "notebook_id": "notebook-id",
            "actual_local_path": str(old_run / "notebooks" / "working"),
        },
    )

    wrapper.assert_no_active_working_conflict(
        notebook_ids={"source": "notebook-id"},
        working_paths={"source": tmp_path / "run" / "notebooks" / "new"},
        open_notebooks={},
    )


def test_active_run_local_lease_without_exact_path_fails_closed(tmp_path) -> None:
    wrapper, _bridge, _hierarchy = _wrapper(tmp_path)
    old_run = tmp_path / "old-run"
    old_run.mkdir()
    write_json(
        old_run / "lifecycle-lease.json",
        {"state": "active", "notebook_id": "notebook-id"},
    )

    with pytest.raises(RunnerFailure, match="missing its exact working path"):
        wrapper.assert_no_active_working_conflict(
            notebook_ids={"source": "new-id"},
            working_paths={"source": tmp_path / "run" / "notebooks" / "new"},
            open_notebooks={},
        )


def test_many_historical_active_leases_use_only_the_supplied_snapshot(tmp_path) -> None:
    wrapper, bridge, _hierarchy = _wrapper(tmp_path)
    for index in range(100):
        old_run = tmp_path / f"old-run-{index:03d}"
        old_run.mkdir()
        write_json(
            old_run / "lifecycle-lease.json",
            {
                "state": "active",
                "notebook_id": f"old-id-{index}",
                "actual_local_path": str(old_run / "notebooks" / "working"),
            },
        )
    calls_before = list(bridge.calls)

    wrapper.assert_no_active_working_conflict(
        notebook_ids={"source": "new-id"},
        working_paths={"source": tmp_path / "run" / "notebooks" / "new"},
        open_notebooks={},
    )

    assert bridge.calls == calls_before


def test_open_notebook_snapshot_reads_each_open_path_once(tmp_path, monkeypatch) -> None:
    wrapper, _bridge, hierarchy = _wrapper(tmp_path)
    hierarchy.created = True
    reported: list[str] = []
    expected = tmp_path / "working"

    def report(notebook_id: str) -> Path:
        reported.append(notebook_id)
        return expected.resolve()

    monkeypatch.setattr(wrapper, "_reported_notebook_directory", report)

    assert wrapper.snapshot_open_notebooks() == {"notebook-id": expected.resolve()}
    assert reported == ["notebook-id"]


def test_open_notebook_snapshot_rejects_two_ids_for_one_path(
    tmp_path,
    monkeypatch,
) -> None:
    wrapper, _bridge, hierarchy = _wrapper(tmp_path)
    hierarchy.created = True
    hierarchy.list_notebooks = lambda: {
        "items": [
            {"id": "notebook-one", "is_open": True},
            {"id": "notebook-two", "is_open": True},
        ],
        "count": 2,
    }
    expected = (tmp_path / "working").resolve()
    monkeypatch.setattr(
        wrapper,
        "_reported_notebook_directory",
        lambda _notebook_id: expected,
    )

    with pytest.raises(RunnerFailure, match="snapshot") as exc_info:
        wrapper.snapshot_open_notebooks()

    assert exc_info.value.exit_code == EXIT_MCP


def test_open_notebook_snapshot_normalizes_bridge_failure(tmp_path, monkeypatch) -> None:
    wrapper, _bridge, _hierarchy = _wrapper(tmp_path)
    monkeypatch.setattr(
        wrapper._hierarchy,
        "list_notebooks",
        lambda **_kwargs: (_ for _ in ()).throw(
            RestoreFailure("injected hierarchy failure")
        ),
    )

    with pytest.raises(RunnerFailure, match="snapshot") as exc_info:
        wrapper.snapshot_open_notebooks()

    assert exc_info.value.exit_code == EXIT_MCP


def test_working_open_lock_releases_after_scope(tmp_path) -> None:
    wrapper, _bridge, _hierarchy = _wrapper(tmp_path)
    lock_path = tmp_path / "working-notebook-open.lock"

    with wrapper.working_notebook_open_lock():
        assert lock_path.exists()

    with wrapper.working_notebook_open_lock(timeout_seconds=0):
        assert lock_path.exists()


def test_cache_template_open_probe_matches_actual_template_path(tmp_path) -> None:
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    template = tmp_path / "cache" / "template-notebook"
    template.mkdir(parents=True)
    bridge.reported_path = str(template.resolve())
    hierarchy.created = True
    entry = {"role_entries": {"source": {"template_path": str(template)}}}

    assert wrapper.any_cache_template_open(entry) is True


def test_cache_template_open_probe_ignores_independent_working_path(tmp_path) -> None:
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    template = tmp_path / "cache" / "template-notebook"
    working = tmp_path / "run" / "notebooks" / "working"
    template.mkdir(parents=True)
    working.mkdir(parents=True)
    bridge.reported_path = str(working.resolve())
    hierarchy.created = True
    entry = {"role_entries": {"source": {"template_path": str(template)}}}

    assert wrapper.any_cache_template_open(entry) is False


def test_cache_template_open_probe_fails_closed_when_actual_path_is_unreadable(
    tmp_path,
    monkeypatch,
) -> None:
    wrapper, _bridge, hierarchy = _wrapper(tmp_path)
    template = tmp_path / "cache" / "template-notebook"
    template.mkdir(parents=True)
    hierarchy.created = True
    entry = {"role_entries": {"source": {"template_path": str(template)}}}
    monkeypatch.setattr(
        wrapper,
        "_reported_notebook_directory",
        lambda _notebook_id: (_ for _ in ()).throw(RestoreFailure("unreadable")),
    )

    assert wrapper.any_cache_template_open(entry) is True


def test_create_writes_exact_id_name_path_lease(tmp_path) -> None:
    wrapper, bridge, _hierarchy = _wrapper(tmp_path)
    notebook, lease = wrapper.create_fresh_notebook("__ISOLATED__")

    expected_path = (tmp_path / "run" / "notebooks" / "__ISOLATED__").resolve()
    assert notebook["id"] == "notebook-id"
    assert lease["notebook_id"] == "notebook-id"
    assert lease["expected_name"] == "__ISOLATED__"
    assert lease["expected_local_path"] == str(expected_path)
    assert lease["state"] == "active"
    assert bridge.calls[0][0] == "open_hierarchy"
    assert read_json(wrapper.lease_path) == lease


def test_close_is_bound_to_exact_lease_and_preserves_files(tmp_path) -> None:
    wrapper, bridge, _hierarchy = _wrapper(tmp_path)
    wrapper.create_fresh_notebook("__ISOLATED__")
    source = wrapper.notebook_root / "__ISOLATED__"
    (source / "Source.one").write_bytes(b"evidence")

    result = wrapper.close_exact_notebook()

    assert result["closed"] is True
    assert result["convergence"]["stable_observations"] == 2
    assert result["source_notebook_id"] == "notebook-id"
    assert result["persistence_sync"] == {
        "requested": False,
        "accepted": False,
        "completion_proof": None,
    }
    assert bridge.calls[-1] == (
        "close_notebook",
        {"notebook_id": "notebook-id", "force": False},
    )
    assert source.exists()
    assert (source / "Source.one").exists()
    assert read_json(wrapper.lease_path)["state"] == "closed"


def _production_close_result(**overrides):
    result = {
        "ok": True,
        "complete": True,
        "closed": True,
        "item": {
            "id": "notebook-id",
            "name": "__ISOLATED__",
            "resource_type": "notebook",
        },
        "final_state": None,
        "convergence": {
            "converged": True,
            "attempts": 2,
            "elapsed_seconds": 0.01,
            "stable_observations": 2,
            "identity_remap": {},
            "transient_errors": [],
        },
        "reconciliation": {
            "state": "applied",
            "mutation_stage": "postcondition",
            "mutation_attempted": True,
            "mutation_attempts": 1,
            "mutation_replayed": False,
            "observed_outcome": "applied",
            "retry_safety": "not_needed",
            "recommended_action": "none",
        },
    }
    result.update(overrides)
    return result


def test_production_close_handoff_seals_exact_active_lease_without_second_close(
    tmp_path,
) -> None:
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    wrapper.create_fresh_notebook("__ISOLATED__")
    calls_before = list(bridge.calls)
    hierarchy.closed = True

    result = wrapper.adopt_production_close(_production_close_result())

    assert result["closed"] is True
    assert result["source_notebook_id"] == "notebook-id"
    assert result["close_origin"] == "production_close_notebook"
    assert bridge.calls == calls_before
    lease = read_json(wrapper.lease_path)
    assert lease["state"] == "closed"
    assert lease["close_result"] == result


def test_production_close_handoff_rejects_unbound_or_replayed_evidence(tmp_path) -> None:
    wrapper, _bridge, _hierarchy = _wrapper(tmp_path)
    wrapper.create_fresh_notebook("__ISOLATED__")

    wrong_item = _production_close_result(
        item={"id": "other-id", "name": "__ISOLATED__"}
    )
    with pytest.raises(RestoreFailure, match="exact leased Notebook"):
        wrapper.adopt_production_close(wrong_item)

    replayed = _production_close_result()
    replayed["reconciliation"]["mutation_attempts"] = 2
    replayed["reconciliation"]["mutation_replayed"] = True
    with pytest.raises(RestoreFailure, match="single-attempt"):
        wrapper.adopt_production_close(replayed)

    assert read_json(wrapper.lease_path)["state"] == "active"


def test_cache_publish_close_syncs_exact_notebook_to_disk_first(tmp_path) -> None:
    wrapper, bridge, _hierarchy = _wrapper(tmp_path)
    wrapper.create_fresh_notebook("__ISOLATED__")

    result = wrapper.close_exact_notebook(sync_to_disk=True)

    assert result["closed"] is True
    assert result["persistence_sync"] == {
        "requested": True,
        "accepted": True,
        "completion_proof": "CloseNotebook(force=false)",
    }
    assert bridge.calls[-2:] == [
        ("sync_hierarchy", {"object_id": "notebook-id"}),
        (
            "close_notebook",
            {"notebook_id": "notebook-id", "force": False},
        ),
    ]


def test_cache_publish_sync_failure_preserves_active_lease_for_finalization(
    tmp_path,
) -> None:
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    wrapper.create_fresh_notebook("__ISOLATED__")
    original_call = bridge.call

    def fail_sync(name: str, **kwargs):
        if name == "sync_hierarchy":
            raise RuntimeError("injected sync failure")
        return original_call(name, **kwargs)

    bridge.call = fail_sync

    with pytest.raises(RestoreFailure, match="persistence sync failed"):
        wrapper.close_exact_notebook(sync_to_disk=True)

    lease = read_json(wrapper.lease_path)
    assert lease["state"] == "active"
    assert "injected sync failure" in lease["persistence_sync_error"]
    assert hierarchy.closed is False


def test_binding_mismatch_refuses_close_and_keeps_notebook_open(tmp_path) -> None:
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    wrapper.create_fresh_notebook("__ISOLATED__")
    lease = read_json(wrapper.lease_path)
    lease["expected_name"] = "WRONG"
    write_json(wrapper.lease_path, lease)

    with pytest.raises(RestoreFailure, match="path/name"):
        wrapper.close_exact_notebook()

    assert hierarchy.closed is False
    assert [name for name, _kwargs in bridge.calls] == ["open_hierarchy"]


def test_get_exact_notebook_validates_open_binding_without_mutation(tmp_path) -> None:
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    wrapper.create_fresh_notebook("__ISOLATED__")
    result = wrapper.get_exact_notebook()

    assert result["id"] == "notebook-id"
    assert hierarchy.closed is False
    assert [name for name, _kwargs in bridge.calls] == [
        "open_hierarchy",
        "get_hierarchy",
    ]


def test_wrapper_exposes_only_bounded_lifecycle_operations() -> None:
    operations = {
        name
        for name, value in vars(NotebookLifecycleWrapper).items()
        if callable(value) and not name.startswith("_")
    }
    assert operations == {
        "assert_no_active_working_conflict",
        "adopt_production_close",
        "create_fresh_notebook",
        "get_exact_notebook",
        "close_exact_notebook",
        "open_working_notebook",
        "any_cache_template_open",
        "snapshot_open_notebooks",
        "working_notebook_open_lock",
    }


def test_role_wrappers_use_independent_leases_and_materialization_evidence(tmp_path) -> None:
    source = NotebookLifecycleWrapper(tmp_path / "run", timeout_seconds=10, bridge=FakeBridge())
    destination = NotebookLifecycleWrapper(
        tmp_path / "run",
        timeout_seconds=10,
        bridge=FakeBridge(),
        role="destination",
    )

    assert source.lease_path.name == "lifecycle-lease.json"
    assert destination.lease_path.name == "lifecycle-lease-destination.json"
    assert source.materialized_evidence_path.name == "materialized-hierarchy-open.json"
    assert (
        destination.materialized_evidence_path.name
        == "materialized-hierarchy-open-destination.json"
    )
    assert source.lease_path != destination.lease_path
