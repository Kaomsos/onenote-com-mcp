"""Pure contracts for the narrow source-Notebook lifecycle wrapper."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.manual_validation.lifecycle import NotebookLifecycleWrapper
from tests.manual_validation.runtime import RestoreFailure
from tests.manual_validation.test_utils import read_json, write_json


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.hierarchy = None

    def call(self, name: str, **kwargs):
        self.calls.append((name, kwargs))
        if name == "open_hierarchy":
            Path(kwargs["path"]).mkdir(parents=True)
            return {"object_id": "notebook-id"}
        if name == "close_notebook":
            self.hierarchy.closed = True
            return {"ok": True}
        raise AssertionError(name)


class FakeHierarchy:
    def __init__(self, name: str = "__ISOLATED__") -> None:
        self.name = name
        self.closed = False
        self.created = False

    def list_notebooks(self, include_recycle_bin: bool = False):
        return {"notebooks": [] if not self.created else [self._item()]}

    def wait_for_created(self, _path: str, _type: str, _fallback_id: str):
        self.created = True
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
    wrapper = NotebookLifecycleWrapper(tmp_path / "run", timeout_seconds=10, bridge=bridge)
    wrapper._hierarchy = hierarchy
    return wrapper, bridge, hierarchy


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
    assert [name for name, _kwargs in bridge.calls] == ["open_hierarchy"]


def test_wrapper_exposes_only_three_public_operations() -> None:
    operations = {
        name
        for name, value in vars(NotebookLifecycleWrapper).items()
        if callable(value) and not name.startswith("_")
    }
    assert operations == {
        "create_fresh_notebook",
        "get_exact_notebook",
        "close_exact_notebook",
    }
