"""Pure contracts for the narrow source-Notebook lifecycle wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_onenote_mcp.bridge import OneNoteBridgeError
from tests.manual_validation.lifecycle import NotebookLifecycleWrapper
from tests.manual_validation.runtime import EXIT_MCP, RestoreFailure, RunnerFailure
from tests.manual_validation.test_utils import read_json, write_json


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.hierarchy = None
        self.opened_paths: dict[Path, str] = {}
        self.fail_absolute_children = False
        self.fail_all_children = False
        self.exact_child_hierarchy = False
        self.exact_child_tag = "Section"
        self.exact_child_failures = 0
        self.exact_child_failure_hresult = 0x80131501
        self.exact_child_failures_after_reopen: int | None = None

    def call(self, name: str, **kwargs):
        self.calls.append((name, kwargs))
        if name == "open_hierarchy":
            path = Path(kwargs["path"])
            opening_notebook = path.is_absolute() and (
                not self.opened_paths
                or (
                    self.reported_path
                    and path.resolve() == Path(self.reported_path).resolve()
                )
            )
            if opening_notebook:
                path.mkdir(parents=True, exist_ok=True)
                self.opened_paths[path.resolve()] = "notebook-id"
                self.reported_path = str(path.resolve())
                self.hierarchy.closed = False
                return {"object_id": "notebook-id"}
            if self.fail_all_children or (
                path.is_absolute() and self.fail_absolute_children
            ):
                raise RuntimeError("injected child open failure")
            if path.is_absolute():
                parent_id = self.opened_paths[path.resolve().parent]
                object_id = f"{parent_id}::{path.name}"
                self.opened_paths[path.resolve()] = object_id
                return {"object_id": object_id}
            return {
                "object_id": f"{kwargs['relative_to_id']}::{path.name}"
            }
        if name == "get_hierarchy_parent":
            object_id = str(kwargs["object_id"])
            return {"parent_id": object_id.rsplit("::", 1)[0]}
        if name == "close_notebook":
            self.hierarchy.closed = True
            if self.exact_child_failures_after_reopen is not None:
                self.exact_child_failures = self.exact_child_failures_after_reopen
            return {"ok": True}
        if name == "get_hierarchy":
            start_id = str(kwargs.get("start_id", ""))
            if start_id != "notebook-id" and self.exact_child_failures:
                self.exact_child_failures -= 1
                raise OneNoteBridgeError(
                    "injected exact child readiness failure",
                    operation="get_hierarchy",
                    hresult=self.exact_child_failure_hresult,
                )
            if self.exact_child_hierarchy and start_id != "notebook-id":
                filename = start_id.rsplit("::", 1)[-1]
                child_name = Path(filename).stem
                return {
                    "xml": (
                        '<one:{tag} xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
                        'ID="{object_id}" name="{child_name}" />'
                    ).format(
                        tag=self.exact_child_tag,
                        object_id=start_id,
                        child_name=child_name,
                    )
                }
            return {
                "xml": (
                    '<one:Notebook xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
                    f'ID="notebook-id" path="{self.reported_path}" />'
                )
            }
        raise AssertionError(name)


class BatchFakeBridge(FakeBridge):
    supports_hierarchy_batch = True

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
        self.hide_children_from_global = False
        self.hidden_child_observations = 0

    def list_notebooks(self, include_recycle_bin: bool = False):
        return {"notebooks": [] if not self.created else [self._item()]}

    def wait_for_created(
        self,
        _path: str,
        _type: str,
        _fallback_id: str,
        **_kwargs,
    ):
        self.created = True
        if _type != "notebook":
            if self.hidden_child_observations:
                self.hidden_child_observations -= 1
                return None
            if self.hide_children_from_global:
                return None
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


def test_open_working_copy_explicitly_opens_bounded_sections_and_groups(tmp_path) -> None:
    wrapper, bridge, _hierarchy = _wrapper(tmp_path)
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    (working / "Group").mkdir(parents=True)
    template.mkdir(parents=True)
    (working / "Root.one").write_bytes(b"root")
    (working / "Group" / "Child.one").write_bytes(b"child")
    (working / "Open Notebook.onetoc2").write_bytes(b"catalog")
    bridge.reported_path = str(working.resolve())

    _notebook, lease = wrapper.open_working_notebook(
        "__ISOLATED__",
        working,
        template_paths=(template,),
    )

    opened = lease["opened_hierarchy"]
    assert [(item["relative_path"], item["resource_type"]) for item in opened] == [
        ("Group", "section_group"),
        ("Group/Child.one", "section"),
        ("Root.one", "section"),
    ]
    child_calls = [call for call in bridge.calls if call[0] == "open_hierarchy"][1:]
    assert [
        (Path(call[1]["path"]).name, bool(call[1]["relative_to_id"]))
        for call in child_calls
    ] == [
        ("Group", False),
        ("Child.one", True),
        ("Root.one", False),
    ]
    open_evidence = read_json(wrapper.run_dir / "materialized-hierarchy-open.json")
    assert open_evidence["status"] == "passed"
    assert open_evidence["content_saved"] is False
    assert all(item["activated"] is True for item in open_evidence["attempts"])


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
    template.mkdir(parents=True)
    (working / "Group" / "A.one").write_bytes(b"a")
    (working / "Group" / "B.one").write_bytes(b"b")
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


def test_nested_working_copy_child_never_uses_parentless_absolute_open(tmp_path) -> None:
    wrapper, bridge, _hierarchy = _wrapper(tmp_path)
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    (working / "Group").mkdir(parents=True)
    template.mkdir(parents=True)
    (working / "Group" / "Child.one").write_bytes(b"child")
    bridge.reported_path = str(working.resolve())

    wrapper.open_working_notebook(
        "__ISOLATED__",
        working,
        template_paths=(template,),
    )

    child_attempt = next(
        attempt
        for attempt in read_json(wrapper.materialized_evidence_path)["attempts"]
        if attempt["relative_path"] == "Group/Child.one"
    )
    assert child_attempt["path_mode"] == "parent_relative"
    assert child_attempt["relative_to_id"] == "notebook-id::Group"
    assert child_attempt["open_path"] == "Child.one"


def test_open_working_copy_falls_back_to_parent_relative_child_path(tmp_path) -> None:
    wrapper, bridge, _hierarchy = _wrapper(tmp_path)
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    working.mkdir(parents=True)
    template.mkdir(parents=True)
    (working / "Root.one").write_bytes(b"root")
    bridge.reported_path = str(working.resolve())
    bridge.fail_absolute_children = True

    _notebook, lease = wrapper.open_working_notebook(
        "__ISOLATED__",
        working,
        template_paths=(template,),
    )

    assert lease["opened_hierarchy"][0]["relative_path"] == "Root.one"
    evidence = read_json(wrapper.run_dir / "materialized-hierarchy-open.json")
    assert evidence["attempts"][0]["path_mode"] == "absolute"
    assert evidence["attempts"][0]["bridge_error_type"] == "RuntimeError"
    assert evidence["attempts"][1]["path_mode"] == "parent_relative"
    assert evidence["attempts"][1]["activated"] is True


def test_open_working_copy_accepts_exact_object_and_parent_when_global_snapshot_lags(
    tmp_path,
) -> None:
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    working.mkdir(parents=True)
    template.mkdir(parents=True)
    (working / "Root.one").write_bytes(b"root")
    bridge.reported_path = str(working.resolve())
    bridge.exact_child_hierarchy = True
    hierarchy.hide_children_from_global = True

    _notebook, lease = wrapper.open_working_notebook(
        "__ISOLATED__",
        working,
        template_paths=(template,),
    )

    assert lease["opened_hierarchy"] == [
        {
            "relative_path": "Root.one",
            "resource_type": "section",
            "object_id": "notebook-id::Root.one",
        }
    ]
    attempt = read_json(wrapper.materialized_evidence_path)["attempts"][0]
    assert attempt["global_snapshot_visible"] is False
    assert attempt["observed_parent_id"] == "notebook-id"
    assert attempt["exact_object_probe"] == "passed"
    assert attempt["activation_proof"] == "exact_object_and_parent"
    exact_calls = [
        kwargs
        for name, kwargs in bridge.calls
        if name == "get_hierarchy" and kwargs.get("start_id") != "notebook-id"
    ]
    assert exact_calls[0]["start_id"] == "notebook-id::Root.one"


def test_open_working_copy_rechecks_global_snapshot_when_exact_self_is_not_ready(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "tests.manual_validation.lifecycle.MATERIALIZED_HIERARCHY_DELAY_SECONDS",
        0,
    )
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    working.mkdir(parents=True)
    template.mkdir(parents=True)
    (working / "Root.one").write_bytes(b"root")
    bridge.reported_path = str(working.resolve())
    bridge.exact_child_failures = 2
    hierarchy.hidden_child_observations = 2

    _notebook, lease = wrapper.open_working_notebook(
        "__ISOLATED__",
        working,
        template_paths=(template,),
    )

    assert lease["opened_hierarchy"][0]["object_id"] == "notebook-id::Root.one"
    attempt = read_json(wrapper.materialized_evidence_path)["attempts"][0]
    assert attempt["global_snapshot_visible"] is False
    assert attempt["exact_object_probe"] == "failed"
    assert attempt["exact_object_probe_error"] == "OneNoteBridgeError"
    assert attempt["exact_object_probe_hresult"] == "0x80131501"
    assert attempt["global_snapshot_retry_attempts"] == 2
    assert attempt["global_snapshot_retry_visible"] is True
    assert attempt["activation_proof"] == "global_snapshot_retry"


def test_open_working_copy_fails_closed_when_neither_activation_proof_converges(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "tests.manual_validation.lifecycle.MATERIALIZED_HIERARCHY_DELAY_SECONDS",
        0,
    )
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    working.mkdir(parents=True)
    template.mkdir(parents=True)
    (working / "Root.one").write_bytes(b"root")
    bridge.reported_path = str(working.resolve())
    bridge.exact_child_failures = 16
    hierarchy.hide_children_from_global = True

    with pytest.raises(RunnerFailure, match="did not become active"):
        wrapper.open_working_notebook(
            "__ISOLATED__",
            working,
            template_paths=(template,),
        )

    attempts = read_json(wrapper.materialized_evidence_path)["attempts"]
    assert len(attempts) == 2
    assert all(attempt["exact_object_probe_attempts"] == 8 for attempt in attempts)
    assert all(attempt["global_snapshot_retry_attempts"] == 8 for attempt in attempts)
    assert all(attempt["global_snapshot_retry_visible"] is False for attempt in attempts)
    assert all(attempt["exact_object_probe_hresult"] == "0x80131501" for attempt in attempts)


def test_open_working_copy_does_not_close_reopen_on_activation_failure(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "tests.manual_validation.lifecycle.MATERIALIZED_HIERARCHY_DELAY_SECONDS",
        0,
    )
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    working.mkdir(parents=True)
    template.mkdir(parents=True)
    (working / "Root.one").write_bytes(b"root")
    bridge.reported_path = str(working.resolve())
    bridge.exact_child_failures = 16
    bridge.exact_child_failure_hresult = 0x8004201D
    hierarchy.hide_children_from_global = True

    with pytest.raises(RunnerFailure, match="did not become active"):
        wrapper.open_working_notebook(
            "__ISOLATED__",
            working,
            template_paths=(template,),
        )

    assert [name for name, _kwargs in bridge.calls].count("close_notebook") == 0
    assert not (wrapper.run_dir / "materialized-activation-recovery.json").exists()
    lease = read_json(wrapper.lease_path)
    assert lease["state"] == "active"
    assert lease["hierarchy_open_status"] == "failed"


def test_open_working_copy_rejects_exact_parent_when_object_type_is_wrong(tmp_path) -> None:
    wrapper, bridge, hierarchy = _wrapper(tmp_path)
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    working.mkdir(parents=True)
    template.mkdir(parents=True)
    (working / "Root.one").write_bytes(b"root")
    bridge.reported_path = str(working.resolve())
    bridge.exact_child_hierarchy = True
    bridge.exact_child_tag = "SectionGroup"
    hierarchy.hide_children_from_global = True

    with pytest.raises(RunnerFailure, match="did not become active"):
        wrapper.open_working_notebook(
            "__ISOLATED__",
            working,
            template_paths=(template,),
        )

    attempts = read_json(wrapper.materialized_evidence_path)["attempts"]
    assert all(attempt["observed_parent_id"] == "notebook-id" for attempt in attempts)
    assert all(attempt["exact_object_probe"] == "failed" for attempt in attempts)


def test_open_working_copy_wraps_bridge_failure_and_preserves_diagnostics(tmp_path) -> None:
    wrapper, bridge, _hierarchy = _wrapper(tmp_path)
    working = wrapper.notebook_root / "source-working-copy"
    template = tmp_path / "cache" / "template-notebook"
    working.mkdir(parents=True)
    template.mkdir(parents=True)
    (working / "Root.one").write_bytes(b"root")
    bridge.reported_path = str(working.resolve())
    bridge.fail_all_children = True

    with pytest.raises(RunnerFailure, match="did not become active"):
        wrapper.open_working_notebook(
            "__ISOLATED__",
            working,
            template_paths=(template,),
        )

    evidence = read_json(wrapper.run_dir / "materialized-hierarchy-open.json")
    assert evidence["status"] == "failed"
    assert len(evidence["attempts"]) == 2
    lease = read_json(wrapper.lease_path)
    assert lease["notebook_id"] == "notebook-id"
    assert lease["hierarchy_open_status"] == "failed"
    assert lease["state"] == "active"


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
    hierarchy.list_notebooks = lambda **_kwargs: {
        "notebooks": [
            {"id": "notebook-one", "is_open": True},
            {"id": "notebook-two", "is_open": True},
        ]
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
    assert bridge.calls[-1] == (
        "close_notebook",
        {"notebook_id": "notebook-id", "force": False},
    )
    assert source.exists()
    assert (source / "Source.one").exists()
    assert read_json(wrapper.lease_path)["state"] == "closed"


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
